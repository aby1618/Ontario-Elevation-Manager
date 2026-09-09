# Ontario Elevation Manager — v1.0.1

Ontario Elevation Manager is a QGIS 3.44 plugin for working with the native Ontario LiDAR-derived **DTM** and **DSM** products without manually locating dozens of 1 km raster tiles inside large provincial packages.

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
- `.img` source rasters.

### LiDAR DSM

- native Ontario 1 km digital-surface-model tiles;
- official Ontario DSM tile-index ZIP downloaded/cached automatically;
- native Ontario DSM source packages;
- `.img`, `.tif` and `.tiff` source rasters supported.

DTM and DSM cache data are kept separate while sharing the same user-selected cache root.

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
3. Select `ontario_elevation_manager_v1.0.1.zip`.
4. Enable/open **Ontario Elevation Manager** from the Raster menu or toolbar.

The internal plugin folder remains `ontario_dtm_manager` so existing installations can be upgraded without changing the QGIS plugin package identity.


### 1.0.1 final corrections

- Added the official plugin icon based on the selected Ontario Tile logo.
- Fixed Area of Interest radio-button exclusivity so only one AOI source can be selected at a time.
