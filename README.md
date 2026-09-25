# HEMY Blender BIM add-ons

Source and installable packages for the **11 custom add-ons** in the [BIM UX feature map](docs/BIM_UX_FEATURES.md). The twelfth add-on in that setup, official **Bonsai 0.8.5**, is an external dependency. The documented environment is Blender **5.2.1 LTS**.

| Add-on | Version | What it provides |
| --- | --- | --- |
| `hemy_ifc_panel` | 0.4.2 | IFC properties, class visibility, photo-based layered wall type |
| `bonsai_ux_host` | 0.2.0 | BIM sidebar, task switcher, viewport header, links to companion tools |
| `bonsai_storey_toolbar` | 1.2.0 | Storey selection, plan and 3D controls, view range |
| `workplane_toolkit` | 1.0.0 | Workplane selection and restoration |
| `bonsai_grid_toolbar` | 1.1.0 | IFC grid creation and axis controls |
| `bonsai_plan_dimensions` | 0.1.1 | Plan measurements and driving dimensions for IFC walls and grids |
| `bonsai_dynamic_dimension` | 0.3.2 | Reference and editable aligned dimensions |
| `bonsai_section_box` | 1.1.0 | Section box around selected model elements |
| `bonsai_slab_thickness` | 1.3.0 | Typed slab, wall, and column sizes |
| `bonsai_nucleus_files` | 1.0.0 | Native Nucleus browser for IFC and Blender projects |
| `bonsai_nucleus_sync` | 0.1.0 | Live sync to a Nucleus session |

The source directories hold the custom add-ons captured with the handover. Machine-specific Nucleus server defaults and SDK paths were removed from this repository copy. The two Blender extensions, `bonsai_plan_dimensions` and `workplane_toolkit`, have `blender_manifest.toml` files and their ZIPs use the extension layout. The other nine ZIPs use the legacy add-on folder layout. Keep each add-on's own license files with its source and ZIP.

## Build and install

Run from the repository root:

```powershell
python scripts/package.py
```

This creates **one ZIP per add-on** in `dist/`. GitHub Actions builds and checks the same 11 ZIPs on every push and pull request; download the `blender-addons` artifact from a successful run. The artifact is a build output, not a published GitHub Release.

Install and enable official Bonsai separately. In Blender 5.2, use **Edit → Preferences → Get Extensions → Install from Disk** for the two extension ZIPs; install the other ZIPs as legacy add-ons from the Add-ons preferences. Enable the companion add-ons first and `bonsai_ux_host` last. The UX Host exposes controls from the enabled add-ons; its buttons do not replace their implementations. Nucleus file access and live sync additionally need your own Nucleus service and connection settings.

The BIM sidebar groups controls under **Context, Active Tool, Selection, View, Collaboration**. Its task switcher offers **Select, Create, Grid, Dimension, Workplane, Section, Inspect**. The viewport header exposes the storey, workplane, plan/3D, and Nucleus status controls. The handover lists the exact UI locations and the actions that were tested.

## BIM workspaces

`scripts/build_bim_ux_startup.py` creates a **model-free** `.blend` with **BIM Model**, **BIM Plan**, and **BIM Review** workspaces under `qa/bim_ux_startup.blend`. Run it against Blender's factory startup file, then inspect that output before choosing whether to use it as a startup file:

```powershell
$blender = 'C:\Program Files\Blender Foundation\Blender 5.2\blender.exe'
& $blender --background --factory-startup --python-exit-code 1 --python scripts/build_bim_ux_startup.py
```

The generated `.blend` is ignored by Git. It does not include your production model, user preferences, installed add-ons, or Nucleus credentials. The handover's existing native workspaces and compacted tab setting were part of that Blender profile, so installing ZIPs alone does not recreate those profile settings.

## Validate changes

Use Blender's Python so `bpy` and IfcOpenShell are available:

```powershell
& $blender --background --factory-startup --python-exit-code 1 --python scripts/test_bonsai_ux_host.py
& $blender --background --factory-startup --python-exit-code 1 --python scripts/check_blender.py
& $blender --background --factory-startup --python-exit-code 1 --python scripts/check_wall_authoring.py
```

The wall tests write disposable files under `qa/`. `scripts/check_bonsai_wall_tool.py` also needs Bonsai enabled in the Blender profile. For real-model validation, run `scripts/check_ifc_model.py` with a locally held IFC file after `--`. Do not commit project IFC files or QA output.

## Publish a version

1. Update the version of each changed add-on and record the Blender, Bonsai, and IfcOpenShell versions used for testing.
2. Build all ZIPs, install changed packages in a disposable Blender profile, and test the affected workflow.
3. Commit and tag the tested source. Attach the 11 ZIPs to a GitHub Release with compatibility and migration notes.

See [the Hemy IFC user guide](docs/Hemy_IFC_User_Guide.md) for the photo-wall workflow. The [BIM UX feature map](docs/BIM_UX_FEATURES.md) records the handover's controls and validation limits.
