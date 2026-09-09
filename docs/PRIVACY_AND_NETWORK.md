# Privacy, Network Access, and Local Files

## Accounts and authentication

Ontario Elevation Manager does not require:

- a plugin account;
- an Ontario account;
- an API key;
- an authentication token;
- a cloud-storage account.

## Network access

The plugin makes network requests to public Ontario and ArcGIS endpoints in order to:

- query or obtain official tile indexes;
- resolve source packages;
- check remote package size/availability;
- download requested Ontario source packages.

## User data

The plugin does not intentionally transmit QGIS project files, local vector files, local raster files, cache contents, or user-authored project data to a plugin-operated server.

AOI geometry is used locally to determine intersecting source tiles. For the DTM live tile-index workflow, a spatial query envelope is sent to the official Ontario ArcGIS service to retrieve intersecting tile-index records.

## Local storage

The plugin can create or manage:

- cached Ontario raster tiles;
- downloaded package ZIP files;
- resumable `.part` download files;
- temporary extraction files;
- working VRT files;
- optional VRT backups;
- optional GeoTIFF/COG terrain exports;
- export manifest files.

Cache cleanup is always user initiated.

## Third-party services

Availability of source data depends on public Government of Ontario / ArcGIS services outside the control of the plugin authors.
