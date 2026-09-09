# Ontario Elevation Manager — User Guide

## 1. Ontario Elevation Dataset

Choose the native Ontario elevation product to use for the workflow.

### LiDAR DTM

The Ontario Digital Terrain Model (LiDAR-Derived) represents bare-earth terrain derived from classified LiDAR point clouds.

### LiDAR DSM

The Ontario Digital Surface Model (LiDAR-Derived) represents the highest reflective surface and may include buildings, vegetation, towers, poles, and other above-ground features.

Changing the active dataset changes the tile index, package downloads, local cache, VRT, lifecycle state, and export workflow.

## 2. Area of Interest

The AOI determines which 1 km source tiles are returned.

### Current map extent

Uses the current visible QGIS map extent.

### Polygon dataset — From Project

Choose one polygon vector layer already loaded in QGIS.

- If that layer has selected features, only the selected features are used.
- If the layer has no selected features, the full layer is used.
- Only one polygon layer can be the active AOI source.

### Polygon dataset — Browse

Choose one external polygon vector dataset without first loading it into the QGIS project.

Common OGR-readable formats such as Shapefile and GeoPackage are supported.

If a GeoPackage contains multiple polygon layers, the plugin prompts for one layer.

Point, line, and non-spatial sources are rejected.

### Draw polygon on map

1. Select **Draw polygon on map**.
2. Click **Draw / Redraw**.
3. Left-click to add vertices.
4. Right-click to finish.
5. Press Esc to cancel.

The AOI remains visible after capture. The AOI boundary is shown separately from its optional buffer.

### Buffer

Available units:

- metres;
- kilometres;
- feet;
- miles.

The chosen value is converted internally to metres before the spatial buffer is calculated.

## 3. Tile Selection

After querying the AOI, the plugin displays the intersecting 1 km tiles.

Statistics show:

- available tiles;
- selected tiles;
- selected tiles already present in cache;
- selected tiles still requiring download/extraction.

Use QGIS feature selection to fine-tune the tile set.

Cached and uncached tiles are styled differently for quick review.

## 4. Terrain

### Cache selection

Two cache modes are supported.

**Global/shared cache:** source tiles can be reused by multiple QGIS projects.

**Project-specific cache:** source tiles are stored at a custom cache root saved with the current QGIS project.

DTM and DSM datasets remain separated within the cache structure.

### Terrain name and VRT

The working terrain is a GDAL VRT.

A VRT references the cached native Ontario raster tiles without duplicating them into a new merged raster.

The plugin explicitly assigns NoData to portions of the VRT footprint that are not covered by selected source tiles.

### Terrain Update / Lifecycle

When an existing terrain VRT is found, the plugin compares it with the current tile selection and identifies:

- tiles kept unchanged;
- tiles added;
- tiles removed.

A terrain update creates and validates a replacement VRT before replacing the current VRT.

Optional timestamped VRT backups can be created before updates.

Removing a tile from the VRT does not delete the source raster from cache.

### Download / Build Terrain

When selected rasters are missing:

1. the plugin resolves required source package(s);
2. checks local ZIP/partial-download state;
3. downloads only what is missing;
4. selectively extracts requested raster tiles;
5. validates source rasters;
6. builds the VRT;
7. optionally adds the VRT to the QGIS project.

Large downloads run as background QGIS tasks and can be cancelled.

## 5. Cache / Dataset Management

The cache summary reports:

### Raster tiles

Native Ontario DTM/DSM raster files currently stored in the active cache.

### Sidecar/support files

Auxiliary files belonging to source rasters.

### Completed package ZIPs

Fully downloaded Ontario package archives retained in cache.

### Partial downloads

Interrupted package downloads kept as `.part` files for possible resume.

### Temporary extracts

Temporary extraction files created during package processing.

### Current terrain cached tiles

Cached source tiles referenced by the current terrain.

### Not used by current terrain/selection

Cached tiles that are not referenced by the current terrain and are not currently selected.

In a global cache these tiles may still be required by another QGIS project.

### Cleanup

Cache cleanup is manual. Review the proposed deletion categories carefully, particularly when using a shared cache.

## 6. Terrain Export / Delivery

The working VRT can remain the primary project terrain or be converted to a standalone raster deliverable.

### Clip extent

Options:

- AOI + buffer;
- AOI only;
- project polygon layer;
- external polygon vector file;
- no clip.

Project polygon export uses selected features when a selection exists; otherwise it uses the entire chosen layer.

External vector clip files must contain polygon geometry.

### Output format

**GeoTIFF:** standard standalone GeoTIFF.

**Cloud Optimized GeoTIFF (COG):** GeoTIFF organized for efficient range-based access.

### Output CRS

**Native Ontario source CRS:** retains the source raster CRS.

**Current QGIS project CRS:** reprojects the exported deliverable.

### Output resolution

Native resolution is recommended unless a resampled product is specifically required.

### Deliverable status

The plugin tracks export settings and working-terrain state. If the working terrain or relevant export settings change, an existing deliverable can be identified as requiring regeneration.

## Troubleshooting

### No tiles returned

Confirm that:

- the AOI is located within the geographic coverage of the active Ontario dataset;
- internet access is available;
- the active dataset index is available/updated.

### A vector AOI is rejected

The AOI source must be polygon geometry. Point and line layers are intentionally rejected.

### A package download was interrupted

Retain the `.part` file and use the same cache directory. The plugin can attempt to resume when the Ontario source server supports byte-range requests.

### VRT contains gaps

Gaps caused by intentionally unselected tiles are represented as NoData rather than valid zero elevation.

### Terrain appears outdated

Open **Terrain Update / Lifecycle**, refresh the change summary, and compare the current tile selection with the existing VRT.

### Export is marked outdated

Regenerate the exported deliverable after changing:

- the working VRT;
- AOI/buffer;
- output CRS;
- output resolution;
- clip settings;
- output format.

## Important data note

Ontario's DTM and DSM datasets compile multiple LiDAR acquisition projects. Resolution, acquisition dates, sensor characteristics, and other specifications can vary by project. Review the Ontario source metadata when project-specific data quality requirements apply.
