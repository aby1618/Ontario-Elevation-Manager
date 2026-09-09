# Ontario Elevation Manager 1.0.2 — Release Notes

## First stable publication candidate

Version 1.0.2 consolidates the workflow developed and tested through the 0.x series into the first official-publication candidate.

## Supported native datasets

- Ontario Digital Terrain Model (LiDAR-Derived)
- Ontario Digital Surface Model (LiDAR-Derived)

## Major capabilities

- AOI from current extent, project polygon, external polygon, or map drawing
- metre/kilometre/foot/mile AOI buffers
- native 1 km tile-index query
- interactive tile selection
- cache detection and reuse
- source package resolution
- resumable package downloads
- selective raster extraction
- IMG/TIF/TIFF handling where applicable
- NoData-safe working VRT
- terrain update/lifecycle comparison
- optional timestamped VRT backups
- cache inventory and cleanup
- GeoTIFF and COG export
- polygon-based clipping
- native/project CRS export options
- configurable output resolution
- compact responsive dock interface
- official plugin icon

## 1.0.2 correction

- Explicitly assigns the bundled icon to the QGIS toolbar/menu action and plugin dock.
- No terrain/download/cache backend behavior was changed from 1.0.1.

## Compatibility status

Validated by the project owner on:

- QGIS 3.44.12 Solothurn
- Windows / OSGeo4W environment

Before broad publication, additional smoke testing on another QGIS 3.x platform/version is recommended.
