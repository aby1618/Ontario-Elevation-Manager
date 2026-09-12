import math
import os
import re
import shutil
import urllib.request
import zipfile
from array import array
from contextlib import suppress

from osgeo import gdal, osr
from qgis.core import QgsTask

from .services import USER_AGENT, _validate_http_url

CHUNK_SIZE = 4 * 1024 * 1024
OUTPUT_NODATA = -9999.0


EXTREME_SENTINEL_THRESHOLD = 1.0e20
QA_SAMPLE_SIZE = 64
VRT_QA_SAMPLE_SIZE = 256
# Values outside +/-1e20 are never plausible Ontario elevations.
# The VRT sanitizer clamps them to OUTPUT_NODATA while preserving every
# value in the broad +/-1e10 identity interval exactly.
VRT_EXTREME_CUTOFF = 1.0e20
VRT_IDENTITY_LIMIT = 1.0e10
VRT_SANITIZE_LUT = (
    f"{-VRT_EXTREME_CUTOFF:.17g}:{OUTPUT_NODATA:.17g},"
    f"{-VRT_IDENTITY_LIMIT:.17g}:{-VRT_IDENTITY_LIMIT:.17g},"
    f"{VRT_IDENTITY_LIMIT:.17g}:{VRT_IDENTITY_LIMIT:.17g},"
    f"{VRT_EXTREME_CUTOFF:.17g}:{OUTPUT_NODATA:.17g}"
)


class RasterQAError(RuntimeError):
    """Raised when source or output terrain raster QA fails."""

    def __init__(self, message, problem_files=None):
        super().__init__(message)
        self.problem_files = list(problem_files or [])


def _nearly_equal(value, other):
    if value is None or other is None:
        return False
    scale = max(1.0, abs(float(value)), abs(float(other)))
    return abs(float(value) - float(other)) <= (scale * 1.0e-7)


def _unique_numeric_values(values):
    unique = []
    for value in values:
        if value is None or not math.isfinite(float(value)):
            continue
        numeric = float(value)
        if not any(_nearly_equal(numeric, existing) for existing in unique):
            unique.append(numeric)
    return unique


def _sample_band_values(band, raster_x_size, raster_y_size, sample_size=QA_SAMPLE_SIZE):
    """Read a small, evenly decimated Float64 sample without a NumPy dependency."""
    x_size = max(1, min(int(sample_size), int(raster_x_size)))
    y_size = max(1, min(int(sample_size), int(raster_y_size)))
    raw = band.ReadRaster(
        0,
        0,
        int(raster_x_size),
        int(raster_y_size),
        buf_xsize=x_size,
        buf_ysize=y_size,
        buf_type=gdal.GDT_Float64,
    )
    if not raw:
        return []

    values = array("d")
    values.frombytes(raw)
    return [float(value) for value in values if math.isfinite(float(value))]


def _implicit_extreme_nodata(values, declared_nodata=None):
    """Detect obvious floating-point sentinel values which are not tagged as NoData."""
    candidates = []
    for value in values:
        if declared_nodata is not None and _nearly_equal(value, declared_nodata):
            continue
        if abs(float(value)) >= EXTREME_SENTINEL_THRESHOLD:
            candidates.append(float(value))
    unique = _unique_numeric_values(candidates)
    if len(unique) == 1:
        return unique[0]
    if len(unique) > 1:
        return unique
    return None


def _canonical_path(path):
    return os.path.normcase(os.path.abspath(os.path.normpath(str(path))))


def validate_source_rasters(raster_paths):
    """Validate source compatibility and collect NoData diagnostics.

    The source rasters are deliberately *not* rejected merely because they use
    different extreme Float32 sentinel values. Newer Ontario TIF acquisitions
    can contain both positive and negative extreme sentinels, including within
    the same acquisition. Those values are sanitized generically in the VRT
    itself instead of trying to guess one source NoData value per raster.
    """
    if not raster_paths:
        raise RasterQAError("No source rasters were supplied.")

    base_srs = None
    base_pixel = None
    source_nodata_by_path = {}
    nodata_values = []
    implicit_nodata_files = []
    declared_nodata_files = []
    sampled_valid_values = []
    sampled_extreme_values = []

    for path in raster_paths:
        dataset = gdal.Open(path, gdal.GA_ReadOnly)

        if dataset is None:
            raise RasterQAError(
                f"Could not open source raster: {path}",
                [os.path.basename(path)],
            )

        if dataset.RasterCount < 1:
            dataset = None
            raise RasterQAError(
                f"Source raster has no bands: {path}",
                [os.path.basename(path)],
            )

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
                raise RasterQAError(
                    "Selected Ontario elevation tiles have inconsistent pixel sizes.",
                    [os.path.basename(path)],
                )

            if projection and base_srs is not None:
                this_srs = osr.SpatialReference()
                this_srs.ImportFromWkt(projection)
                if not bool(base_srs.IsSame(this_srs)):
                    dataset = None
                    raise RasterQAError(
                        "Selected Ontario elevation tiles have inconsistent coordinate systems.",
                        [os.path.basename(path)],
                    )

        band = dataset.GetRasterBand(1)
        declared_nodata = band.GetNoDataValue()
        if declared_nodata is not None and math.isfinite(float(declared_nodata)):
            declared_nodata = float(declared_nodata)
            declared_nodata_files.append(os.path.basename(path))
            nodata_values.append(declared_nodata)
        else:
            declared_nodata = None

        source_nodata_by_path[_canonical_path(path)] = declared_nodata

        sample_values = _sample_band_values(
            band,
            dataset.RasterXSize,
            dataset.RasterYSize,
            QA_SAMPLE_SIZE,
        )

        found_extreme = False
        for value in sample_values:
            if declared_nodata is not None and _nearly_equal(value, declared_nodata):
                continue
            if abs(float(value)) >= EXTREME_SENTINEL_THRESHOLD:
                sampled_extreme_values.append(float(value))
                found_extreme = True
                continue
            sampled_valid_values.append(float(value))

        if found_extreme:
            implicit_nodata_files.append(os.path.basename(path))

        dataset = None

    unique_declared = _unique_numeric_values(nodata_values)
    unique_extremes = _unique_numeric_values(sampled_extreme_values)
    all_nodata_values = _unique_numeric_values(unique_declared + unique_extremes)

    return {
        "pixel_size": base_pixel,
        "source_nodata": all_nodata_values[0] if len(all_nodata_values) == 1 else None,
        "source_nodata_values": all_nodata_values,
        "source_nodata_by_path": source_nodata_by_path,
        "implicit_nodata_files": implicit_nodata_files,
        "declared_nodata_files": declared_nodata_files,
        "sample_min": min(sampled_valid_values) if sampled_valid_values else None,
        "sample_max": max(sampled_valid_values) if sampled_valid_values else None,
        "source_count": len(raster_paths),
        "sampled_extreme_values": unique_extremes,
        "sanitizer": "vrt_lut_extreme_clamp",
    }


_VRT_RELEVANT_TAG_RE = re.compile(
    r"<(?P<closing>/)?(?P<tag>VRTRasterBand|SimpleSource|ComplexSource)\b[^>]*>",
    re.IGNORECASE,
)
_VRT_LUT_RE = re.compile(
    r"<LUT\b[^>]*>.*?</LUT>",
    re.IGNORECASE | re.DOTALL,
)


def _vrt_top_level_source_ranges(vrt_text):
    """Return source-element ranges belonging to top-level VRT data bands.

    This intentionally avoids a general-purpose XML parser. The input VRT is
    generated locally by GDAL, and we only need to identify GDAL's own
    VRTRasterBand/SimpleSource/ComplexSource tags. Nested mask-band sources are
    ignored by tracking VRTRasterBand depth.
    """
    band_depth = 0
    active_source = None
    ranges = []

    for match in _VRT_RELEVANT_TAG_RE.finditer(vrt_text):
        tag = match.group("tag")
        tag_lower = tag.lower()
        closing = bool(match.group("closing"))
        token = match.group(0)
        self_closing = token.rstrip().endswith("/>")

        if tag_lower == "vrtrasterband":
            if closing:
                band_depth = max(0, band_depth - 1)
            elif not self_closing:
                band_depth += 1
            continue

        if tag_lower not in ("simplesource", "complexsource"):
            continue

        if not closing:
            if band_depth == 1 and not self_closing:
                active_source = {
                    "tag": tag,
                    "start": match.start(),
                }
            continue

        if (
            active_source is not None
            and band_depth == 1
            and tag_lower == active_source["tag"].lower()
        ):
            ranges.append(
                (
                    int(active_source["start"]),
                    int(match.end()),
                )
            )
            active_source = None

    return ranges


def _patch_vrt_source_block(source_block):
    """Promote one GDAL VRT source to ComplexSource and attach the LUT."""
    opening = re.search(
        r"<(SimpleSource|ComplexSource)\b(?P<attrs>[^>]*)>",
        source_block,
        flags=re.IGNORECASE,
    )
    if opening is None:
        return source_block, False

    original_tag = opening.group(1)
    attrs = opening.group("attrs") or ""

    # ComplexSource is required for LUT support.
    if original_tag.lower() == "simplesource":
        source_block = (
            source_block[: opening.start()]
            + f"<ComplexSource{attrs}>"
            + source_block[opening.end() :]
        )
        source_block = re.sub(
            r"</SimpleSource\s*>",
            "</ComplexSource>",
            source_block,
            count=1,
            flags=re.IGNORECASE,
        )

    # Replace any prior LUT so rebuilds are deterministic.
    source_block = _VRT_LUT_RE.sub(
        "",
        source_block,
    )

    marker = re.search(
        r"(?m)^(?P<indent>[ \t]*)<(SrcRect|DstRect)\b",
        source_block,
    )

    if marker is not None:
        indent = marker.group("indent")
        lut_text = f"{indent}<LUT>{VRT_SANITIZE_LUT}</LUT>\n"
        source_block = (
            source_block[: marker.start()]
            + lut_text
            + source_block[marker.start() :]
        )
    else:
        closing = re.search(
            r"(?m)^(?P<indent>[ \t]*)</ComplexSource\s*>",
            source_block,
            flags=re.IGNORECASE,
        )
        if closing is None:
            return source_block, False
        indent = closing.group("indent") + "  "
        lut_text = f"{indent}<LUT>{VRT_SANITIZE_LUT}</LUT>\n"
        source_block = (
            source_block[: closing.start()]
            + lut_text
            + source_block[closing.start() :]
        )

    return source_block, True


def _patch_vrt_source_nodata(vrt_path, source_qa):
    """Attach the sanitizer LUT to every top-level elevation source.

    No XML parser is used here. The VRT is generated by GDAL in this same
    workflow and is edited only through a narrow tag scanner which recognizes
    the GDAL elements needed for this operation.
    """
    with open(vrt_path, "r", encoding="utf-8") as source:
        vrt_text = source.read()

    source_ranges = _vrt_top_level_source_ranges(vrt_text)
    patched_count = 0

    # Work backwards so string offsets remain stable.
    for start_pos, end_pos in reversed(source_ranges):
        original_block = vrt_text[start_pos:end_pos]
        patched_block, patched = _patch_vrt_source_block(
            original_block
        )
        if not patched:
            continue

        vrt_text = (
            vrt_text[:start_pos]
            + patched_block
            + vrt_text[end_pos:]
        )
        patched_count += 1

    expected_count = int(source_qa.get("source_count") or 0)
    if expected_count and patched_count < expected_count:
        raise RasterQAError(
            "The VRT sanitizer could not be attached to every source raster. "
            "Terrain creation was stopped before commit."
        )

    with open(vrt_path, "w", encoding="utf-8", newline="\n") as output:
        output.write(vrt_text)

    return patched_count


def _validate_vrt_sanitizer_definition(vrt_path):
    """Confirm every top-level elevation source carries the expected LUT."""
    with open(vrt_path, "r", encoding="utf-8") as source:
        vrt_text = source.read()

    source_ranges = _vrt_top_level_source_ranges(vrt_text)
    source_blocks = [
        vrt_text[start_pos:end_pos]
        for start_pos, end_pos in source_ranges
    ]

    unsanitized = []
    for block in source_blocks:
        lut_match = _VRT_LUT_RE.search(block)
        if lut_match is None:
            unsanitized.append(block)
            continue

        lut_body = re.sub(
            r"^<LUT\b[^>]*>|</LUT>$",
            "",
            lut_match.group(0).strip(),
            flags=re.IGNORECASE,
        ).strip()

        if lut_body != VRT_SANITIZE_LUT:
            unsanitized.append(block)

    return len(source_blocks), len(unsanitized)


def _validate_built_vrt(vrt_path, source_qa):
    """Postflight QA and create clean statistics for immediate QGIS display."""
    dataset = gdal.Open(vrt_path, gdal.GA_ReadOnly)
    if dataset is None or dataset.RasterCount < 1:
        raise RasterQAError("The newly created VRT could not be reopened for QA.")

    band = dataset.GetRasterBand(1)
    vrt_nodata = band.GetNoDataValue()
    if vrt_nodata is None or not _nearly_equal(vrt_nodata, OUTPUT_NODATA):
        dataset = None
        raise RasterQAError(
            "The newly created VRT does not expose the required output NoData "
            f"value ({OUTPUT_NODATA:g}). The terrain was not committed."
        )

    # First, verify the actual VRT definition: every top-level elevation source
    # must carry the sanitizer LUT. This deterministic text-level check avoids
    # general-purpose XML parsing and ignores nested mask-band sources.
    data_source_count, unsanitized_count = _validate_vrt_sanitizer_definition(
        vrt_path
    )
    if unsanitized_count:
        dataset = None
        raise RasterQAError(
            "Terrain QA found one or more VRT sources without the required "
            "extreme-value sanitizer. The terrain was not committed."
        )

    values = _sample_band_values(
        band,
        dataset.RasterXSize,
        dataset.RasterYSize,
        VRT_QA_SAMPLE_SIZE,
    )
    valid_values = [
        value
        for value in values
        if not _nearly_equal(value, OUTPUT_NODATA)
    ]
    if any(abs(float(value)) >= EXTREME_SENTINEL_THRESHOLD for value in valid_values):
        dataset = None
        raise RasterQAError(
            "Terrain QA detected an extreme floating-point sentinel after VRT "
            "sanitization. The terrain was not committed."
        )

    # Ask GDAL for statistics after sanitization. Approximate statistics are
    # sufficient for display stretching and avoid a costly full 0.5 m mosaic
    # scan; importantly, the source LUTs make sentinel leakage impossible at
    # read time. These statistics are persisted in PAM where supported, so QGIS
    # opens the new layer with a useful elevation stretch instead of a stale
    # Float32-sentinel minimum.
    stats = None
    try:
        stats = band.ComputeStatistics(True)
    except Exception:
        stats = None

    if stats and len(stats) >= 2:
        vrt_min = float(stats[0])
        vrt_max = float(stats[1])
    else:
        vrt_min = min(valid_values) if valid_values else None
        vrt_max = max(valid_values) if valid_values else None

    if (
        vrt_min is not None
        and vrt_max is not None
        and (
            abs(vrt_min) >= EXTREME_SENTINEL_THRESHOLD
            or abs(vrt_max) >= EXTREME_SENTINEL_THRESHOLD
        )
    ):
        dataset = None
        raise RasterQAError(
            "Terrain statistics still contain an extreme floating-point sentinel. "
            "The terrain was not committed."
        )

    dataset = None

    report = dict(source_qa)
    report.update(
        {
            "output_nodata": OUTPUT_NODATA,
            "vrt_sample_min": vrt_min,
            "vrt_sample_max": vrt_max,
            "qa_passed": True,
            "source_nodata_patched_count": data_source_count,
            "sanitizer": "vrt_lut_extreme_clamp",
        }
    )
    return report

def build_vrt(vrt_path, raster_paths):
    gdal.UseExceptions()
    source_qa = validate_source_rasters(raster_paths)

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

    # Do NOT pass a global srcNodata value here. Ontario source files within a
    # single acquisition can use different NoData sentinels. The VRT XML is
    # patched per source immediately after GDAL creates the mosaic definition.
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

    try:
        patched_count = _patch_vrt_source_nodata(
            vrt_path,
            source_qa,
        )
        source_qa["source_nodata_patched_count"] = patched_count
        return _validate_built_vrt(vrt_path, source_qa)
    except Exception:
        # A QA-failed VRT must never survive to the commit step.
        with suppress(OSError):
            os.remove(vrt_path)
        with suppress(OSError):
            os.remove(vrt_path + ".aux.xml")
        raise


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
            QgsTask.Flag.CanCancel,
        )

        self.jobs = jobs
        self.packages_directory = packages_directory
        self.tiles_directory = tiles_directory
        self.selected_filenames = selected_filenames
        self.vrt_path = vrt_path
        self.keep_zips = keep_zips
        self.completion_callback = completion_callback

        self.error_message = None
        self.vrt_qa_report = None
        self.qa_problem_files = []
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

        download_url = _validate_http_url(job["download_url"])
        request = urllib.request.Request(
            download_url,
            headers=headers,
        )
        response = urllib.request.urlopen(  # nosec B310 - URL scheme validated above
            request,
            timeout=60,
        )

        status = response.getcode()

        # If Range was ignored, restart rather than appending duplicate bytes.
        if existing_size > 0 and status != 206:
            response.close()
            existing_size = 0

            with suppress(OSError):
                os.remove(partial_path)

            request = urllib.request.Request(
                download_url,
                headers={"User-Agent": USER_AGENT},
            )
            response = urllib.request.urlopen(  # nosec B310 - URL scheme validated above
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
                    with suppress(OSError):
                        os.remove(archive_path)

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
            self.vrt_qa_report = build_vrt(
                self.vrt_path,
                raster_paths,
            )
            self.setProgress(100.0)
            return True

        except RasterQAError as exc:
            self.error_message = str(exc)
            self.qa_problem_files = list(exc.problem_files)
            return False
        except Exception as exc:
            self.error_message = str(exc)
            return False

    def finished(self, result):
        if self.completion_callback:
            self.completion_callback(
                self,
                result,
            )
