from contextlib import suppress
import json
import os
from datetime import datetime, timezone

from osgeo import gdal
from qgis.core import QgsTask


EXPORT_NODATA = -9999.0


def manifest_path_for(output_path):
    return output_path + ".ontario_elevation.json"


def load_export_manifest(output_path):
    candidates = [
        manifest_path_for(output_path),
        output_path + ".ontario_dtm.json",  # legacy v0.7 compatibility
    ]

    for path in candidates:
        if not os.path.exists(path):
            continue
        with suppress(OSError, UnicodeError, json.JSONDecodeError):
            with open(path, "r", encoding="utf-8") as source:
                return json.load(source)

    return None


def export_is_current(output_path, source_vrt_path):
    if not output_path or not os.path.exists(output_path):
        return False, "Not exported"

    manifest = load_export_manifest(output_path)
    if not manifest:
        return False, "Existing raster found; no plugin manifest"

    if not source_vrt_path or not os.path.exists(source_vrt_path):
        return False, "Source VRT not found"

    source_norm = os.path.normcase(os.path.abspath(source_vrt_path))
    manifest_source = os.path.normcase(
        os.path.abspath(str(manifest.get("source_vrt", "")))
    )

    if source_norm != manifest_source:
        return False, "Existing export references a different working VRT"

    try:
        current_mtime = os.path.getmtime(source_vrt_path)
        saved_mtime = float(manifest.get("source_vrt_mtime", -1))
    except Exception:
        return False, "Could not compare source VRT timestamps"

    if abs(current_mtime - saved_mtime) > 1e-6:
        return False, "Out of date — working VRT has changed"

    return True, "Current"


def inspect_raster(vrt_path, approximate_range=True):
    if not vrt_path or not os.path.exists(vrt_path):
        raise RuntimeError("Working terrain VRT was not found.")

    gdal.UseExceptions()
    dataset = gdal.Open(vrt_path, gdal.GA_ReadOnly)
    if dataset is None:
        raise RuntimeError("GDAL could not open the working terrain VRT.")

    band = dataset.GetRasterBand(1)
    transform = dataset.GetGeoTransform()
    projection = dataset.GetProjection() or ""

    minimum = None
    maximum = None
    if approximate_range and band is not None:
        try:
            minimum, maximum = band.ComputeRasterMinMax(True)
        except Exception:
            minimum = None
            maximum = None

    nodata = band.GetNoDataValue() if band is not None else None
    file_list = dataset.GetFileList() or []
    source_imgs = [
        os.path.normpath(path)
        for path in file_list
        if str(path).lower().endswith((".img", ".tif", ".tiff"))
    ]

    result = {
        "width": dataset.RasterXSize,
        "height": dataset.RasterYSize,
        "pixel_x": abs(transform[1]),
        "pixel_y": abs(transform[5]),
        "projection_wkt": projection,
        "nodata": nodata,
        "minimum": minimum,
        "maximum": maximum,
        "source_imgs": source_imgs,
        "tile_count": len(source_imgs),
    }
    dataset = None
    return result


class TerrainExportTask(QgsTask):
    """Background export of the working VRT to GTiff or COG."""

    def __init__(
        self,
        source_vrt_path,
        output_path,
        output_format,
        dst_srs_wkt,
        resolution,
        clip_wkt,
        clip_srs_wkt,
        manifest_data,
        completion_callback,
    ):
        super().__init__(
            "Ontario Elevation - Export Terrain Deliverable",
            QgsTask.Flag.CanCancel,
        )

        self.source_vrt_path = source_vrt_path
        self.output_path = output_path
        self.output_format = output_format
        self.dst_srs_wkt = dst_srs_wkt or None
        self.resolution = resolution
        self.clip_wkt = clip_wkt or None
        self.clip_srs_wkt = clip_srs_wkt or None
        self.manifest_data = dict(manifest_data or {})
        self.completion_callback = completion_callback
        self.error_message = None

        base, extension = os.path.splitext(self.output_path)
        if not extension:
            extension = ".tif"
        self.partial_path = base + ".exporting" + extension
        self.intermediate_path = base + ".warp_tmp.tif"

    def _cleanup_temporary(self):
        for path in (
            self.partial_path,
            self.intermediate_path,
        ):
            if path and os.path.exists(path):
                with suppress(OSError):
                    os.remove(path)

    def _callback(self, start, span):
        def callback(complete, message, user_data):
            if self.isCanceled():
                return 0
            self.setProgress(start + (float(complete) * span))
            return 1
        return callback

    def _warp_options(self, callback, output_path):
        kwargs = {
            "format": "GTiff",
            "resampleAlg": "bilinear",
            "srcNodata": EXPORT_NODATA,
            "dstNodata": EXPORT_NODATA,
            "multithread": True,
            "creationOptions": [
                "TILED=YES",
                "COMPRESS=DEFLATE",
                "BIGTIFF=IF_SAFER",
            ],
            "callback": callback,
        }

        if self.dst_srs_wkt:
            kwargs["dstSRS"] = self.dst_srs_wkt

        if self.resolution is not None:
            kwargs["xRes"] = float(self.resolution)
            kwargs["yRes"] = float(self.resolution)

        if self.clip_wkt:
            kwargs["cutlineWKT"] = self.clip_wkt
            kwargs["cutlineSRS"] = self.clip_srs_wkt
            kwargs["cropToCutline"] = True
            # Include all edge pixels touched by the AOI boundary.
            kwargs["warpOptions"] = ["CUTLINE_ALL_TOUCHED=TRUE"]

        return gdal.WarpOptions(**kwargs)

    def _write_manifest(self):
        data = dict(self.manifest_data)
        data.update(
            {
                "source_vrt": os.path.normpath(self.source_vrt_path),
                "source_vrt_mtime": os.path.getmtime(self.source_vrt_path),
                "output_path": os.path.normpath(self.output_path),
                "output_format": self.output_format,
                "created_utc": datetime.now(timezone.utc).isoformat(),
            }
        )

        manifest_path = manifest_path_for(self.output_path)
        temporary_manifest = manifest_path + ".tmp"
        with open(temporary_manifest, "w", encoding="utf-8") as output:
            json.dump(data, output, indent=2)
        os.replace(temporary_manifest, manifest_path)

    def run(self):
        try:
            gdal.UseExceptions()
            self._cleanup_temporary()

            if not os.path.exists(self.source_vrt_path):
                raise RuntimeError("Working terrain VRT does not exist.")

            output_folder = os.path.dirname(self.output_path)
            if output_folder:
                os.makedirs(output_folder, exist_ok=True)

            if self.output_format == "COG":
                warp_options = self._warp_options(
                    self._callback(0.0, 75.0),
                    self.intermediate_path,
                )
                dataset = gdal.Warp(
                    self.intermediate_path,
                    self.source_vrt_path,
                    options=warp_options,
                )
                if dataset is None:
                    raise RuntimeError("GDAL failed while preparing the COG export.")
                dataset.FlushCache()
                dataset = None

                if self.isCanceled():
                    self._cleanup_temporary()
                    return False

                translate_options = gdal.TranslateOptions(
                    format="COG",
                    creationOptions=[
                        "COMPRESS=DEFLATE",
                        "BIGTIFF=IF_SAFER",
                        "BLOCKSIZE=512",
                        "OVERVIEWS=AUTO",
                    ],
                    callback=self._callback(75.0, 25.0),
                )
                dataset = gdal.Translate(
                    self.partial_path,
                    self.intermediate_path,
                    options=translate_options,
                )
                if dataset is None:
                    raise RuntimeError("GDAL failed while creating the COG.")
                dataset.FlushCache()
                dataset = None

                with suppress(OSError):
                    os.remove(self.intermediate_path)

            else:
                warp_options = self._warp_options(
                    self._callback(0.0, 100.0),
                    self.partial_path,
                )
                dataset = gdal.Warp(
                    self.partial_path,
                    self.source_vrt_path,
                    options=warp_options,
                )
                if dataset is None:
                    raise RuntimeError("GDAL failed while creating the GeoTIFF.")
                dataset.FlushCache()
                dataset = None

            if self.isCanceled():
                self._cleanup_temporary()
                return False

            check = gdal.Open(self.partial_path, gdal.GA_ReadOnly)
            if check is None or check.RasterCount < 1:
                if check is not None:
                    check = None
                raise RuntimeError("The exported terrain could not be reopened for validation.")
            check = None

            # Leave the validated new raster at the temporary path. The QGIS
            # main thread commits it after any loaded prior export layer has
            # been removed, which avoids Windows file-lock issues.
            self.setProgress(100.0)
            return True

        except Exception as exc:
            self.error_message = str(exc)
            self._cleanup_temporary()
            return False

    def commit_output(self):
        if not os.path.exists(self.partial_path):
            raise RuntimeError(
                "Validated temporary export was not found for final commit."
            )

        os.replace(self.partial_path, self.output_path)
        self._write_manifest()

    def cleanup_after_failure(self):
        self._cleanup_temporary()

    def finished(self, result):
        if self.completion_callback:
            self.completion_callback(self, result)
