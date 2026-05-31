"""
fishnet_dialog.py
-----------------
Dialog that replicates ArcGIS "Create Fishnet" UI in QGIS.

Layout mirrors the ArcGIS panel:
  - Output path
  - Template extent  (Top / Left / Right / Bottom + helpers)
  - Origin coordinate
  - Y-Axis coordinate  (defines rotation)
  - Cell Size Width / Height
  - Number of Rows / Columns
  - Opposite Corner   (alternative definition; auto-syncs with rows/cols)
  - Create Label Points
  - Geometry Type
  - CRS selector
  - Progress bar + OK / Cancel
"""

import os
import math

from qgis.PyQt.QtWidgets import (
    QDialog, QDialogButtonBox, QFileDialog, QGroupBox,
    QHBoxLayout, QVBoxLayout, QGridLayout, QLabel,
    QLineEdit, QDoubleSpinBox, QSpinBox, QCheckBox,
    QComboBox, QPushButton, QProgressBar, QSizePolicy,
    QMessageBox, QFrame,
)
from qgis.PyQt.QtCore import Qt, QPoint
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtGui import QFont

from qgis.core import (
    QgsProject, QgsVectorLayer, QgsCoordinateReferenceSystem,
    QgsMapLayerProxyModel,
)
from qgis.gui import QgsProjectionSelectionWidget, QgsMapLayerComboBox

from .fishnet_core import rows_cols_from_extent, compute_angle
from .fishnet_worker import FishnetWorker


# ---------------------------------------------------------------------------
# Helper: thin horizontal separator
# ---------------------------------------------------------------------------
def _separator():
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setFrameShadow(QFrame.Shadow.Sunken)
    return line


# ---------------------------------------------------------------------------
# Double spin box with generous range
# ---------------------------------------------------------------------------
def _coord_spin():
    sb = QDoubleSpinBox()
    sb.setDecimals(10)          # enough for sub-metre degree precision
    sb.setRange(-1e12, 1e12)
    sb.setSingleStep(1.0)
    sb.setMinimumWidth(130)
    return sb


def _positive_spin(decimals=10):   # 10 dp → no rounding on sub-degree pixel sizes
    sb = QDoubleSpinBox()
    sb.setDecimals(decimals)
    sb.setRange(1e-12, 1e12)
    sb.setSingleStep(1.0)
    sb.setMinimumWidth(100)
    return sb


# ---------------------------------------------------------------------------
# Dialog
# ---------------------------------------------------------------------------
class FishnetDialog(QDialog):

    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self._worker = None
        self._block_sync = False        # prevents infinite sync loops
        self._stored_extent = None      # (xmin, ymin, xmax, ymax) set by extent buttons

        self.setWindowTitle("Create Fishnet")
        self.setMinimumWidth(520)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)

        # Window icon
        icon_path = os.path.join(os.path.dirname(__file__), "icon.png")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))

        self._build_ui()
        self._connect_signals()
        self._load_project_crs()

    # -----------------------------------------------------------------------
    # UI construction
    # -----------------------------------------------------------------------

    def _build_ui(self):
        main = QVBoxLayout(self)
        main.setSpacing(8)

        # ---- Output path ------------------------------------------------
        out_grp = QGroupBox("Output Feature Class")
        out_vlay = QVBoxLayout(out_grp)

        # Temp layer toggle
        self.chk_temp = QCheckBox("Save as temporary scratch layer  (no file needed)")
        self.chk_temp.setToolTip(
            "Creates the fishnet as an in-memory scratch layer.\n"
            "It appears in the Layers panel but is not saved to disk.\n"
            "You can export it later via Layer → Save As."
        )
        out_vlay.addWidget(self.chk_temp)

        out_file_row = QHBoxLayout()
        self.output_edit = QLineEdit()
        self.output_edit.setPlaceholderText("Choose output file (.shp or .gpkg)…")
        self.btn_browse = QPushButton("…")
        self.btn_browse.setFixedWidth(30)
        out_file_row.addWidget(self.output_edit)
        out_file_row.addWidget(self.btn_browse)
        out_vlay.addLayout(out_file_row)

        main.addWidget(out_grp)

        # ---- Template extent ---------------------------------------------
        # Column layout:
        #   col 0: "Left" label  (right-aligned)
        #   col 1: Left spinbox
        #   col 2: Right spinbox
        #   col 3: "Right" label (left-aligned)
        # Top / Bottom spinboxes span cols 1–2, centred above / below the row.

        ext_grp = QGroupBox("Template Extent  (optional)")
        ext_grid = QGridLayout(ext_grp)
        ext_grid.setColumnStretch(1, 1)
        ext_grid.setColumnStretch(2, 1)

        # Row 0 — "Top" label centred over cols 1–2
        lbl_top = QLabel("Top")
        lbl_top.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ext_grid.addWidget(lbl_top, 0, 1, 1, 2)

        # Row 1 — Top spinbox centred over cols 1–2
        self.ext_top = _coord_spin()
        ext_grid.addWidget(self.ext_top, 1, 1, 1, 2, Qt.AlignmentFlag.AlignHCenter)

        # Row 2 — Left label | left spin | right spin | Right label
        lbl_left = QLabel("Left")
        lbl_left.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        ext_grid.addWidget(lbl_left, 2, 0)

        self.ext_left = _coord_spin()
        ext_grid.addWidget(self.ext_left, 2, 1)

        self.ext_right = _coord_spin()
        ext_grid.addWidget(self.ext_right, 2, 2)

        lbl_right = QLabel("Right")
        lbl_right.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        ext_grid.addWidget(lbl_right, 2, 3)

        # Row 3 — "Bottom" label centred over cols 1–2
        lbl_bottom = QLabel("Bottom")
        lbl_bottom.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ext_grid.addWidget(lbl_bottom, 3, 1, 1, 2)

        # Row 4 — Bottom spinbox centred over cols 1–2
        self.ext_bottom = _coord_spin()
        ext_grid.addWidget(self.ext_bottom, 4, 1, 1, 2, Qt.AlignmentFlag.AlignHCenter)

        # Row 5 — Base Layer picker (always visible)
        base_row = QHBoxLayout()
        base_row.addWidget(QLabel("Base Layer:"))

        self.layer_combo = QgsMapLayerComboBox()
        self.layer_combo.setFilters(
            QgsMapLayerProxyModel.Filter.VectorLayer | QgsMapLayerProxyModel.Filter.RasterLayer
        )
        self.layer_combo.setAllowEmptyLayer(True)
        self.layer_combo.setToolTip("Pick a loaded layer — the fishnet will cover its full extent")
        base_row.addWidget(self.layer_combo)

        self.btn_layer_extent = QPushButton("Set Extent from Layer")
        self.btn_layer_extent.setToolTip(
            "Fill the extent fields from the selected layer.\n"
            "Rows and columns are recalculated automatically from your cell size."
        )
        base_row.addWidget(self.btn_layer_extent)

        self.btn_canvas_extent = QPushButton("Set from Canvas")
        self.btn_canvas_extent.setToolTip("Use the current map canvas view as the extent")
        base_row.addWidget(self.btn_canvas_extent)

        self.btn_clear_extent = QPushButton("Clear")
        base_row.addWidget(self.btn_clear_extent)

        ext_grid.addLayout(base_row, 5, 0, 1, 4)

        # Row 6 — Pixel info (auto-populated when a raster layer is selected)
        pixel_row = QHBoxLayout()

        self.lbl_pixel_info = QLabel("")
        self.lbl_pixel_info.setStyleSheet("color: #2255aa; font-size: 10px;")
        self.lbl_pixel_info.setToolTip(
            "Spatial resolution of the selected raster layer.\n"
            "This is the size of one pixel in the layer's CRS units."
        )
        pixel_row.addWidget(self.lbl_pixel_info)
        pixel_row.addStretch()

        self.btn_use_pixel_size = QPushButton("Use Pixel Size as Cell Size")
        self.btn_use_pixel_size.setToolTip(
            "Set the fishnet cell width and height to exactly match\n"
            "the pixel resolution of the selected raster layer.\n"
            "CRS will also be set to match the raster."
        )
        self.btn_use_pixel_size.setVisible(False)   # shown only for raster layers
        pixel_row.addWidget(self.btn_use_pixel_size)

        ext_grid.addLayout(pixel_row, 6, 0, 1, 4)
        main.addWidget(ext_grp)

        # ---- Origin + Y-Axis side by side --------------------------------
        origin_row = QHBoxLayout()

        origin_grp = QGroupBox("Fishnet Origin Coordinate")
        og = QGridLayout(origin_grp)
        og.addWidget(QLabel("X Coordinate"), 0, 0)
        og.addWidget(QLabel("Y Coordinate"), 0, 1)
        self.origin_x = _coord_spin()
        self.origin_y = _coord_spin()
        og.addWidget(self.origin_x, 1, 0)
        og.addWidget(self.origin_y, 1, 1)
        origin_row.addWidget(origin_grp)

        yaxis_grp = QGroupBox("Y-Axis Coordinate  (defines rotation)")
        yg = QGridLayout(yaxis_grp)
        yg.addWidget(QLabel("X Coordinate"), 0, 0)
        yg.addWidget(QLabel("Y Coordinate"), 0, 1)
        self.yaxis_x = _coord_spin()
        self.yaxis_y = _coord_spin()
        yg.addWidget(self.yaxis_x, 1, 0)
        yg.addWidget(self.yaxis_y, 1, 1)
        origin_row.addWidget(yaxis_grp)

        main.addLayout(origin_row)

        # ---- Cell size + Rows/Cols + Opposite corner --------------------
        # Unit options: (display label, factor_to_meters, short_code)
        # factor_to_meters = None means "degrees" — no metric conversion.
        self._UNITS = [
            ("m  – Meters",           1.0,       "m"),
            ("km – Kilometers",       1000.0,    "km"),
            ("ft  – Feet",            0.3048,    "ft"),
            ("mi  – Miles",           1609.344,  "mi"),
            ("yd  – Yards",           0.9144,    "yd"),
            ("nmi – Nautical Miles",  1852.0,    "nmi"),
            ("°   – Degrees",         None,      "°"),
        ]

        grid_grp = QGroupBox("Grid Definition")
        gg = QGridLayout(grid_grp)

        # Row 0: labels
        gg.addWidget(QLabel("Cell Size Width"),   0, 0, 1, 2)
        gg.addWidget(QLabel("Cell Size Height"),  0, 2, 1, 2)
        gg.addWidget(QLabel("Number of Rows"),    0, 4)
        gg.addWidget(QLabel("Number of Columns"), 0, 5)

        # Row 1: spinbox + unit combo for width, same for height, then rows/cols
        self.cell_width  = _positive_spin()
        self.cell_height = _positive_spin()

        self.unit_width  = QComboBox()
        self.unit_height = QComboBox()
        for label, _, _ in self._UNITS:
            self.unit_width.addItem(label)
            self.unit_height.addItem(label)
        self.unit_width.setToolTip(
            "Unit for cell width.\n"
            "Changing unit auto-converts the current value.\n"
            "On run, the value is converted to the CRS map units."
        )
        self.unit_height.setToolTip(self.unit_width.toolTip())

        self.n_rows = QSpinBox()
        self.n_rows.setRange(1, 1_000_000)
        self.n_rows.setValue(10)
        self.n_cols = QSpinBox()
        self.n_cols.setRange(1, 1_000_000)
        self.n_cols.setValue(10)

        gg.addWidget(self.cell_width,  1, 0)
        gg.addWidget(self.unit_width,  1, 1)
        gg.addWidget(self.cell_height, 1, 2)
        gg.addWidget(self.unit_height, 1, 3)
        gg.addWidget(self.n_rows,      1, 4)
        gg.addWidget(self.n_cols,      1, 5)

        # Unit status label — shows effective size in CRS units
        self.lbl_unit_status = QLabel("")
        self.lbl_unit_status.setStyleSheet("color: gray; font-size: 10px;")
        gg.addWidget(self.lbl_unit_status, 2, 0, 1, 6)

        main.addWidget(grid_grp)

        # ---- Options ----------------------------------------------------
        opt_grp = QGroupBox("Options")
        opt_lay = QVBoxLayout(opt_grp)

        # --- Row 1: Label points + Geometry type
        row1 = QHBoxLayout()
        self.chk_labels = QCheckBox("Create Label Points  (cell centroids)")
        row1.addWidget(self.chk_labels)
        row1.addStretch()
        row1.addWidget(QLabel("Geometry Type:"))
        self.cmb_geom = QComboBox()
        self.cmb_geom.addItems(["POLYGON", "POLYLINE"])
        row1.addWidget(self.cmb_geom)
        opt_lay.addLayout(row1)

        # --- Row 2: CRS
        crs_row = QHBoxLayout()
        crs_row.addWidget(QLabel("CRS:"))
        self.crs_widget = QgsProjectionSelectionWidget()
        crs_row.addWidget(self.crs_widget)
        opt_lay.addLayout(crs_row)

        opt_lay.addWidget(_separator())

        # --- Clip to polygon layer
        clip_row = QHBoxLayout()
        self.chk_clip = QCheckBox("Clip fishnet to polygon layer:")
        self.chk_clip.setToolTip(
            "Remove or trim cells that fall outside the selected polygon layer.\n"
            "Cells fully outside are deleted; cells partially outside are clipped to the boundary."
        )
        self.cmb_clip_layer = QgsMapLayerComboBox()
        self.cmb_clip_layer.setFilters(QgsMapLayerProxyModel.Filter.PolygonLayer)
        self.cmb_clip_layer.setAllowEmptyLayer(True)
        self.cmb_clip_layer.setEnabled(False)
        self.cmb_clip_mode = QComboBox()
        self.cmb_clip_mode.addItems(["Clip geometry to boundary", "Keep only cells that intersect"])
        self.cmb_clip_mode.setEnabled(False)
        self.chk_clip.toggled.connect(self.cmb_clip_layer.setEnabled)
        self.chk_clip.toggled.connect(self.cmb_clip_mode.setEnabled)
        clip_row.addWidget(self.chk_clip)
        clip_row.addWidget(self.cmb_clip_layer)
        clip_row.addWidget(self.cmb_clip_mode)
        opt_lay.addLayout(clip_row)

        main.addWidget(opt_grp)

        # ---- Progress bar -----------------------------------------------
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        main.addWidget(self.progress_bar)

        # ---- Rotation info label ----------------------------------------
        self.lbl_rotation = QLabel("Rotation: 0.000°")
        self.lbl_rotation.setStyleSheet("color: gray; font-size: 10px;")
        main.addWidget(self.lbl_rotation)

        # ---- Buttons ----------------------------------------------------
        btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.btn_ok     = btn_box.button(QDialogButtonBox.StandardButton.Ok)
        self.btn_ok.setText("Create Fishnet")
        self.btn_cancel = btn_box.button(QDialogButtonBox.StandardButton.Cancel)
        btn_box.accepted.connect(self._run)
        btn_box.rejected.connect(self.reject)
        main.addWidget(btn_box)

    # -----------------------------------------------------------------------
    # Signal connections
    # -----------------------------------------------------------------------

    def _connect_signals(self):
        self.btn_browse.clicked.connect(self._browse_output)
        self.btn_canvas_extent.clicked.connect(self._set_from_canvas)
        self.btn_layer_extent.clicked.connect(self._set_from_layer)
        self.btn_clear_extent.clicked.connect(self._clear_extent)
        self.btn_use_pixel_size.clicked.connect(self._use_pixel_size)

        # Auto-show pixel info whenever the base layer selection changes
        self.layer_combo.layerChanged.connect(self._on_base_layer_changed)
        self._on_base_layer_changed(self.layer_combo.currentLayer())

        # Temp layer toggle — disable file path when checked
        def _on_temp_toggled(checked):
            self.output_edit.setEnabled(not checked)
            self.btn_browse.setEnabled(not checked)
            if checked:
                self.output_edit.setPlaceholderText("(temporary layer — no file needed)")
            else:
                self.output_edit.setPlaceholderText("Choose output file (.shp or .gpkg)…")
        self.chk_temp.toggled.connect(_on_temp_toggled)

        # Unit conversion — when user changes unit, convert existing value
        self._prev_unit_w = 0
        self._prev_unit_h = 0

        def on_width_unit(new_idx):
            self._on_unit_changed(self.cell_width, self._prev_unit_w, new_idx)
            self._prev_unit_w = new_idx

        def on_height_unit(new_idx):
            self._on_unit_changed(self.cell_height, self._prev_unit_h, new_idx)
            self._prev_unit_h = new_idx

        self.unit_width.currentIndexChanged.connect(on_width_unit)
        self.unit_height.currentIndexChanged.connect(on_height_unit)

        # Update unit status label
        for w in (self.cell_width, self.cell_height):
            w.valueChanged.connect(self._update_unit_status)
        self.unit_width.currentIndexChanged.connect(self._update_unit_status)
        self.unit_height.currentIndexChanged.connect(self._update_unit_status)
        self.crs_widget.crsChanged.connect(self._update_unit_status)

        # When cell size or unit changes, recalculate rows/cols if extent is known
        self.cell_width.valueChanged.connect(self._on_cell_size_changed)
        self.cell_height.valueChanged.connect(self._on_cell_size_changed)
        self.unit_width.currentIndexChanged.connect(self._on_cell_size_changed)
        self.unit_height.currentIndexChanged.connect(self._on_cell_size_changed)
        self.crs_widget.crsChanged.connect(self._on_cell_size_changed)

        # Rotation display
        for widget in (self.origin_x, self.origin_y,
                       self.yaxis_x, self.yaxis_y):
            widget.valueChanged.connect(self._update_rotation_label)

    # -----------------------------------------------------------------------
    # Extent helpers
    # -----------------------------------------------------------------------

    def _set_from_layer(self):
        """Set extent directly from the visible Base Layer dropdown."""
        layer = self.layer_combo.currentLayer()
        if not layer:
            QMessageBox.warning(self, "No layer selected",
                                "Please select a layer in the Base Layer dropdown.")
            return
        ext = layer.extent()
        self._fill_extent(ext.xMinimum(), ext.yMinimum(),
                          ext.xMaximum(), ext.yMaximum())

    def _set_from_canvas(self):
        ext = self.iface.mapCanvas().extent()
        self._fill_extent(ext.xMinimum(), ext.yMinimum(),
                          ext.xMaximum(), ext.yMaximum())

    # -----------------------------------------------------------------------
    # Raster pixel info
    # -----------------------------------------------------------------------

    def _on_base_layer_changed(self, layer):
        """
        Called whenever the Base Layer combo changes.
        Shows pixel size from GDAL geotransform (most precise) or QGIS API fallback.
        """
        from qgis.core import QgsRasterLayer, QgsUnitTypes

        if not layer or not isinstance(layer, QgsRasterLayer):
            self.lbl_pixel_info.setText(
                "  (Select a raster layer to see its pixel size)" if layer is None else ""
            )
            self.btn_use_pixel_size.setVisible(False)
            return

        # Try GDAL geotransform first for exact values
        px_w = px_h = None
        source_label = "QGIS API"
        try:
            from osgeo import gdal
            gdal.UseExceptions()
            src = layer.source().split("|")[0]
            ds  = gdal.Open(src, gdal.GA_ReadOnly)
            if ds:
                gt   = ds.GetGeoTransform()
                px_w = gt[1]
                px_h = abs(gt[5])
                source_label = "GDAL"
                ds = None
        except Exception:
            pass

        if px_w is None:
            px_w = layer.rasterUnitsPerPixelX()
            px_h = layer.rasterUnitsPerPixelY()

        crs_unit_name = QgsUnitTypes.toString(layer.crs().mapUnits())
        n_cols = layer.width()
        n_rows = layer.height()
        bands  = layer.bandCount()

        self.lbl_pixel_info.setText(
            f"  📐 {px_w:.10g} × {px_h:.10g} {crs_unit_name}"
            f"   |   {n_cols} × {n_rows} px"
            f"   |   {bands} band{'s' if bands != 1 else ''}"
            f"   [{source_label}]"
        )
        self.btn_use_pixel_size.setVisible(True)

    def _use_pixel_size(self):
        """
        Set cell size, origin, rows/cols and CRS to exactly match the raster.

        Uses GDAL geotransform coefficients directly — the same values QGIS
        uses internally to position pixels — so alignment is exact at any zoom.
        Falls back to the QGIS layer API if GDAL is unavailable.
        """
        from qgis.core import QgsRasterLayer, QgsUnitTypes

        layer = self.layer_combo.currentLayer()
        if not layer or not isinstance(layer, QgsRasterLayer):
            QMessageBox.warning(self, "No raster layer",
                                "Please select a raster layer in the Base Layer dropdown.")
            return

        # ── 1. Read exact pixel geometry from GDAL geotransform ──────────
        px_w = px_h = xmin = ymax = None
        try:
            from osgeo import gdal
            gdal.UseExceptions()
            src = layer.source().split("|")[0]   # strip layer= suffix for multi-layer files
            ds  = gdal.Open(src, gdal.GA_ReadOnly)
            if ds is not None:
                gt   = ds.GetGeoTransform()
                # gt = [top-left X, pixel width, row rotation,
                #       top-left Y, column rotation, pixel height (negative)]
                xmin = gt[0]
                px_w = gt[1]
                ymax = gt[3]
                px_h = abs(gt[5])       # gt[5] is negative for north-up rasters
                ds = None               # close dataset
        except Exception:
            pass    # GDAL unavailable or failed — fall back below

        # ── 2. QGIS API fallback ──────────────────────────────────────────
        if px_w is None:
            ext  = layer.extent()
            xmin = ext.xMinimum()
            ymax = ext.yMaximum()
            px_w = layer.rasterUnitsPerPixelX()
            px_h = layer.rasterUnitsPerPixelY()

        n_cols    = layer.width()           # exact integer pixel counts
        n_rows    = layer.height()
        lyr_crs   = layer.crs()
        map_units = lyr_crs.mapUnits()
        xmax      = xmin + n_cols * px_w   # recompute from geotransform origin
        ymin      = ymax - n_rows * px_h

        # ── 3. Unit combo ─────────────────────────────────────────────────
        unit_map = {
            QgsUnitTypes.DistanceMeters:        0,
            QgsUnitTypes.DistanceKilometers:    1,
            QgsUnitTypes.DistanceFeet:          2,
            QgsUnitTypes.DistanceMiles:         3,
            QgsUnitTypes.DistanceYards:         4,
            QgsUnitTypes.DistanceNauticalMiles: 5,
            QgsUnitTypes.DistanceDegrees:       6,
        }
        unit_idx = unit_map.get(map_units, 0)

        self._block_sync = True

        # ── 4. Write exact values into the dialog ─────────────────────────
        self.crs_widget.setCrs(lyr_crs)

        self.origin_x.setValue(xmin)
        self.origin_y.setValue(ymax)
        self.yaxis_x.setValue(xmin)
        self.yaxis_y.setValue(ymax + px_h * 0.001)

        self._prev_unit_w = unit_idx
        self._prev_unit_h = unit_idx
        self.unit_width.setCurrentIndex(unit_idx)
        self.unit_height.setCurrentIndex(unit_idx)
        self.cell_width.setValue(px_w)
        self.cell_height.setValue(px_h)

        self.n_rows.setValue(n_rows)
        self.n_cols.setValue(n_cols)

        self.ext_left.setValue(xmin)
        self.ext_right.setValue(xmax)
        self.ext_top.setValue(ymax)
        self.ext_bottom.setValue(ymin)
        self._stored_extent = (xmin, ymin, xmax, ymax)

        self._block_sync = False
        self._update_unit_status()

        # ── 5. Verify pixel-perfect reconstruction ────────────────────────
        drift_x = abs(xmin + n_cols * px_w - xmax)
        drift_y = abs(ymax - n_rows * px_h - ymin)
        tol = max(px_w, px_h) * 1e-6

        if drift_x <= tol and drift_y <= tol:
            self.lbl_unit_status.setStyleSheet("color: #226622; font-size: 10px;")
            unit_str = QgsUnitTypes.toString(map_units)
            self.lbl_unit_status.setText(
                f"  ✓ Pixel-perfect (GDAL geotransform)  —  "
                f"{n_cols} × {n_rows} px  |  "
                f"cell = {px_w:.10g} × {px_h:.10g} {unit_str}"
            )
        else:
            self.lbl_unit_status.setStyleSheet("color: #cc6600; font-size: 10px;")
            self.lbl_unit_status.setText(
                f"  ⚠ Small drift detected (X:{drift_x:.2e}, Y:{drift_y:.2e}) "
                "— raster may have irregular pixels."
            )

    def _fill_extent(self, xmin, ymin, xmax, ymax):
        # Store extent so we can re-use it when cell size or unit changes later
        self._stored_extent = (xmin, ymin, xmax, ymax)

        self._block_sync = True
        self.ext_left.setValue(xmin)
        self.ext_bottom.setValue(ymin)
        self.ext_right.setValue(xmax)
        self.ext_top.setValue(ymax)

        # Origin = TOP-LEFT corner (pixel/raster convention: row 0 is northernmost)
        self.origin_x.setValue(xmin)
        self.origin_y.setValue(ymax)

        # Y-axis points upward/northward from origin — no rotation
        span = ymax - ymin
        self.yaxis_x.setValue(xmin)
        self.yaxis_y.setValue(ymax + (span * 0.001 if span > 0 else 1.0))
        self._block_sync = False

        self._recalc_rows_cols_from_extent(xmin, ymin, xmax, ymax)

    def _recalc_rows_cols_from_extent(self, xmin, ymin, xmax, ymax):
        """
        Recalculate rows/cols to fully cover the stored extent using
        the CONVERTED (CRS-unit) cell size.
        """
        # Convert cell size to CRS units before calculating coverage
        cw_conv, _ = self._to_crs_units(self.cell_width.value(),  self.unit_width.currentIndex())
        ch_conv, _ = self._to_crs_units(self.cell_height.value(), self.unit_height.currentIndex())

        if cw_conv > 0 and ch_conv > 0:
            nc, nr = rows_cols_from_extent(xmin, ymin, xmax, ymax, cw_conv, ch_conv)
            # Safety cap — refuse to generate > 1 million cells silently
            if nr * nc > 1_000_000:
                self.lbl_unit_status.setText(
                    f"  ⚠ Cell size too small: would need {nr:,} × {nc:,} = {nr*nc:,} cells. "
                    "Increase cell size."
                )
                return
            self._block_sync = True
            self.n_rows.setValue(nr)
            self.n_cols.setValue(nc)
            self._block_sync = False
            self._update_unit_status()

    def _on_cell_size_changed(self):
        """
        Called whenever cell size value OR unit changes.
        If we have a stored extent, recompute rows/cols to keep full coverage.
        """
        if hasattr(self, "_stored_extent") and self._stored_extent:
            self._recalc_rows_cols_from_extent(*self._stored_extent)


    def _pick_layer_extent(self):
        """Show a small dialog to choose a layer, then fill the extent from it."""
        from qgis.PyQt.QtWidgets import QDialog, QVBoxLayout, QDialogButtonBox, QLabel
        dlg = QDialog(self)
        dlg.setWindowTitle("Use Layer Extent")
        layout = QVBoxLayout(dlg)
        layout.addWidget(QLabel("Select a layer:"))

        picker = QgsMapLayerComboBox(dlg)
        picker.setFilters(
            QgsMapLayerProxyModel.Filter.VectorLayer | QgsMapLayerProxyModel.Filter.RasterLayer
        )
        layout.addWidget(picker)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, dlg)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        layout.addWidget(btns)

        if dlg.exec() == QDialog.DialogCode.Accepted:
            layer = picker.currentLayer()
            if layer:
                ext = layer.extent()
                self._fill_extent(
                    ext.xMinimum(), ext.yMinimum(),
                    ext.xMaximum(), ext.yMaximum(),
                )
            else:
                QMessageBox.warning(self, "No layer selected", "Please select a layer.")

    def _clear_extent(self):
        self._stored_extent = None
        for sb in (self.ext_top, self.ext_bottom, self.ext_left, self.ext_right):
            sb.setValue(0.0)

    # -----------------------------------------------------------------------
    # Rotation display
    # -----------------------------------------------------------------------

    def _update_rotation_label(self):
        angle_rad = compute_angle(
            self.origin_x.value(), self.origin_y.value(),
            self.yaxis_x.value(),  self.yaxis_y.value(),
        )
        deg = math.degrees(angle_rad)
        self.lbl_rotation.setText(f"Rotation: {deg:.3f}°")

    # -----------------------------------------------------------------------
    # CRS
    # -----------------------------------------------------------------------

    def _load_project_crs(self):
        self.crs_widget.setCrs(QgsProject.instance().crs())

    def _unit_factor(self, index):
        """Return the to-meters factor for a unit combo index (None = degrees)."""
        return self._UNITS[index][1]

    def _unit_code(self, index):
        return self._UNITS[index][2]

    def _on_unit_changed(self, spinbox, old_idx, new_idx):
        """
        Convert the spinbox value from the previous unit to the new unit.
        """
        old_factor = self._unit_factor(old_idx)
        new_factor = self._unit_factor(new_idx)

        # Both are metric — do a direct conversion
        if old_factor is not None and new_factor is not None:
            old_val = spinbox.value()
            converted = old_val * old_factor / new_factor
            spinbox.blockSignals(True)
            spinbox.setValue(round(converted, 6))
            spinbox.blockSignals(False)
        # degree ↔ metric: no meaningful automatic conversion; leave value as-is
        self._update_unit_status()

    def _crs_map_unit_factor(self):
        """
        Return the factor to convert 1 meter → 1 CRS map unit.
        E.g. CRS in meters → 1.0; CRS in degrees → None (can't auto-convert);
        CRS in feet → 1 / 0.3048.
        """
        from qgis.core import QgsUnitTypes
        crs = self.crs_widget.crs()
        if not crs.isValid():
            return None
        mu = crs.mapUnits()
        # QgsUnitTypes.fromUnitToUnitFactor returns factor such that
        # value_in_from * factor = value_in_to
        try:
            factor = QgsUnitTypes.fromUnitToUnitFactor(
                QgsUnitTypes.DistanceMeters, mu
            )
            return factor   # meters → CRS units
        except Exception:
            return None

    def _to_crs_units(self, value, unit_index):
        """
        Convert *value* from the user-chosen unit to the CRS map unit.
        Returns (converted_value, warning_string_or_None).
        """
        from qgis.core import QgsUnitTypes
        user_factor = self._unit_factor(unit_index)   # user unit → meters (None if degrees)
        crs_factor   = self._crs_map_unit_factor()    # meters → CRS unit (None if degrees CRS)

        # user is working in degrees → pass through only if CRS is also degrees
        if user_factor is None:
            crs = self.crs_widget.crs()
            if crs.isValid() and crs.mapUnits() == QgsUnitTypes.DistanceDegrees:
                return value, None
            return value, "⚠ Unit is degrees but CRS uses metric/feet units. Value passed as-is."

        # CRS is in degrees (geographic) → can't do a simple metric conversion
        if crs_factor is None:
            return value, "⚠ CRS is geographic (degrees). Value passed as-is — ensure it is in degrees."

        converted = value * user_factor * crs_factor
        return converted, None

    def _update_unit_status(self):
        """Show effective cell size in CRS units, coverage, and any unit mismatch warnings."""
        crs = self.crs_widget.crs()
        if not crs.isValid():
            self.lbl_unit_status.setText("")
            return

        from qgis.core import QgsUnitTypes
        crs_unit_name = QgsUnitTypes.toString(crs.mapUnits())

        w_conv, w_warn = self._to_crs_units(self.cell_width.value(),  self.unit_width.currentIndex())
        h_conv, h_warn = self._to_crs_units(self.cell_height.value(), self.unit_height.currentIndex())

        # Warn when user mixes metric unit with geographic (degree) CRS
        has_warning = bool(w_warn or h_warn)
        if has_warning:
            warn_text = w_warn or h_warn
            self.lbl_unit_status.setStyleSheet("color: #cc6600; font-size: 10px;")
            self.lbl_unit_status.setText(f"  ⚠ {warn_text}")
            return

        self.lbl_unit_status.setStyleSheet("color: gray; font-size: 10px;")
        msg = (f"  Cell size in CRS units → "
               f"Width: {w_conv:.6g} {crs_unit_name},  "
               f"Height: {h_conv:.6g} {crs_unit_name}")

        # Coverage check when extent is stored
        if self._stored_extent and w_conv > 0 and h_conv > 0:
            xmin, ymin, xmax, ymax = self._stored_extent
            total_w = xmax - xmin
            total_h = ymax - ymin

            # Use same ceiling logic as actual generation
            nc = max(1, math.ceil(total_w / w_conv))
            nr = max(1, math.ceil(total_h / h_conv))
            total_cells = nc * nr

            if total_cells > 1_000_000:
                self.lbl_unit_status.setStyleSheet("color: #cc0000; font-size: 10px;")
                msg = (f"  ⚠ Cell size too small — would produce {nr:,} rows × {nc:,} cols "
                       f"= {total_cells:,} cells. Increase cell size.")
            else:
                coverage_pct_w = min(100, (nc * w_conv / total_w) * 100)
                coverage_pct_h = min(100, (nr * h_conv / total_h) * 100)
                msg += (f"  |  Grid: {nc} cols × {nr} rows = {total_cells:,} cells  "
                        f"({'full extent' if coverage_pct_w >= 99 and coverage_pct_h >= 99 else f'{coverage_pct_w:.0f}% W, {coverage_pct_h:.0f}% H coverage'})")

        self.lbl_unit_status.setText(msg)

    # -----------------------------------------------------------------------
    # Browse output
    # -----------------------------------------------------------------------

    def _browse_output(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Fishnet As",
            os.path.expanduser("~"),
            "GeoPackage (*.gpkg);;Shapefile (*.shp)",
        )
        if path:
            self.output_edit.setText(path)

    # -----------------------------------------------------------------------
    # Validate inputs
    # -----------------------------------------------------------------------

    def _validate(self):
        errors = []

        if not self.chk_temp.isChecked() and not self.output_edit.text().strip():
            errors.append("Output path is required (or tick 'Save as temporary scratch layer').")

        if self.cell_width.value() <= 0:
            errors.append("Cell Size Width must be greater than 0.")

        if self.cell_height.value() <= 0:
            errors.append("Cell Size Height must be greater than 0.")

        if self.n_rows.value() < 1:
            errors.append("Number of Rows must be at least 1.")

        if self.n_cols.value() < 1:
            errors.append("Number of Columns must be at least 1.")

        if not self.crs_widget.crs().isValid():
            errors.append("Please select a valid CRS.")

        if errors:
            QMessageBox.warning(self, "Invalid Input", "\n".join(errors))
            return False
        return True

    # -----------------------------------------------------------------------
    # Run
    # -----------------------------------------------------------------------

    def _run(self):
        if not self._validate():
            return

        # Convert cell size from user unit → CRS map units
        cell_w, w_warn = self._to_crs_units(self.cell_width.value(),  self.unit_width.currentIndex())
        cell_h, h_warn = self._to_crs_units(self.cell_height.value(), self.unit_height.currentIndex())

        # Warn if conversion was ambiguous but still let user proceed
        warnings = [w for w in (w_warn, h_warn) if w]
        if warnings:
            reply = QMessageBox.warning(
                self, "Unit Conversion Warning",
                "\n".join(warnings) + "\n\nProceed anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.No:
                return

        # Clip layer — dissolve all features into one geometry for clipping.
        # CRITICAL: reproject each feature to the fishnet CRS first, otherwise
        # geometries from a different CRS won't spatially intersect the grid.
        clip_geom_wkt = None
        clip_mode = None
        if self.chk_clip.isChecked():
            clip_layer = self.cmb_clip_layer.currentLayer()
            if clip_layer:
                from qgis.core import (QgsGeometry, QgsCoordinateTransform,
                                       QgsProject)
                fishnet_crs = self.crs_widget.crs()
                clip_crs    = clip_layer.crs()
                need_xform  = fishnet_crs.isValid() and clip_crs != fishnet_crs
                xform = QgsCoordinateTransform(clip_crs, fishnet_crs,
                                               QgsProject.instance()) if need_xform else None

                dissolved = QgsGeometry()
                for feat in clip_layer.getFeatures():
                    geom = feat.geometry()
                    if geom.isEmpty():
                        continue
                    if xform:
                        geom.transform(xform)      # reproject to fishnet CRS
                    dissolved = dissolved.combine(geom) if not dissolved.isEmpty() else geom

                if not dissolved.isEmpty():
                    clip_geom_wkt = dissolved.asWkt()
                    clip_mode = self.cmb_clip_mode.currentIndex()
                else:
                    QMessageBox.warning(self, "Clip layer", "Selected clip layer has no valid geometry.")
                    return
            else:
                QMessageBox.warning(self, "Clip layer", "Please select a polygon layer to clip to.")
                return

        # Determine output path (temp or user-specified)
        import tempfile
        self._is_temp = self.chk_temp.isChecked()
        if self._is_temp:
            tmp_dir = tempfile.gettempdir()
            output_path = os.path.join(tmp_dir, "fishnet_scratch.gpkg")
            # Remove existing temp file so writer can create fresh
            if os.path.exists(output_path):
                try:
                    os.remove(output_path)
                except OSError:
                    pass
        else:
            output_path = self.output_edit.text().strip()

        params = {
            "origin_x":      self.origin_x.value(),
            "origin_y":      self.origin_y.value(),
            "yaxis_x":       self.yaxis_x.value(),
            "yaxis_y":       self.yaxis_y.value(),
            "cell_width":    cell_w,
            "cell_height":   cell_h,
            "n_rows":        self.n_rows.value(),
            "n_cols":        self.n_cols.value(),
            "geometry_type": self.cmb_geom.currentText(),
            "create_labels": self.chk_labels.isChecked(),
            "output_path":   output_path,
            "crs_wkt":       self.crs_widget.crs().toWkt(),
            "clip_geom_wkt": clip_geom_wkt,
            "clip_mode":     clip_mode,
        }

        self.btn_ok.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)

        self._worker = FishnetWorker(params, parent=self)
        self._worker.progress.connect(self.progress_bar.setValue)
        self._worker.finished.connect(self._on_finished)
        self._worker.start()

    def _on_finished(self, success, message, label_path):
        self.progress_bar.setValue(100)
        self.btn_ok.setEnabled(True)

        if not success:
            QMessageBox.critical(self, "Error", message)
            return

        is_temp = getattr(self, "_is_temp", False)
        layer_name = "Fishnet (scratch)" if is_temp else os.path.splitext(os.path.basename(message))[0]

        # Load grid layer
        grid_layer = QgsVectorLayer(message, layer_name, "ogr")
        if grid_layer.isValid():
            if is_temp:
                # Mark as scratch so QGIS shows the "unsaved" indicator
                grid_layer.setCustomProperty("skipMemoryLayersCheck", 1)
            QgsProject.instance().addMapLayer(grid_layer)

        # Load label layer
        if label_path and os.path.exists(label_path):
            lbl_name = "Fishnet Labels (scratch)" if is_temp else \
                       os.path.splitext(os.path.basename(label_path))[0]
            lbl_layer = QgsVectorLayer(label_path, lbl_name, "ogr")
            if lbl_layer.isValid():
                if is_temp:
                    lbl_layer.setCustomProperty("skipMemoryLayersCheck", 1)
                QgsProject.instance().addMapLayer(lbl_layer)

        if is_temp:
            QMessageBox.information(self, "Done",
                "Temporary fishnet layer added to the project.\n"
                "To keep it permanently, right-click the layer → Export → Save Features As…")
        else:
            QMessageBox.information(self, "Done",
                f"Fishnet created successfully.\n\nGrid: {message}"
                + (f"\nLabels: {label_path}" if label_path else ""))
        self.accept()

    def closeEvent(self, event):
        if self._worker and self._worker.isRunning():
            self._worker.quit()
            self._worker.wait()
        super().closeEvent(event)
