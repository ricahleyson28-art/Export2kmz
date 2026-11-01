# Export2KMZ — QGIS 3 plugin

Exports enabled vector layers from the current QGIS project to a Google Earth KMZ file.

## Features
- Point, line, polygon, and multipart vector geometry export.
- Reprojects output coordinates to EPSG:4326.
- Per-layer Description field selection. When no field is selected, the KML Description element is omitted.
- Basic KML styling and optional ExtendedData attributes.

## Installation
Copy the `Export2kmz` plugin folder into your QGIS profile's `python/plugins/` directory, restart QGIS, and enable Export2KMZ in Plugins → Manage and Install Plugins.

See `metadata.txt` and `export2kmz.py` for the implementation.