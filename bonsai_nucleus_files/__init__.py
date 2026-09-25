"""Bonsai Nucleus Files — native project storage on NVIDIA Omniverse Nucleus."""
bl_info = {
    "name": "Bonsai Nucleus Files",
    "author": "Bonsai Nucleus Files contributors",
    "version": (1, 0, 0),
    "blender": (4, 2, 0),
    "location": "3D View > Sidebar > Nucleus; File > Nucleus",
    "description": "Browse Nucleus and open/save native IFC and Blender projects",
    "category": "Import-Export",
    "license": "GPL-3.0-or-later",
}


def register():
    from . import ui
    ui.register()


def unregister():
    from . import ui
    ui.unregister()
