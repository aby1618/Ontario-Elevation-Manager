import html
import json
import os
import re
import shutil
import urllib.parse
import urllib.error
import urllib.request
import zipfile
from contextlib import suppress
from collections import defaultdict

from .datasets import RASTER_DATASETS

USER_AGENT = "QGIS Ontario Elevation Manager/1.2.0"
_PACKAGE_CATALOG_CACHE = {}

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


def _package_tokens(value):
    """Return comparable package-name tokens across index and archive naming."""
    value = os.path.splitext(os.path.basename(str(value or "")))[0]
    # Split CamelCase as well as punctuation/underscores.
    value = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", value)
    raw_tokens = re.findall(r"[A-Za-z]+|\d+", value.lower())
    aliases = {
        "lk": "lake",
    }
    ignored = {
        "lidar",
        "dtm",
        "dsm",
        "derived",
        "raster",
        "package",
    }
    return [
        aliases.get(token, token)
        for token in raw_tokens
        if token not in ignored
    ]


def _metadata_package_catalog(dataset):
    """Return official package ZIP URLs advertised in a dataset metadata page."""
    metadata_url = dataset.get("metadata_url")
    if not metadata_url:
        return []

    if metadata_url in _PACKAGE_CATALOG_CACHE:
        return _PACKAGE_CATALOG_CACHE[metadata_url]

    catalog = []
    try:
        metadata_text = html.unescape(_read_text(metadata_url))
        urls = re.findall(
            r"https://ws\.gisetl\.lrc\.gov\.on\.ca/"
            r"fmedatadownload/Packages/[^\s<\"']+?\.zip",
            metadata_text,
            flags=re.IGNORECASE,
        )
        seen = set()
        for url in urls:
            clean = url.rstrip(".,;)")
            if clean in seen:
                continue
            seen.add(clean)
            stem = os.path.splitext(
                os.path.basename(urllib.parse.urlparse(clean).path)
            )[0]
            catalog.append(
                {
                    "url": clean,
                    "stem": stem,
                    "normalized": _normalize_package(stem),
                    "tokens": _package_tokens(stem),
                }
            )
    except (OSError, ValueError, urllib.error.URLError, json.JSONDecodeError):
        # Metadata lookup is a fallback only. The primary package resolver may
        # still succeed, and unresolved packages are surfaced to the user.
        catalog = []

    _PACKAGE_CATALOG_CACHE[metadata_url] = catalog
    return catalog


def _dtm_pattern_candidates(dataset, package_name):
    """Generate known Ontario GTA/Halton archive patterns from package labels."""
    if dataset.get("short_name") != "DTM":
        return []

    package = " ".join(str(package_name or "").strip().split())
    base = dataset.get("package_base_url") or ""
    candidates = []

    # Tile/package indexes use labels such as "GTA Halton D", while Ontario's
    # official archive is named GTA-Halton-LidarDTM-D.zip.
    match = re.fullmatch(
        r"GTA\s+(Halton|Milton|Peel|Brampton)\s+([A-Za-z0-9]+)",
        package,
        flags=re.IGNORECASE,
    )
    if match:
        area = match.group(1).title()
        part = match.group(2).upper()
        candidates.append(
            base + f"GTA-{area}-LidarDTM-{part}.zip"
        )

    # Older GTA year packages use GTA2014-LidarDTM-A.zip, etc.
    match = re.fullmatch(
        r"GTA\s+(20\d{2})\s+([A-Za-z0-9]+)",
        package,
        flags=re.IGNORECASE,
    )
    if match:
        year = match.group(1)
        part = match.group(2).upper()
        candidates.append(
            base + f"GTA{year}-LidarDTM-{part}.zip"
        )

    return candidates


def _find_metadata_package_url(dataset, project_name, package_name):
    """Resolve a package URL from official metadata when the index link is blank."""
    # First try deterministic Ontario filename patterns. This avoids downloading
    # the metadata page for common legacy GTA/Halton records.
    for candidate in _dtm_pattern_candidates(dataset, package_name):
        info = get_remote_info(candidate)
        if info.get("accessible"):
            return candidate, info, "derived Ontario archive pattern"

    catalog = _metadata_package_catalog(dataset)
    if not catalog:
        return None, None, None

    target_normalized = _normalize_package(package_name)
    target_tokens = set(_package_tokens(package_name))

    # Exact archive-stem normalization is the safest match.
    for item in catalog:
        if item["normalized"] == target_normalized:
            return item["url"], get_remote_info(item["url"]), "official metadata"

    # Ontario package labels sometimes omit terms embedded in the archive name
    # (e.g. "GTA Halton D" vs "GTA-Halton-LidarDTM-D"). Require every
    # meaningful package token to appear in one unique official archive stem.
    subset_matches = []
    if target_tokens:
        for item in catalog:
            item_tokens = set(item["tokens"])
            if target_tokens.issubset(item_tokens):
                subset_matches.append(item)

    if len(subset_matches) == 1:
        item = subset_matches[0]
        return item["url"], get_remote_info(item["url"]), "official metadata"

    # If several archives share the same package tokens, use project tokens only
    # as a disambiguator and never guess if the winner is not unique.
    project_tokens = set(_package_tokens(project_name))
    if len(subset_matches) > 1 and project_tokens:
        scored = []
        for item in subset_matches:
            overlap = len(project_tokens.intersection(set(item["tokens"])))
            scored.append((overlap, item))
        best_score = max(score for score, _ in scored)
        best = [item for score, item in scored if score == best_score]
        if best_score > 0 and len(best) == 1:
            item = best[0]
            return item["url"], get_remote_info(item["url"]), "official metadata"

    return None, None, None


def _resolve_stem_package_url(dataset, package_name):
    stem = os.path.splitext(str(package_name).strip())[0]
    base_url = dataset.get("package_base_url") or ""
    direct = base_url + urllib.parse.quote(stem, safe="-_.()") + ".zip"

    info = get_remote_info(direct)
    if info["accessible"]:
        return direct, info

    fallback, fallback_info, _ = _find_metadata_package_url(
        dataset,
        "",
        package_name,
    )
    if fallback:
        return fallback, fallback_info or get_remote_info(fallback)

    # The DSM tile-index Package field is intended to identify the archive
    # stem. Keep the deterministic URL as a final fallback so a transient HEAD
    # failure does not block a valid package.
    return direct, info


def create_manual_package_job(issue, dataset_key, download_url):
    """Create a normal download job from a user-supplied direct package URL."""
    extracted = extract_download_url(download_url) or download_url
    safe_url = _validate_http_url(extracted)
    path = urllib.parse.urlparse(safe_url).path
    archive_name = os.path.basename(path)

    if not archive_name.lower().endswith(".zip"):
        raise ValueError(
            "The direct package URL must point to a .zip archive."
        )

    remote_info = get_remote_info(safe_url)
    if not remote_info.get("accessible"):
        raise ValueError(
            "The supplied URL could not be reached as an HTTP(S) package. "
            "Check the link and try again."
        )

    return {
        "project": issue["project"],
        "package": issue["package"],
        "files": list(issue["files"]),
        "download_url": safe_url,
        "listed_size_gb": issue.get("listed_size_gb"),
        "resolution": issue.get("resolution"),
        "vintage": issue.get("vintage"),
        "vertical_datum": issue.get("vertical_datum"),
        "remote_size": remote_info.get("size"),
        "archive_name": archive_name,
        "dataset_key": dataset_key,
        "resolution_source": "manual URL",
    }


def resolve_package_jobs(
    tile_records,
    dataset_key="lidar_dtm",
    manual_urls=None,
    collect_errors=False,
):
    """Resolve requested tiles to package jobs.

    When ``collect_errors`` is True, return ``(jobs, unresolved)`` instead of
    aborting at the first package problem. This lets the UI highlight only the
    affected tiles and continue with valid package groups.
    """
    dataset = RASTER_DATASETS[dataset_key]
    manual_urls = manual_urls or {}
    groups = defaultdict(list)

    for tile in tile_records:
        key = (tile["Project"], tile["Package"])
        groups[key].append(tile["FileName"])

    jobs = []
    unresolved = []

    for (project_name, package_name), filenames in groups.items():
        issue = {
            "project": project_name,
            "package": package_name,
            "files": list(filenames),
            "listed_size_gb": None,
            "resolution": None,
            "vintage": None,
            "vertical_datum": None,
            "error": "",
        }

        try:
            override_key = (dataset_key, str(project_name), str(package_name))
            override_url = manual_urls.get(override_key)
            if override_url:
                jobs.append(
                    create_manual_package_job(
                        issue,
                        dataset_key,
                        override_url,
                    )
                )
                continue

            resolution_source = "package index"

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
                    raise RuntimeError(
                        f"Ontario package service error: {data['error']}"
                    )

                matches = data.get("features", [])
                if not matches:
                    raise RuntimeError(
                        f"No package-index match for {project_name} / {package_name}."
                    )

                attributes = matches[0].get("attributes", {})
                issue["listed_size_gb"] = attributes.get("Size_GB")
                issue["resolution"] = attributes.get("Resolution")
                issue["vintage"] = attributes.get("Vintage")
                issue["vertical_datum"] = attributes.get("VerticalDatum")

                download_url = extract_download_url(
                    attributes.get("DownloadLink")
                )

                if download_url:
                    remote_info = get_remote_info(download_url)
                else:
                    download_url, remote_info, resolution_source = (
                        _find_metadata_package_url(
                            dataset,
                            project_name,
                            package_name,
                        )
                    )

                if not download_url:
                    raise RuntimeError(
                        f"Ontario's package index does not provide a download URL "
                        f"for package '{package_name}', and no unambiguous official "
                        "metadata fallback could be resolved."
                    )

                remote_info = remote_info or get_remote_info(download_url)
                listed_size_gb = issue["listed_size_gb"]
                resolution = issue["resolution"]
                vintage = issue["vintage"]
                vertical_datum = issue["vertical_datum"]

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
                resolution_source = "package stem / official metadata"

            else:
                raise RuntimeError(
                    f"Unsupported package-resolution mode for {dataset['name']}."
                )

            archive_name = os.path.basename(
                urllib.parse.urlparse(download_url).path
            )
            if not archive_name:
                archive_name = re.sub(
                    r'[<>:"/\\|?*]+',
                    "_",
                    package_name,
                ) + ".zip"

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
                    "remote_size": remote_info.get("size") if remote_info else None,
                    "archive_name": archive_name,
                    "dataset_key": dataset_key,
                    "resolution_source": resolution_source,
                }
            )

        except (OSError, ValueError, RuntimeError, urllib.error.URLError) as exc:
            issue["error"] = str(exc)
            unresolved.append(issue)

    if collect_errors:
        return jobs, unresolved

    if unresolved:
        details = "; ".join(
            f"{item['package']}: {item['error']}"
            for item in unresolved
        )
        raise RuntimeError(
            "Could not resolve one or more Ontario package downloads. " + details
        )

    return jobs


def get_package_metadata(
    project_name,
    package_name,
    dataset_key="lidar_dtm",
    resolve_download=True,
):
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
        download_url = extract_download_url(
            attributes.get("DownloadLink")
        )
        source = "package index"
        if not download_url and resolve_download:
            download_url, _, source = _find_metadata_package_url(
                dataset,
                project_name,
                package_name,
            )
        attributes["DownloadURL"] = download_url
        attributes["DownloadResolutionSource"] = source
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
