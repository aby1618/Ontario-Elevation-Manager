# Ontario Elevation Manager — v1.2.4

Ontario Elevation Manager is a QGIS 3.44+ / QGIS 4.x plugin for working with the native Ontario LiDAR-derived **DTM** and **DSM** products without manually locating dozens of 1 km raster tiles inside large provincial packages.

This is the first stable release of the workflow developed and validated through the 0.x series. It is not an official Government of Ontario or QGIS product.

## Core workflow

For either LiDAR DTM or LiDAR DSM:

1. Choose the Ontario elevation dataset.
2. Define the area of interest.
3. Query the official 1 km tile index.
4. Select/deselect required tiles.
5. Reuse locally cached source rasters where available.
6. Resolve and download only the required Ontario source package archives.
7. Extract only the requested native source rasters and associated sidecars.
8. Build/update a NoData-safe working VRT.
9. Manage terrain lifecycle and cache usage.
10. Optionally create clipped GeoTIFF or COG deliverables.

## V1 interface

The dock is organized into six collapsible sections:

1. **Ontario Elevation Dataset**
2. **Area of Interest**
3. **Tile Selection**
4. **Terrain**
5. **Cache / Dataset Management**
6. **Terrain Export / Delivery**

Long explanatory notes have been moved into compact `?` help controls and tooltips. The dock is designed to remain usable when narrowed: long fields shrink, form rows wrap, and multi-button rows stack vertically when required.

## Area of Interest options

- **Current map extent**
- **Polygon dataset — From Project**
  - choose exactly one polygon vector layer already loaded in QGIS;
  - if features are selected, only the selected features are used;
  - otherwise the whole polygon layer is used.
- **Polygon dataset — Browse**
  - browse to an external polygon vector source without loading it into the project;
  - supports Shapefile, GeoPackage and other common OGR-readable vector files;
  - GeoPackages with multiple polygon layers prompt for one layer;
  - non-polygon vector sources are rejected.
- **Draw polygon on map**
  - left-click vertices;
  - right-click to finish;
  - Esc cancels;
  - completed AOI stays visible as a solid line;
  - buffer is shown as a dashed outline.

### Buffer units

AOI buffer input supports:

- metres
- kilometres
- feet
- miles

The interface converts the selected unit to metres before using the existing local-UTM buffer calculation.

## Dataset support

### LiDAR DTM

- native Ontario 1 km bare-earth terrain tiles;
- live Ontario tile-index and package-index services;
- legacy `.img` and newer `.tif` / `.tiff` source rasters.

### LiDAR DSM

- native Ontario 1 km digital-surface-model tiles;
- official Ontario DSM tile-index ZIP downloaded/cached automatically;
- native Ontario DSM source packages;
- `.img`, `.tif` and `.tiff` source rasters supported.

DTM and DSM cache data are kept separate while sharing the same user-selected cache root.

## Overlapping LiDAR acquisition datasets

Ontario's DTM and DSM coverage can overlap between independent LiDAR acquisition projects. Version 1.2 detects those overlaps before tile selection.

When more than one source project intersects the AOI, the plugin displays a chooser showing the source project, acquisition year/vintage, resolution (when exposed by Ontario metadata), vertical datum, intersecting tile count, package count and raster format. The newest identifiable source is preselected, but users may choose one or more.

Only one source dataset is displayed for tile editing at a time. If several sources are selected, use the **Source dataset** selector in Section 3 to review and fine-tune each independent tile selection.

When more than one source dataset is built, the plugin creates a separate VRT for each acquisition. Source projects are never mixed into a single VRT. Source-specific names are derived from the terrain name, for example:

- `Oakville_DTM__GTA_Lidar_2014_18.vrt`
- `Oakville_DTM__GTA_Lidar_2023.vrt`

Newer Ontario DTM projects distributed as TIFF, including GTA 2023, are supported alongside legacy IMG packages.

## Terrain behavior

- Working terrain is a GDAL VRT, so cached source data are not duplicated.
- VRT gaps are explicitly written as NoData `-9999`, never valid zero elevation.
- Existing terrain updates are transactional: a replacement VRT is validated before the current terrain is replaced.
- Optional timestamped VRT backups are supported.
- Removing a tile from a VRT never deletes the cached Ontario source tile.

## Cache management

The plugin reports and explains:

- native raster tiles;
- sidecar/support files;
- completed package ZIPs;
- resumable partial downloads;
- temporary extraction files;
- cached tiles used by the current terrain;
- cached tiles not currently used;
- total cache size;
- available disk space.

Cache cleanup is always explicit/manual.

## Terrain export

Optional deliverables can be:

- GeoTIFF;
- Cloud Optimized GeoTIFF (COG).

Clip sources can be:

- AOI + buffer;
- AOI only;
- polygon layer from the current QGIS project;
- external polygon vector file;
- full working VRT extent.

Output can retain the native source CRS or use the current QGIS project CRS, with optional output-resolution changes.

## Installation

1. Open **Plugins > Manage and Install Plugins** in QGIS 3.44.
2. Choose **Install from ZIP**.
3. Select `ontario_elevation_manager_v1.2.4.zip`.
4. Enable/open **Ontario Elevation Manager** from the Raster menu or toolbar.

The plugin package folder is `ontario_elevation_manager` so existing installations can be upgraded without changing the QGIS plugin package identity.


### 1.0.1 final corrections

- Added the official plugin icon based on the selected Ontario Tile logo.
- Fixed Area of Interest radio-button exclusivity so only one AOI source can be selected at a time.


## Package-resolution recovery

If Ontario's package index contains a tile/package record but its direct download link is missing, the plugin now tries official Ontario metadata and known archive naming rules before reporting an issue. If a package still cannot be resolved, only the affected tiles are highlighted red and deselected. The user can provide a direct Ontario ZIP URL, open the dataset GeoHub page, zoom to the affected tiles, or continue with the unaffected selected tiles.


## Terrain raster QA

Before a working VRT is committed, the plugin validates source raster consistency
and checks for extreme floating-point sentinel values used as implicit NoData by
some newer Ontario TIF products. Detected source NoData is normalized to the
working VRT NoData value (`-9999`). The completed VRT is then reopened and
post-checked before it is allowed to replace the project terrain.

If QA fails, the existing terrain remains unchanged and affected source tiles are
marked as tile issues in the current tile layer.


### Per-source NoData normalization

Ontario source tiles within one acquisition may use different valid NoData conventions. The plugin records the effective NoData value for each source raster independently and writes that value into the corresponding GDAL VRT source definition. Different source sentinels are therefore normalized to the working VRT NoData value (`-9999`) without altering the downloaded Ontario rasters.


## VRT extreme-value sanitizer

Ontario source rasters can contain extreme Float32 sentinel values near the
limits of the data type. The plugin does not rewrite those source rasters.

Instead, each source in the working VRT receives a GDAL ComplexSource lookup
table. Values throughout a very broad valid range are passed through unchanged,
while extreme sentinel values are mapped virtually to the working NoData value
(-9999). This works even when positive and negative sentinel conventions occur
within the same acquisition or source tile.

The completed VRT is validated before it replaces an existing terrain, and the
plugin computes post-sanitization statistics for a useful QGIS display stretch.
