"""
fishnet_core.py
---------------
All computation for grid generation. No UI dependencies here.
"""

import math

from qgis.core import (
    QgsVectorLayer,
    QgsFeature,
    QgsGeometry,
    QgsPointXY,
    QgsField,
    QgsFields,
    QgsVectorFileWriter,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransformContext,
    QgsWkbTypes,
)
from qgis.PyQt.QtCore import QVariant


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def compute_angle(origin_x, origin_y, yaxis_x, yaxis_y):
    """
    Return the counter-clockwise rotation angle (radians) implied by the
    Y-axis coordinate.  When yaxis == (origin_x, origin_y + anything) the
    angle is 0 (no rotation).
    """
    dx = yaxis_x - origin_x
    dy = yaxis_y - origin_y
    if dx == 0.0 and dy == 0.0:
        return 0.0
    # Y-axis direction vector; rotate grid so this points "up"
    return math.atan2(dx, dy)   # clockwise angle from geographic north


def _rot(lx, ly, origin_x, origin_y, cos_a, sin_a):
    """Rotate local grid point to world coordinates."""
    return QgsPointXY(
        origin_x + lx * cos_a - ly * sin_a,
        origin_y + lx * sin_a + ly * cos_a,
    )


def rows_cols_from_extent(extent_xmin, extent_ymin, extent_xmax, extent_ymax,
                           cell_width, cell_height):
    """Return (n_cols, n_rows) that cover the given extent."""
    n_cols = max(1, math.ceil((extent_xmax - extent_xmin) / cell_width))
    n_rows = max(1, math.ceil((extent_ymax - extent_ymin) / cell_height))
    return n_cols, n_rows


def opposite_corner(origin_x, origin_y, angle,
                    cell_width, cell_height, n_rows, n_cols):
    """
    Return the opposite (bottom-right) corner of the fishnet in world
    coordinates, accounting for rotation.
    Grid grows RIGHT (+X) and DOWN (-Y) from the top-left origin.
    """
    cos_a = math.cos(angle)
    sin_a = math.sin(angle)
    lx =  n_cols * cell_width
    ly = -n_rows * cell_height   # negative = downward from top-left origin
    return (
        origin_x + lx * cos_a - ly * sin_a,
        origin_y + lx * sin_a + ly * cos_a,
    )


# ---------------------------------------------------------------------------
# Main generator
# ---------------------------------------------------------------------------

def generate_fishnet(
    origin_x, origin_y,
    yaxis_x, yaxis_y,
    cell_width, cell_height,
    n_rows, n_cols,
    geometry_type,        # 'POLYGON' or 'POLYLINE'
    create_labels,        # bool
    output_path,          # full path to .shp or .gpkg
    crs_wkt,              # WKT string
    clip_geom_wkt=None,   # WKT of dissolved clip polygon, or None
    clip_mode=0,          # 0 = clip geometry, 1 = intersect-only (keep full cells)
    progress_callback=None,
):
    """
    Write a fishnet grid to *output_path*.

    clip_geom_wkt : if provided, cells are either clipped (mode=0) or
                    filtered by intersection (mode=1) against this geometry.

    Returns (success: bool, message: str, label_path: str|None).
    """

    if cell_width <= 0 or cell_height <= 0:
        return False, "Cell width and height must be greater than zero.", None
    if n_rows <= 0 or n_cols <= 0:
        return False, "Number of rows and columns must be greater than zero.", None

    angle = compute_angle(origin_x, origin_y, yaxis_x, yaxis_y)
    cos_a = math.cos(angle)
    sin_a = math.sin(angle)

    crs_obj = QgsCoordinateReferenceSystem()
    crs_obj.createFromWkt(crs_wkt)

    # ---- clip geometry (optional) ----
    clip_geom = None
    if clip_geom_wkt:
        clip_geom = QgsGeometry.fromWkt(clip_geom_wkt)
        if clip_geom.isEmpty() or clip_geom.isNull():
            clip_geom = None

    # ---- determine output driver ----
    if output_path.lower().endswith(".gpkg"):
        driver_name = "GPKG"
    else:
        driver_name = "ESRI Shapefile"

    # ---- main grid fields ----
    fields = QgsFields()
    fields.append(QgsField("id",  QVariant.Int))
    fields.append(QgsField("row", QVariant.Int))
    fields.append(QgsField("col", QVariant.Int))

    # ---- choose geometry type ----
    if geometry_type == "POLYGON":
        wkb_type = QgsWkbTypes.Polygon
    else:
        wkb_type = QgsWkbTypes.MultiLineString

    save_opts = QgsVectorFileWriter.SaveVectorOptions()
    save_opts.driverName = driver_name
    save_opts.fileEncoding = "UTF-8"

    writer = QgsVectorFileWriter.create(
        output_path, fields, wkb_type, crs_obj,
        QgsCoordinateTransformContext(), save_opts,
    )

    if writer.hasError() != QgsVectorFileWriter.NoError:
        return False, f"Cannot create output file:\n{writer.errorMessage()}", None

    total = n_rows * n_cols
    fid = 1
    written = 0

    for row in range(n_rows):
        for col in range(n_cols):
            # Local coords: X grows right, Y grows DOWN (negative geographic Y)
            # so origin is the TOP-LEFT corner of the grid, row 0 = northernmost row
            lx0 =  col       * cell_width
            ly0 = -(row      * cell_height)   # top edge of this row
            lx1 =  (col + 1) * cell_width
            ly1 = -((row + 1) * cell_height)  # bottom edge of this row

            p00 = _rot(lx0, ly0, origin_x, origin_y, cos_a, sin_a)
            p10 = _rot(lx1, ly0, origin_x, origin_y, cos_a, sin_a)
            p11 = _rot(lx1, ly1, origin_x, origin_y, cos_a, sin_a)
            p01 = _rot(lx0, ly1, origin_x, origin_y, cos_a, sin_a)

            if geometry_type == "POLYGON":
                geom = QgsGeometry.fromPolygonXY([[p00, p10, p11, p01, p00]])
            else:
                geom = QgsGeometry.fromMultiPolylineXY([
                    [p00, p10],
                    [p10, p11],
                    [p11, p01],
                    [p01, p00],
                ])

            # ---- apply clipping ----
            if clip_geom is not None:
                if not geom.intersects(clip_geom):
                    fid += 1
                    if progress_callback:
                        progress_callback(int((fid / total) * (90 if create_labels else 100)))
                    continue   # skip — fully outside

                if clip_mode == 0:
                    # Clip cell geometry to boundary
                    clipped = geom.intersection(clip_geom)
                    if clipped.isEmpty() or clipped.isNull():
                        fid += 1
                        continue
                    geom = clipped
                # mode == 1: keep full cell geometry if it intersects (already passed test above)

            feat = QgsFeature(fields)
            feat["id"]  = fid
            feat["row"] = row + 1
            feat["col"] = col + 1
            feat.setGeometry(geom)
            writer.addFeature(feat)
            written += 1
            fid += 1

            if progress_callback:
                progress_callback(int((fid / total) * (90 if create_labels else 100)))

    del writer   # flush & close

    if written == 0:
        return False, "No cells were written — clip polygon may not overlap the fishnet.", None

    # ---- optional label points ----
    label_path = None
    if create_labels:
        label_path = (
            output_path[:-5] + "_label.gpkg"
            if driver_name == "GPKG"
            else output_path.replace(".shp", "_label.shp")
        )

        lbl_fields = QgsFields()
        lbl_fields.append(QgsField("id",  QVariant.Int))
        lbl_fields.append(QgsField("row", QVariant.Int))
        lbl_fields.append(QgsField("col", QVariant.Int))

        save_opts2 = QgsVectorFileWriter.SaveVectorOptions()
        save_opts2.driverName = driver_name
        save_opts2.fileEncoding = "UTF-8"

        lbl_writer = QgsVectorFileWriter.create(
            label_path, lbl_fields, QgsWkbTypes.Point, crs_obj,
            QgsCoordinateTransformContext(), save_opts2,
        )

        fid = 1
        for row in range(n_rows):
            for col in range(n_cols):
                cx =  (col + 0.5) * cell_width
                cy = -((row + 0.5) * cell_height)   # negative Y — centred in downward cell
                px = origin_x + cx * cos_a - cy * sin_a
                py = origin_y + cx * sin_a + cy * cos_a

                pt = QgsPointXY(px, py)
                pt_geom = QgsGeometry.fromPointXY(pt)

                # Only write label if centroid is inside clip boundary
                if clip_geom is not None and not pt_geom.intersects(clip_geom):
                    fid += 1
                    continue

                feat = QgsFeature(lbl_fields)
                feat["id"]  = fid
                feat["row"] = row + 1
                feat["col"] = col + 1
                feat.setGeometry(pt_geom)
                lbl_writer.addFeature(feat)
                fid += 1

        del lbl_writer
        if progress_callback:
            progress_callback(100)

    return True, output_path, label_path
