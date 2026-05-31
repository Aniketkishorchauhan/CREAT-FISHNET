# Fishnet Creator — QGIS Plugin

**Author:** Cyanide  
**License:** GPL-2  
**Compatible:** QGIS 3.x and 4.x  
**Version:** 1.1.0

## Overview

Creates fishnet polygon or polyline grids — equivalent to ArcGIS "Create Fishnet" — with additional features for satellite imagery and remote sensing workflows.

## Features

- Generate fishnet from any loaded layer's extent (one click)
- **Pixel-perfect mode:** match cell size and origin exactly to a raster's pixel grid using GDAL geotransform
- Auto-detects and displays raster pixel size when a layer is selected
- Cell size unit selection: meters, km, feet, miles, yards, nautical miles, degrees
- Live coverage feedback (rows × cols = N cells) with cell size shown in CRS units
- Clip fishnet to a polygon layer boundary (trim or intersect mode)
- Optional label points at cell centroids
- Polygon or polyline output geometry
- Save as temporary scratch layer (no file needed) or to .shp / .gpkg
- Rotation support via Y-axis coordinate
- Compatible with QGIS 3.x (Qt5/PyQt5) and QGIS 4.x (Qt6/PyQt6) — uses `qgis.PyQt` shim

## Installation

### From ZIP (recommended for testing)
1. QGIS → Plugins → Manage and Install Plugins → Install from ZIP
2. Select `fishnet_plugin.zip`
3. Enable "Fishnet Creator" in the Installed tab

### Manual
Copy the `fishnet_plugin/` folder to your QGIS plugins directory:
- **Windows:** `%APPDATA%\QGIS\QGIS3\profiles\default\python\plugins\`
- **macOS:** `~/Library/Application Support/QGIS/QGIS3/profiles/default/python/plugins/`
- **Linux:** `~/.local/share/QGIS/QGIS3/profiles/default/python/plugins/`

## Usage

### Basic workflow (any layer)
1. **Vector → Fishnet → Create Fishnet…**
2. Select your base layer in the **Base Layer** dropdown
3. Click **Set Extent from Layer**
4. Enter **Cell Size Width** and **Height** (e.g. 5 km × 5 km)
5. Tick **Save as temporary scratch layer**
6. Click **Create Fishnet**

### Pixel-perfect raster match
1. Select your raster in **Base Layer**
2. The pixel size is shown automatically (e.g. `📐 Pixel size: 30.0 × 30.0 meters`)
3. Click **Use Pixel Size as Cell Size**
4. Click **Create Fishnet** — each fishnet cell maps to exactly one raster pixel

### Clip to boundary
1. Tick **Clip fishnet to polygon layer**
2. Select your boundary polygon
3. Choose mode: *Clip geometry* (trims cells at edge) or *Keep only intersecting* (full cells)

## Changelog

### 1.1.0
- QGIS 4.x / Qt6 compatibility (`qgis.PyQt` imports, scoped enums, `exec()`)
- Pixel-perfect raster alignment using GDAL geotransform
- Auto pixel-size display when raster layer selected
- Temporary scratch layer option
- Clip polygon CRS reprojection fix
- Removed confusing tile-grid section; simplified to Base Layer workflow
- Cell size unit selector (m, km, ft, mi, yd, nmi, °)
- 10 decimal-place precision on all coordinate spinboxes

### 1.0.0
- Initial release
