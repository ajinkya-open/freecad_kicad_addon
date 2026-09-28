"""Kikakuka KiCad IPC and Action plugin."""

from __future__ import annotations

from add_placeholder_models import main as run_add_placeholder_models
from coupler_3d_viewer import main as run_coupler_3d_viewer
from coupler_helpers import (
    EnableResult,
    HideResult,
    enable_update_coupler_helpers,
    hide_coupler_helpers,
)
from disable_coupler_helpers import main as run_disable_coupler_helpers
from placeholder_models import ScanResult, add_placeholder_models

__all__ = [
    "ScanResult",
    "EnableResult",
    "HideResult",
    "add_placeholder_models",
    "enable_update_coupler_helpers",
    "hide_coupler_helpers",
    "run_add_placeholder_models",
    "run_coupler_3d_viewer",
    "run_disable_coupler_helpers",
]

# Register fallback ActionPlugins only when loaded inside KiCad's GUI application
try:
    import wx
    if wx.GetApp() is not None:
        import pcbnew

        class PopulatePlaceholderAction(pcbnew.ActionPlugin):
            def defaults(self):
                self.name = "Populate Placeholder 3D Models"
                self.category = "Kikakuka"
                self.description = "Add scaled unit cubes to footprints without a valid 3D model"
                self.show_toolbar_button = True
                self.icon_file_name = "placeholder-24.png"

            def Run(self):
                run_add_placeholder_models()

        class Coupler3DViewerAction(pcbnew.ActionPlugin):
            def defaults(self):
                self.name = "Coupler 3D Viewer"
                self.category = "Kikakuka"
                self.description = "Update coupler 3D helpers using Z, Tilt, and Offset"
                self.show_toolbar_button = True
                self.icon_file_name = "coupler-3d-viewer-24.png"

            def Run(self):
                run_coupler_3d_viewer()

        class HideCouplersAction(pcbnew.ActionPlugin):
            def defaults(self):
                self.name = "Hide Couplers"
                self.category = "Kikakuka"
                self.description = "Hide Kikakuka coupler helper 3D models"
                self.show_toolbar_button = True
                self.icon_file_name = "disable-coupler-helpers-24.png"

            def Run(self):
                run_disable_coupler_helpers()

        PopulatePlaceholderAction().register()
        Coupler3DViewerAction().register()
        HideCouplersAction().register()
except Exception:
    pass


