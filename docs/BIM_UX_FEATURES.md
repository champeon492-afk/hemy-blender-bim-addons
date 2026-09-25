# BIM UX feature map

This maps the 25 September 2026 BIM UX handover to the source in this repository. The recorded installation used Blender 5.2.1 LTS, Bonsai 0.8.5, and 11 custom add-ons. Official Bonsai and IfcOpenShell remain external dependencies.

| Handover control or workflow | Provided by |
| --- | --- |
| Five BIM sidebar sections: Context, Active Tool, Selection, View, Collaboration | `bonsai_ux_host` |
| Select, Create, Grid, Dimension, Workplane, Section, Inspect task switcher | `bonsai_ux_host`, using the companion add-ons below |
| Viewport header project/building, storey, workplane, Plan/3D, Nucleus status | `bonsai_ux_host` with Bonsai and companion add-ons |
| Storey picker, workplane at storey elevation, Plan/3D toggle, view range | `bonsai_storey_toolbar`, `workplane_toolkit` |
| Set or restore workplane from faces, cursor, view, or world axes | `workplane_toolkit` |
| IFC grid creation, axes, offsets, labels | `bonsai_grid_toolbar` |
| Reference and driving dimensions in plan | `bonsai_dynamic_dimension`, `bonsai_plan_dimensions` |
| Typed wall/slab thickness and column profile size | `bonsai_slab_thickness` |
| Photo-based wall type creation, IFC class visibility, IFC property inspection | `hemy_ifc_panel` |
| Full IFC properties in the Properties Editor | `bonsai_ux_host` opens the editor; Bonsai supplies the IFC property interface |
| Fit and reset section box | `bonsai_section_box` |
| Nucleus project browser and native IFC/Blend open and save actions | `bonsai_nucleus_files` |
| Nucleus Live Sync controls | `bonsai_nucleus_sync` |
| BIM Model, BIM Plan, BIM Review workspaces | `scripts/build_bim_ux_startup.py` generates a clean model-free `.blend` for review |

`bonsai_ux_host` brings these controls into one task-oriented interface. Its panels depend on the corresponding companion add-ons being installed and enabled. Nucleus workflows additionally need a configured server, NVIDIA Client Library, and any required authentication. This repository contains no server address, credentials, BIM project model, or saved Blender profile.

The handover verified loading all 12 add-ons in a disposable Blender process and visually inspected the live BIM interface. It did **not** exercise an interactive multi-storey IFC workflow, dimension placement and edits, a remote Nucleus session, a complete restart of the unsaved live model, or 125% display scaling. Those checks remain appropriate before treating this as a production release. Installing these ZIPs does not recreate the live profile's compact sidebar tabs or overwrite its existing startup preferences.
