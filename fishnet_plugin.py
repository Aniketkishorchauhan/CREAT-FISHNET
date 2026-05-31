"""
fishnet_plugin.py
-----------------
QGIS Plugin entry point. Registers the menu action and toolbar button.
"""

from qgis.PyQt.QtWidgets import QAction
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtCore import Qt
import os


class FishnetPlugin:

    def __init__(self, iface):
        self.iface = iface
        self._action = None
        self._dialog = None

    def initGui(self):
        icon_path = os.path.join(os.path.dirname(__file__), "icon.png")
        icon = QIcon(icon_path) if os.path.exists(icon_path) else QIcon()

        self._action = QAction(icon, "Create Fishnet…", self.iface.mainWindow())
        self._action.setToolTip("Create a fishnet polygon or polyline grid")
        self._action.triggered.connect(self._open_dialog)

        # Add to Vector menu → Fishnet submenu
        self.iface.addPluginToVectorMenu("&Fishnet", self._action)
        # Also add a toolbar button
        self.iface.addToolBarIcon(self._action)

    def unload(self):
        self.iface.removePluginVectorMenu("&Fishnet", self._action)
        self.iface.removeToolBarIcon(self._action)
        self._action = None

    def _open_dialog(self):
        from .fishnet_dialog import FishnetDialog
        dlg = FishnetDialog(self.iface, parent=self.iface.mainWindow())
        dlg.setWindowFlags(dlg.windowFlags() | Qt.WindowType.WindowMinimizeButtonHint)
        dlg.exec()
