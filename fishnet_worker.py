"""
fishnet_worker.py
-----------------
QThread worker so the UI stays responsive during generation.
"""

from qgis.PyQt.QtCore import QThread, pyqtSignal
from .fishnet_core import generate_fishnet


class FishnetWorker(QThread):
    progress  = pyqtSignal(int)          # 0–100
    finished  = pyqtSignal(bool, str, object)   # success, message, label_path|None

    def __init__(self, params: dict, parent=None):
        super().__init__(parent)
        self._params = params

    def run(self):
        p = self._params
        try:
            ok, msg, label_path = generate_fishnet(
                origin_x       = p["origin_x"],
                origin_y       = p["origin_y"],
                yaxis_x        = p["yaxis_x"],
                yaxis_y        = p["yaxis_y"],
                cell_width     = p["cell_width"],
                cell_height    = p["cell_height"],
                n_rows         = p["n_rows"],
                n_cols         = p["n_cols"],
                geometry_type  = p["geometry_type"],
                create_labels  = p["create_labels"],
                output_path    = p["output_path"],
                crs_wkt        = p["crs_wkt"],
                clip_geom_wkt  = p.get("clip_geom_wkt"),
                clip_mode      = p.get("clip_mode", 0),
                progress_callback=lambda v: self.progress.emit(v),
            )
            self.finished.emit(ok, msg, label_path)
        except Exception as exc:
            self.finished.emit(False, str(exc), None)
