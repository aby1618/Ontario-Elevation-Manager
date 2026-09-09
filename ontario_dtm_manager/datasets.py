"""Dataset registry for native Ontario LiDAR raster products."""

RASTER_DATASETS = {
    "lidar_dtm": {
        "name": "LiDAR DTM (bare earth)",
        "short_name": "DTM",
        "description": (
            "Ontario LiDAR-derived Digital Terrain Model. Uses the live 1 km "
            "tile index and package index already validated by the plugin."
        ),
        "geohub_url": (
            "https://geohub.lio.gov.on.ca/maps/mnrf::"
            "ontario-digital-terrain-model-lidar-derived/about"
        ),
        "tile_index_mode": "feature_service",
        "tile_service_url": (
            "https://services6.arcgis.com/MIfAo8rbUEh3uLcq/"
            "ArcGIS/rest/services/"
            "OntarioDTM_LidarDerived_TileIndex/FeatureServer/55"
        ),
        "package_mode": "feature_service",
        "package_service_url": (
            "https://services6.arcgis.com/MIfAo8rbUEh3uLcq/"
            "arcgis/rest/services/"
            "Ontario_DTM_Lidar_Derived_Package_Index/FeatureServer/1"
        ),
        "cache_subdir": "",
        "extensions": (".img",),
        "index_zip_url": None,
        "metadata_url": None,
        "package_base_url": None,
    },
    "lidar_dsm": {
        "name": "LiDAR DSM (surface model)",
        "short_name": "DSM",
        "description": (
            "Ontario LiDAR-derived Digital Surface Model. Uses the official "
            "Ontario 1 km DSM tile index and native downloadable DSM packages. "
            "The same AOI, selection, cache, package, VRT, lifecycle and export "
            "workflow is used as for DTM."
        ),
        "geohub_url": (
            "https://geohub.lio.gov.on.ca/maps/mnrf::"
            "ontario-digital-surface-model-lidar-derived/about"
        ),
        "tile_index_mode": "zip_shapefile",
        "tile_service_url": None,
        "package_mode": "package_stem",
        "package_service_url": None,
        "cache_subdir": "DSM",
        "extensions": (".img", ".tif", ".tiff"),
        "index_zip_url": (
            "https://www.publicdocs.mnr.gov.on.ca/mirb/"
            "OntarioDSM_LidarDerived_TileIndex.zip"
        ),
        "metadata_url": (
            "https://www.arcgis.com/sharing/rest/content/items/"
            "9697ee73dc9346669308a657d7b0d025/info/metadata/metadata.xml"
            "?format=default&output=html"
        ),
        "package_base_url": (
            "https://ws.gisetl.lrc.gov.on.ca/"
            "fmedatadownload/Packages/"
        ),
    },
}
