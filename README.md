# HEMY Blender BIM add-ons

Source and release packages for two Blender add-ons used alongside Bonsai:

| Add-on | Source | Current version | Purpose |
| --- | --- | --- | --- |
| Hemy 360 IFC Element Properties | `hemy_ifc_panel/` | 0.4.2 | Inspect IFC properties, control class visibility, and create a layered IFC wall type from a photo. |
| Bonsai UX Host | `bonsai_ux_host/` | 0.2.0 | Task-oriented BIM sidebar, header, and tool shortcuts over installed Bonsai and companion add-ons. |

These add-ons depend on an installed, enabled Bonsai extension. They do not bundle or modify Bonsai or IfcOpenShell. Other companion add-ons visible in Blender are separate dependencies and are not included here.

## Build installable ZIPs

From the repository root, run:

```powershell
python scripts/package.py
```

The script creates `dist/hemy_ifc_panel.zip` and `dist/bonsai_ux_host.zip`. Each ZIP contains one add-on directory with its Python source. In Blender, use **Edit → Preferences → Add-ons → Install from Disk**, select the ZIP, then enable the add-on. Install Bonsai separately.

GitHub Actions builds both ZIPs on every push and pull request. Download a successful run's `blender-addons` artifact for testing. Tagged versions can be attached to a GitHub Release after local Blender/Bonsai verification.

## Check changes locally

Use Blender's Python so `bpy` and IfcOpenShell are available. On Windows, for the installation recorded in the handover:

```powershell
$blender = 'C:\Program Files\Blender Foundation\Blender 5.2\blender.exe'
& $blender --background --factory-startup --python-exit-code 1 --python scripts/test_bonsai_ux_host.py
& $blender --background --factory-startup --python-exit-code 1 --python scripts/check_blender.py
& $blender --background --factory-startup --python-exit-code 1 --python scripts/check_wall_authoring.py
```

The wall tests write disposable files under `qa/`. `scripts/check_bonsai_wall_tool.py` also needs Bonsai enabled in the Blender profile. For real-model validation, run `scripts/check_ifc_model.py` with a locally held IFC file after `--`. Do not commit project IFC files or QA output.

## Release checklist

1. Update the version tuple in each changed add-on; keep its package and release notes aligned.
2. Run the local checks with the Blender, Bonsai, and IfcOpenShell versions you intend to support.
3. Build both ZIPs and install them into a test Blender profile.
4. Commit the source, tag the tested state, and publish the ZIPs in a GitHub Release. Record the tested version combination and any migration notes.

See [the Hemy IFC user guide](docs/Hemy_IFC_User_Guide.md) for the photo-wall workflow.
