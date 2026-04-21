'''
─────────────────────────────────────────
  Nifty FBX Exporter
  Blender Addon by Alexander Stock
─────────────────────────────────────────
'''


bl_info = {
	"name": "Nifty FBX Exporter",
	"description": "Batch-export named groups of objects and collections as individual FBX files",
	"author": "Alexander Stock",
	"version": (1, 0, 1),
	"blender": (4, 0, 0),
	"location": "View3D > Sidebar > Nifty FBX Exporter",
	"category": "Import-Export"
	}

import bpy
import bpy.utils.previews
import importlib
import sys
import os

# Reload submodules when the package is already loaded (development workflow)
_SUBMODULES = ("properties", "operators", "app_handlers", "ui")
_pkg = __name__

_preview_collections = {}

def _load_modules():
	for name in _SUBMODULES:
		full = "{}.{}".format(_pkg, name)
		if full in sys.modules:
			importlib.reload(sys.modules[full])
			print("[Nifty FBX Exporter] Reloaded: " + name)
		else:
			print("[Nifty FBX Exporter] Imported: " + name)

_load_modules()

from . import properties, operators, app_handlers, ui


def register():
	properties.register()
	operators.register()
	app_handlers.register()
	ui.register()

	# Load button icon
	pcoll = bpy.utils.previews.new()
	btn_path = os.path.join(os.path.dirname(__file__), "images", "nifty_star_logo_transparent.png")
	if os.path.isfile(btn_path):
		pcoll.load("nifty_btn", btn_path, "IMAGE")

	_preview_collections["nifty"] = pcoll

def unregister():
	for pcoll in _preview_collections.values():
		bpy.utils.previews.remove(pcoll)
	_preview_collections.clear()

	properties.unregister()
	operators.unregister()
	app_handlers.unregister()
	ui.unregister()

if __name__ == "__main__":
	register()