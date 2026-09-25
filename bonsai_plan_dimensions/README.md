# Bonsai Driving Plan Dimensions

An installable Blender extension for storey-specific, editable IFC grid and wall
dimensions. Editing a value translates end A or B and writes the change to IFC.
Version 0.1.1, GPL-3.0-or-later.

## Requirements and installation

- Enable **Bonsai 0.8.5** first and open an IFC project. The add-on has no extra
  Python dependencies beyond Blender and Bonsai.
- Targets Blender **5.2 LTS**. Development checks use the locally installed
  **Blender 5.2.1 LTS / Bonsai 0.8.5**. Blender 5.2.2 is the current stable patch
  at the time of this delivery. The manifest allows 4.5+, but other combinations
  have not been integration-tested.
- In Blender, open **Edit > Preferences > Get Extensions**, use the menu at the
  top right, and choose **Install from Disk**. Select
  `bonsai_plan_dimensions-0.1.1.zip` and enable it.
- In the 3D View, press **N** and open **Plan Dimensions**.

To try it immediately, open the included `demo_plan_dimensions.ifc` in Bonsai,
choose **Ground** in this panel, use top view, and frame the model with
**Home**. The sample contains two storeys, four walls, two grids and ten saved
driving dimensions. Use Bonsai's storey isolation if both floors are visible.

## Two walls

1. Choose the **Building storey** in the panel. Use Bonsai's own storey isolation
   tools if you also want to hide model elements from other storeys.
2. Switch to **Top Plan View** (or NumPad 7), or enter a downward-facing
   orthographic Bonsai drawing camera.
3. Select two parallel walls on that storey. Select the wall you want to move
   **last**, making it the active object (end B).
4. Choose **Clear faces** for the clear gap, or **Body centres** for spacing
   between the physical wall body centre lines. Set **Line offset** to place
   the dimension beyond the shared ends of the walls.
5. Click **Dimension 2 Selected**. The dimension appears in the plan.
6. Double-click its value, use **Pick Value**, or click its value in the panel.
   Enter the new **Distance**, choose **End B** or **End A**, then click **OK**.
   The other end stays fixed. Blender distance fields accept unit expressions
   such as `3200 mm`, `3.2 m`, or `10 ft`.
7. Save the **IFC project** with Bonsai. A `.blend` file alone is not an IFC save.

For example, changing a clear gap from 3 m to 3.5 m moves the chosen wall 0.5 m
away. Its thickness, length and elevation are preserved. Openings and other
elements whose IFC placements are children of that wall move with it.

## Grids and chains

- Select **individual IfcGridAxis objects**, rather than the parent IfcGrid.
- Select two parallel axes and click **Dimension 2 Selected**, or select three
  or more parallel axes and click **Chain Selected Grids** to dimension each
  adjacent spacing automatically.
- Each chain spacing is independently editable. Changing one moves the chosen
  axis; every other dimension referencing it updates to the resulting distance.
  Other spacing values are not locked or automatically preserved.
- Axis identifiers must have unique, nonempty tags within each U/V/W family.
  To dimension both directions of a rectangular grid, create one chain per
  parallel family.
- An IfcGrid shared by multiple storeys is still one IFC grid. Editing an axis
  changes that shared grid throughout the model; assigning a dimension to a
  storey controls its display, not ownership of the grid geometry.

## Plan and storey scope

Dimensions draw only in a top orthographic viewport or a downward orthographic
plan camera, and only for the storey selected in this panel. Both referenced
objects must be loaded and visible. They disappear in perspective, side views,
and other storeys. This does not change the model's own visibility.

A dimension created while viewing a Bonsai plan camera is also bound to that
exact drawing's IFC GUID. It displays in that camera and in ordinary top view
for its storey, but not in another drawing camera. A dimension created in
ordinary top view remains top-view-only; create it in a drawing camera to bind
it there. Switching drawing cameras does not change the panel's storey choice.

Dimensions are stored as custom `IfcAnnotation` records contained in the chosen
storey. The `BPD_DrivingDimension.Definition` property contains versioned JSON as
`IfcText`. Walls are referenced by GUID; grid axes by parent grid GUID, family,
and tag. Reopening the IFC restores dimensions automatically. Renamed or deleted
axis tags produce a stale-reference message; remove and recreate that dimension.

## Supported geometry and boundaries

This is a focused initial implementation:

- Straight, parallel, vertical walls with an explicit straight IFC Axis
  representation and a rectangular body footprint; straight, two-point
  IfcPolyline grid axes. Rotated plan geometry is supported.
- Walls must belong to the chosen storey, including through spaces/aggregates.
  A grid on a different storey is rejected; building-level grids can be used.
- Finite positive distances, with overlapping longitudinal extents. Zero gaps,
  overlapping clear faces, nonparallel axes, curved/tapered/mitred walls, wall
  path joins, and scaled/sheared/mirrored objects are rejected explicitly.
- Moving a wall translates it; it does not resize it, rejoin neighbouring walls,
  regenerate room boundaries, or run a multi-element constraint solver.
- Hosted objects follow the IFC **placement hierarchy**. Semantic hosting alone
  does not guarantee movement if an imported model uses independent placements.
- Grid axes used by `IfcGridPlacement` intersections cannot be driven in this
  version. Save pending geometry edits and pending wall placements with loaded
  dependents; remove animation/transform constraints
  before driving a dimension.
- Labels and lines are **viewport overlays**. They do not appear in Blender
  image renders, Bonsai SVG/PDF drawing exports, or other IFC viewers. The IFC
  retains the editable dimension definition for this add-on.
- Editing uses Bonsai's IFC undo transaction system; Ctrl+Z/Shift+Ctrl+Z can undo
  and redo successful edits. Failed edits restore the IFC transaction and loaded
  object transforms. The add-on never saves over your IFC automatically.

## Source and verification

The companion source archive contains the add-on, pure geometry tests, Blender
integration tests and the GPU overlay test. See `VALIDATION.md` in the delivery
for the executed checks and remaining test limits.

From the extracted source root, run the independent tests with:

```text
python -m unittest discover -s tests -p test_geometry.py
python -m unittest discover -s tests -p test_wall_adapter.py
blender --background --factory-startup --python-exit-code 1 --python tests/blender_registration.py
blender --background --factory-startup --python-exit-code 1 --python tests/test_overlay_blender.py
blender --background --factory-startup --python-exit-code 1 --python tests/blender_integration.py
```

For integration testing outside this Windows/Blender 5.2 layout, set
`BPD_BONSAI_SITE_PACKAGES` to the folder containing Bonsai and its installed
dependencies. Tests use a separate factory-startup process; they do not save
preferences or install the extension into your configuration.

Implementation references:

- [Bonsai documentation](https://docs.bonsaibim.org/)
- [Grid curve API](https://docs.ifcopenshell.org/autoapi/ifcopenshell/api/grid/create_axis_curve/index.html)
- [Placement API](https://docs.ifcopenshell.org/autoapi/ifcopenshell/api/geometry/edit_object_placement/index.html)
- [Blender releases](https://www.blender.org/releases/)

This independent add-on is not an official IfcOpenShell or Bonsai release.
