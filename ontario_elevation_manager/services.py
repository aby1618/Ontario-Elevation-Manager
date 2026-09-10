import html
import json
import os
import re
import shutil
import urllib.parse
import urllib.request
import zipfile
from contextlib import suppress
from collections import defaultdict

from .datasets import RASTER_DATASETS

USER_AGENT = "QGIS Ontario Elevation Manager/1.0.3"
_DSM_PACKAGE_CATALOG = None

def _validate_http_url(url):
    """Return a validated public HTTP(S) URL for urllib operations."""
    value = str(url or "").strip()
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Only HTTP(S) URLs are permitted for remote Ontario data access.")
    return value



def _read_json(url, timeout=30):
    safe_url = _validate_http_url(url)
    request = urllib.request.Request(
        safe_url,
        headers={"User-Agent": USER_AGENT},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec B310 - URL scheme validated above
        return json.loads(response.read().decode("utf-8"))


def _read_text(url, timeout=60):
    safe_url = _validate_http_url(url)
    request = urllib.request.Request(
        safe_url,
        headers={"User-Agent": USER_AGENT},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec B310 - URL scheme validated above
        return response.read().decode("utf-8", errors="replace")


def query_tiles_by_envelope(rect, dataset_key="lidar_dtm"):
    """Query a live ArcGIS tile index. Currently used by the DTM adapter."""
    dataset = RASTER_DATASETS[dataset_key]
    service_url = dataset.get("tile_service_url")
    if not service_url:
        raise RuntimeError(
            f"Dataset '{dataset['name']}' does not use a live tile-index service."
        )

    geometry_string = (
        f"{rect.xMinimum()},"
        f"{rect.yMinimum()},"
        f"{rect.xMaximum()},"
        f"{rect.yMaximum()}"
    )

    parameters = {
        "where": "1=1",
        "geometry": geometry_string,
        "geometryType": "esriGeometryEnvelope",
        "inSR": "3857",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "TileName,FileName,Project,Package",
        "returnGeometry": "true",
        "outSR": "3857",
        "f": "json",
    }

    url = service_url + "/query?" + urllib.parse.urlencode(parameters)
    data = _read_json(url)

    if "error" in data:
        raise RuntimeError(f"Ontario tile service error: {data['error']}")

    return data.get("features", []), bool(data.get("exceededTransferLimit"))


def ensure_local_tile_index(dataset_key, cache_root, force=False):
    """Download/extract an official zipped shapefile tile index when required."""
    dataset = RASTER_DATASETS[dataset_key]
    zip_url = dataset.get("index_zip_url")

    if not zip_url:
        raise RuntimeError(
            f"Dataset '{dataset['name']}' does not define a downloadable tile index."
        )

    if not cache_root:
        raise RuntimeError("Choose a cache folder before downloading the DSM index.")

    index_dir = os.path.join(cache_root, "index")
    archive_path = os.path.join(index_dir, "tile_index.zip")
    partial_path = archive_path + ".part"

    if force and os.path.isdir(index_dir):
        shutil.rmtree(index_dir, ignore_errors=True)

    os.makedirs(index_dir, exist_ok=True)

    def find_shp():
        candidates = []
        for root, _, files in os.walk(index_dir):
            for name in files:
                if name.lower().endswith(".shp"):
                    candidates.append(os.path.join(root, name))
        if not candidates:
            return None
        preferred = [
            path for path in candidates
            if "tileindex" in os.path.basename(path).lower()
            or "tile_index" in os.path.basename(path).lower()
        ]
        return (preferred or candidates)[0]

    existing = find_shp()
    if existing and not force:
        return existing

    safe_zip_url = _validate_http_url(zip_url)
    request = urllib.request.Request(
        safe_zip_url,
        headers={"User-Agent": USER_AGENT},
    )

    with urllib.request.urlopen(request, timeout=90) as response:  # nosec B310 - URL scheme validated above
        with open(partial_path, "wb") as output:
            while True:
                chunk = response.read(4 * 1024 * 1024)
                if not chunk:
                    break
                output.write(chunk)

    if not zipfile.is_zipfile(partial_path):
        with suppress(OSError):
            os.remove(partial_path)
        raise RuntimeError("Downloaded Ontario DSM tile index is not a valid ZIP file.")

    os.replace(partial_path, archive_path)

    with zipfile.ZipFile(archive_path, "r") as archive:
        # Flatten the shapefile components into the index directory. This avoids
        # depending on any folder structure used inside Ontario's ZIP.
        for member in archive.infolist():
            if member.is_dir():
                continue
            basename = os.path.basename(member.filename)
            if not basename:
                continue
            destination = os.path.join(index_dir, basename)
            with archive.open(member, "r") as source, open(destination, "wb") as target:
                shutil.copyfileobj(source, target)

    shp = find_shp()
    if not shp:
        raise RuntimeError(
            "Ontario DSM tile-index ZIP downloaded successfully, but no shapefile was found."
        )

    return shp


def extract_download_url(value):
    if not value:
        return None

    value = html.unescape(str(value))

    match = re.search(
        r'href=["\']([^"\']+)["\']',
        value,
        re.IGNORECASE,
    )

    if match:
        return match.group(1)

    if value.lower().startswith(("http://", "https://")):
        return value

    return None


def get_remote_info(url):
    try:
        safe_url = _validate_http_url(url)
        request = urllib.request.Request(
            safe_url,
            method="HEAD",
            headers={"User-Agent": USER_AGENT},
        )

        with urllib.request.urlopen(request, timeout=30) as response:  # nosec B310 - URL scheme validated above
            length = response.headers.get("Content-Length")
            return {
                "accessible": 200 <= int(response.status) < 400,
                "size": int(length) if length else None,
                "content_type": response.headers.get("Content-Type"),
                "accept_ranges": response.headers.get("Accept-Ranges"),
            }

    except Exception:
        return {
            "accessible": False,
            "size": None,
            "content_type": None,
            "accept_ranges": None,
        }


def get_remote_size(url):
    return get_remote_info(url).get("size")


def _normalize_package(value):
    value = os.path.splitext(os.path.basename(str(value or "")))[0]
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _dsm_package_catalog(dataset):
    global _DSM_PACKAGE_CATALOG

    if _DSM_PACKAGE_CATALOG is not None:
        return _DSM_PACKAGE_CATALOG

    catalog = {}
    metadata_url = dataset.get("metadata_url")
    if not metadata_url:
        _DSM_PACKAGE_CATALOG = catalog
        return catalog

    try:
        text = html.unescape(_read_text(metadata_url))
        urls = re.findall(
            r"https://ws\.gisetl\.lrc\.gov\.on\.ca/"
            r"fmedatadownload/Packages/[^\s<\"']+?\.zip",
            text,
            flags=re.IGNORECASE,
        )
        for url in urls:
            clean = url.rstrip(".,;)")
            stem = os.path.splitext(
                os.path.basename(urllib.parse.urlparse(clean).path)
            )[0]
            catalog[_normalize_package(stem)] = clean
    except Exception:
        catalog = {}

    _DSM_PACKAGE_CATALOG = catalog
    return catalog


def _resolve_stem_package_url(dataset, package_name):
    stem = os.path.splitext(str(package_name).strip())[0]
    base_url = dataset.get("package_base_url") or ""
    direct = base_url + urllib.parse.quote(stem, safe="-_.()") + ".zip"

    info = get_remote_info(direct)
    if info["accessible"]:
        return direct, info

    catalog = _dsm_package_catalog(dataset)
    fallback = catalog.get(_normalize_package(stem))
    if fallback:
        return fallback, get_remote_info(fallback)

    # The DSM tile-index Package field is intended to identify the archive
    # stem (e.g. Lake-Erie-DSM-29). Keep the deterministic URL as a final
    # fallback so a transient HEAD failure does not block a valid package.
    return direct, info


def resolve_package_jobs(tile_records, dataset_key="lidar_dtm"):
    dataset = RASTER_DATASETS[dataset_key]
    groups = defaultdict(list)

    for tile in tile_records:
        key = (tile["Project"], tile["Package"])
        groups[key].append(tile["FileName"])

    jobs = []

    for (project_name, package_name), filenames in groups.items():
        if dataset.get("package_mode") == "feature_service":
            project_sql = str(project_name).replace("'", "''")
            package_sql = str(package_name).replace("'", "''")

            where_clause = (
                f"Project = '{project_sql}' "
                f"AND Package = '{package_sql}'"
            )

            parameters = {
                "where": where_clause,
                "outFields": (
                    "Package,Project,Resolution,Vintage,"
                    "VerticalDatum,Size_GB,DownloadLink"
                ),
                "returnGeometry": "false",
                "f": "json",
            }

            service_url = dataset["package_service_url"]
            url = service_url + "/query?" + urllib.parse.urlencode(parameters)
            data = _read_json(url)

            if "error" in data:
                raise RuntimeError(f"Ontario package service error: {data['error']}")

            matches = data.get("features", [])
            if not matches:
                raise RuntimeError(
                    f"No package-index match for {project_name} / {package_name}"
                )

            attributes = matches[0].get("attributes", {})
            download_url = extract_download_url(attributes.get("DownloadLink"))

            if not download_url:
                raise RuntimeError(
                    f"Could not resolve a download URL for package '{package_name}'."
                )

            remote_info = get_remote_info(download_url)
            listed_size_gb = attributes.get("Size_GB")
            resolution = attributes.get("Resolution")
            vintage = attributes.get("Vintage")
            vertical_datum = attributes.get("VerticalDatum")

        elif dataset.get("package_mode") == "package_stem":
            download_url, remote_info = _resolve_stem_package_url(
                dataset,
                package_name,
            )
            listed_size_gb = (
                (remote_info.get("size") or 0) / 1_000_000_000.0
                if remote_info.get("size")
                else None
            )
            resolution = None
            vintage = None
            vertical_datum = None

        else:
            raise RuntimeError(
                f"Unsupported package-resolution mode for {dataset['name']}."
            )

        archive_name = os.path.basename(
            urllib.parse.urlparse(download_url).path
        )
        if not archive_name:
            archive_name = re.sub(r'[<>:"/\\|?*]+', "_", package_name) + ".zip"

        jobs.append(
            {
                "project": project_name,
                "package": package_name,
                "files": list(filenames),
                "download_url": download_url,
                "listed_size_gb": listed_size_gb,
                "resolution": resolution,
                "vintage": vintage,
                "vertical_datum": vertical_datum,
                "remote_size": remote_info.get("size"),
                "archive_name": archive_name,
                "dataset_key": dataset_key,
            }
        )

    return jobs


def get_package_metadata(project_name, package_name, dataset_key="lidar_dtm"):
    dataset = RASTER_DATASETS[dataset_key]

    if dataset.get("package_mode") == "feature_service":
        project_sql = str(project_name).replace("'", "''")
        package_sql = str(package_name).replace("'", "''")

        where_clause = (
            f"Project = '{project_sql}' "
            f"AND Package = '{package_sql}'"
        )

        parameters = {
            "where": where_clause,
            "outFields": (
                "Package,Project,Resolution,Vintage,"
                "VerticalDatum,Size_GB,DownloadLink"
            ),
            "returnGeometry": "false",
            "f": "json",
        }

        service_url = dataset["package_service_url"]
        url = service_url + "/query?" + urllib.parse.urlencode(parameters)
        data = _read_json(url)

        if "error" in data:
            raise RuntimeError(f"Ontario package service error: {data['error']}")

        matches = data.get("features", [])
        if not matches:
            return None

        attributes = dict(matches[0].get("attributes", {}))
        attributes["DownloadURL"] = extract_download_url(
            attributes.get("DownloadLink")
        )
        return attributes

    download_url, remote_info = _resolve_stem_package_url(
        dataset,
        package_name,
    )
    size = remote_info.get("size")
    return {
        "Package": package_name,
        "Project": project_name,
        "Resolution": None,
        "Vintage": None,
        "VerticalDatum": None,
        "Size_GB": (size / 1_000_000_000.0) if size else None,
        "DownloadLink": download_url,
        "DownloadURL": download_url,
    }
