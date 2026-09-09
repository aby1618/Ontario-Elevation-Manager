import os
import shutil
import urllib.request
import zipfile

from osgeo import gdal, osr
from qgis.core import QgsTask

from .services import USER_AGENT

CHUNK_SIZE = 4 * 1024 * 1024
OUTPUT_NODATA = -9999.0


def validate_source_rasters(raster_paths):
    if not raster_paths:
        raise RuntimeError("No source rasters were supplied.")

    base_srs = None
    base_pixel = None

    for path in raster_paths:
        dataset = gdal.Open(path, gdal.GA_ReadOnly)

        if dataset is None:
            raise RuntimeError(f"Could not open source raster: {path}")

        if dataset.RasterCount < 1:
            dataset = None
            raise RuntimeError(f"Source raster has no bands: {path}")

        projection = dataset.GetProjection() or ""
        transform = dataset.GetGeoTransform()
        pixel = (abs(transform[1]), abs(transform[5]))

        if base_pixel is None:
            base_pixel = pixel
            base_srs = osr.SpatialReference()
            if projection:
                base_srs.ImportFromWkt(projection)

        else:
            if (
                abs(pixel[0] - base_pixel[0]) > 1e-9
                or abs(pixel[1] - base_pixel[1]) > 1e-9
            ):
                dataset = None
                raise RuntimeError(
                    "Selected Ontario elevation tiles have inconsistent pixel sizes."
                )

            if projection and base_srs is not None:
                this_srs = osr.SpatialReference()
                this_srs.ImportFromWkt(projection)

                if not bool(base_srs.IsSame(this_srs)):
                    dataset = None
                    raise RuntimeError(
                        "Selected Ontario elevation tiles have inconsistent coordinate systems."
                    )

        dataset = None

    return base_pixel


def build_vrt(vrt_path, raster_paths):
    gdal.UseExceptions()
    validate_source_rasters(raster_paths)

    output_folder = os.path.dirname(vrt_path)
    if output_folder:
        os.makedirs(output_folder, exist_ok=True)

    # Remove old VRT and stale QGIS/GDAL statistics metadata.
    for stale_path in (vrt_path, vrt_path + ".aux.xml"):
        if os.path.exists(stale_path):
            try:
                os.remove(stale_path)
            except Exception as exc:
                raise RuntimeError(
                    f"Could not replace existing VRT metadata:\n"
                    f"{stale_path}\n{exc}"
                )

    options = gdal.BuildVRTOptions(
        resolution="highest",
        resampleAlg="nearest",
        VRTNodata=OUTPUT_NODATA,
    )

    dataset = gdal.BuildVRT(
        vrt_path,
        raster_paths,
        options=options,
    )

    if dataset is None:
        raise RuntimeError("GDAL did not create the VRT.")

    dataset.FlushCache()
    dataset = None

    if not os.path.exists(vrt_path):
        raise RuntimeError("VRT file was not created.")


class OntarioDTMDownloadTask(QgsTask):
    def __init__(
        self,
        jobs,
        packages_directory,
        tiles_directory,
        selected_filenames,
        vrt_path,
        keep_zips,
        completion_callback,
        dataset_short_name="DTM",
    ):
        self.dataset_short_name = str(dataset_short_name or "Elevation")
        super().__init__(
            f"Ontario {self.dataset_short_name} - Download, Extract and Build Terrain",
            QgsTask.CanCancel,
        )

        self.jobs = jobs
        self.packages_directory = packages_directory
        self.tiles_directory = tiles_directory
        self.selected_filenames = selected_filenames
        self.vrt_path = vrt_path
        self.keep_zips = keep_zips
        self.completion_callback = completion_callback

        self.error_message = None
        self.extracted_main_files = []
        self.reused_main_files = []
        self.failed_files = []

        self.total_remote_bytes = sum(
            job["remote_size"] or 0
            for job in jobs
        )
        self.network_done = 0

    def _update_download_progress(self):
        if self.total_remote_bytes > 0:
            fraction = min(
                1.0,
                self.network_done / self.total_remote_bytes,
            )
            self.setProgress(fraction * 85.0)

    def _update_extraction_progress(self, completed, total):
        if total <= 0:
            self.setProgress(95.0)
            return

        self.setProgress(
            85.0 + ((completed / total) * 10.0)
        )

    def _download_package(self, job):
        final_path = os.path.join(
            self.packages_directory,
            job["archive_name"],
        )
        partial_path = final_path + ".part"

        if os.path.exists(final_path):
            if zipfile.is_zipfile(final_path):
                if job["remote_size"]:
                    self.network_done += job["remote_size"]
                    self._update_download_progress()
                return final_path

            os.remove(final_path)

        existing_size = 0
        if os.path.exists(partial_path):
            existing_size = os.path.getsize(partial_path)

        headers = {"User-Agent": USER_AGENT}

        if existing_size > 0:
            headers["Range"] = f"bytes={existing_size}-"

        request = urllib.request.Request(
            job["download_url"],
            headers=headers,
        )
        response = urllib.request.urlopen(
            request,
            timeout=60,
        )

        status = response.getcode()

        # If Range was ignored, restart rather than appending duplicate bytes.
        if existing_size > 0 and status != 206:
            response.close()
            existing_size = 0

            try:
                os.remove(partial_path)
            except Exception:
                pass

            request = urllib.request.Request(
                job["download_url"],
                headers={"User-Agent": USER_AGENT},
            )
            response = urllib.request.urlopen(
                request,
                timeout=60,
            )

        mode = "ab" if existing_size > 0 else "wb"
        self.network_done += existing_size
        self._update_download_progress()

        with open(partial_path, mode) as output_file:
            while True:
                if self.isCanceled():
                    response.close()
                    return None

                chunk = response.read(CHUNK_SIZE)

                if not chunk:
                    break

                output_file.write(chunk)
                self.network_done += len(chunk)
                self._update_download_progress()

        response.close()

        if job["remote_size"]:
            actual_size = os.path.getsize(partial_path)

            if actual_size != job["remote_size"]:
                raise RuntimeError(
                    "Downloaded file size does not match Ontario server. "
                    f"Expected {job['remote_size']} bytes, "
                    f"received {actual_size} bytes."
                )

        if not zipfile.is_zipfile(partial_path):
            raise RuntimeError(
                f"Downloaded package is not a valid ZIP: "
                f"{job['archive_name']}"
            )

        os.replace(partial_path, final_path)
        return final_path

    def _extract_requested_files(
        self,
        archive_path,
        requested_files,
        extraction_index,
        total_extractions,
    ):
        with zipfile.ZipFile(archive_path, "r") as archive:
            members = [
                member
                for member in archive.infolist()
                if not member.is_dir()
            ]

            for target_name in requested_files:
                if self.isCanceled():
                    return False

                main_destination = os.path.join(
                    self.tiles_directory,
                    target_name,
                )

                if os.path.exists(main_destination):
                    self.reused_main_files.append(main_destination)
                    extraction_index += 1
                    self._update_extraction_progress(
                        extraction_index,
                        total_extractions,
                    )
                    continue

                target_lower = target_name.lower()
                target_root = os.path.splitext(
                    target_name
                )[0].lower()

                matching_members = []

                for member in members:
                    basename = os.path.basename(
                        member.filename
                    )
                    basename_lower = basename.lower()

                    if (
                        basename_lower == target_lower
                        or basename_lower.startswith(
                            target_root + "."
                        )
                    ):
                        matching_members.append(member)

                main_member_found = any(
                    os.path.basename(
                        member.filename
                    ).lower()
                    == target_lower
                    for member in matching_members
                )

                if not main_member_found:
                    self.failed_files.append(target_name)
                    extraction_index += 1
                    self._update_extraction_progress(
                        extraction_index,
                        total_extractions,
                    )
                    continue

                for member in matching_members:
                    if self.isCanceled():
                        return False

                    basename = os.path.basename(
                        member.filename
                    )
                    destination = os.path.join(
                        self.tiles_directory,
                        basename,
                    )
                    temporary_destination = (
                        destination + ".extracting"
                    )

                    with archive.open(member, "r") as source:
                        with open(
                            temporary_destination,
                            "wb",
                        ) as destination_file:
                            shutil.copyfileobj(
                                source,
                                destination_file,
                                length=CHUNK_SIZE,
                            )

                    os.replace(
                        temporary_destination,
                        destination,
                    )

                if os.path.exists(main_destination):
                    self.extracted_main_files.append(
                        main_destination
                    )
                else:
                    self.failed_files.append(target_name)

                extraction_index += 1
                self._update_extraction_progress(
                    extraction_index,
                    total_extractions,
                )

        return extraction_index

    def run(self):
        try:
            os.makedirs(
                self.packages_directory,
                exist_ok=True,
            )
            os.makedirs(
                self.tiles_directory,
                exist_ok=True,
            )

            total_extractions = sum(
                len(job["files"])
                for job in self.jobs
            )
            extraction_index = 0

            for job in self.jobs:
                if self.isCanceled():
                    return False

                archive_path = self._download_package(job)

                if archive_path is None:
                    return False

                result = self._extract_requested_files(
                    archive_path,
                    job["files"],
                    extraction_index,
                    total_extractions,
                )

                if result is False:
                    return False

                extraction_index = result

                package_complete = all(
                    os.path.exists(
                        os.path.join(
                            self.tiles_directory,
                            filename,
                        )
                    )
                    for filename in job["files"]
                )

                if package_complete and not self.keep_zips:
                    try:
                        os.remove(archive_path)
                    except Exception:
                        pass

            if self.failed_files:
                raise RuntimeError(
                    "Some requested raster files were not found "
                    "inside the Ontario ZIP: "
                    + ", ".join(self.failed_files)
                )

            raster_paths = [
                os.path.join(
                    self.tiles_directory,
                    filename,
                )
                for filename in self.selected_filenames
            ]

            missing = [
                path
                for path in raster_paths
                if not os.path.exists(path)
            ]

            if missing:
                raise RuntimeError(
                    "Cannot build VRT because selected rasters "
                    "are missing: "
                    + ", ".join(
                        os.path.basename(path)
                        for path in missing
                    )
                )

            if self.isCanceled():
                return False

            self.setProgress(96.0)
            build_vrt(
                self.vrt_path,
                raster_paths,
            )
            self.setProgress(100.0)
            return True

        except Exception as exc:
            self.error_message = str(exc)
            return False

    def finished(self, result):
        if self.completion_callback:
            self.completion_callback(
                self,
                result,
            )
