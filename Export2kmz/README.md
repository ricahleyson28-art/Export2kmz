# Export2KMZ — QGIS 3 plugin

Exports enabled vector layers from the current QGIS project to one Google Earth `.kmz` file.

## Features
- Layer table with enable checkboxes, geometry type, basic style controls, point icon selector, and per-layer Description field selection.
- Point, line, polygon and multipart point/line/polygon geometry export.
- Coordinate transformation to EPSG:4326.
- Optional ExtendedData attributes controlled by “显示属性信息”.
- Description field behavior: selecting a field writes that feature's value into KML `<description>`; `(不添加说明)` omits the Description element.
- KMZ archive containing `doc.kml`.

## Install
Copy this `Export2kmz` folder into the QGIS profile `python/plugins/` directory, restart QGIS, and enable Export2KMZ in Plugins → Manage and Install Plugins.

## Limitations
Basic single-symbol style approximation only; categorized/graduated renderer fidelity, raster layers, and custom icon rendering are not fully supported. Test with your QGIS 3 version and representative project data before production use.
