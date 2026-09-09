# Changelog

## 1.0.2

- Fixed the QGIS toolbar/menu action to explicitly use the bundled Ontario Elevation Manager icon.
- Applied the same icon to the plugin dock window.
- No backend workflow changes.

## 1.0.1

- Fixed Section 2 AOI radio-button logic so only one AOI source can be selected at a time.
- Added the selected Ontario Tile logo as the plugin icon.
- Retained all backend logic and workflow behavior from v1.0.0.

## 1.0.0

- Promoted the validated native DTM + DSM workflow to the first stable release.
- Renumbered the main UI sections sequentially from 1 through 6.
- Reworked the dock for narrow-width responsiveness.
- Added compact `?` help controls and moved persistent explanatory text into contextual help/tooltips.
- Added AOI selection from one polygon layer already loaded in the QGIS project.
- Added AOI selection from one external polygon vector source without requiring it to be loaded into the project.
- Added explicit non-polygon validation for AOI vector sources.
- Added metre, kilometre, foot and mile AOI-buffer input units while retaining metre-based spatial processing.
- Made drawn-AOI status guidance dynamic and hidden after successful capture.
- Made Terrain Update / Lifecycle a nested collapsible subsection.
- Replaced the large cache summary block with compact statistics and per-stat help controls.
- Retained all validated DTM/DSM package, cache, extraction, VRT, lifecycle and export backend behavior.

## 0.8.0 (corrected)

- Added full native LiDAR DSM workflow alongside DTM.
- Added official DSM tile-index download/cache/refresh.
- Added DSM native package resolution and IMG/TIF/TIFF support.
- Retained flexible polygon export clipping and collapsible sections.
