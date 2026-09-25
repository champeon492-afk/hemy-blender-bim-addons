# Workplane Toolkit

A Blender add-on for setting a modeling workplane from a selected face, the 3D cursor, your view, or the world XY / XZ / YZ planes.

## Install

1. In Blender, open **Edit > Preferences > Get Extensions**.
2. Open the menu at the top right and choose **Install from Disk**.
3. Select **workplane_toolkit-1.0.0.zip**, confirm installation, and enable **Workplane Toolkit** if it is not already enabled.
4. In a 3D Viewport, press **N** to open the sidebar, then select the **Workplane** tab.

The extension declares Blender 4.2 or newer. It was tested in Blender **5.2.1 LTS**; earlier versions have not been run here. No network access or extra Python dependencies are required.

## Set a workplane

| Control | Result |
| --- | --- |
| From Selected Face | In mesh Edit Mode, uses the active selected face center and normal. If only one face is selected, it can be used without an active face. |
| 3D Cursor | Uses the cursor's existing position and rotation. |
| Current View | Uses the current view orientation, with the origin at the 3D cursor. Exit Quad View before using this sidebar command. |
| XY / XZ / YZ | Sets that world plane at the world origin. |
| Origin / Rotation, then Apply | Sets a precise position and angle. Rotation fields use Blender's angle display. |

Face alignment works with rotated objects and nonuniform scale. Local X follows the face's longest transformed edge; local Z follows its transformed normal. A non-planar face supplies a representative plane at its center. The tool rejects collapsed faces and zero-scale objects.

## Model on the plane

Setting the workplane moves and rotates the **3D cursor** and chooses **Cursor** as the transform orientation. The red and green grid axes are workplane X and Y; workplane Z is perpendicular to the plane.

- **G, Shift Z** moves your selection within the workplane.
- **G, Z** moves perpendicular to it.
- **R, Z** rotates within it.
- **Look at Workplane** aligns the current viewport to an orthographic view of the plane.
- **Add Plane** creates a mesh plane aligned to the applied workplane. In Edit Mode, it adds geometry to the current mesh.
- **Pivot at Workplane Origin** controls whether rotate/scale use the workplane origin. Click **Apply / Reapply Workplane** after changing this option.
- **Restore Previous Setup** disables the workplane and restores the cursor, orientation, and pivot saved before the first Set. Setting another plane does not overwrite this backup.

You can adjust grid spacing, extent, and opacity or hide the grid. The viewport's **Show Overlays** toggle also hides it. Settings and the restoration backup are stored per scene and saved in the `.blend` file. Setting, applying, adding geometry, and restoring support Blender undo.

## Behavior to know

The grid is a viewport overlay; Blender's standard floor and grid remain available. Grid spacing is visual and does **not** enable grid snapping. Existing geometry stays unchanged when setting a workplane, and transforms only stay in the plane when you constrain them as above. The standard Add menu retains Blender's normal behavior; use the add-on's **Add Plane** button for explicit plane alignment.

The stored workplane stays where you set it; it does not follow later object movement or deformation. Origin and rotation edits take effect when you click **Apply**. Moving the cursor or changing the transform orientation separately can detach the transform axes from the stored grid; the panel shows a reminder to reapply. **Look at Workplane** changes the viewport and is not reversed by **Restore Previous Setup**. If you delete an original custom orientation while the workplane is active, restoration falls back to Global.

## Files and license

`__init__.py` contains the complete add-on. `blender_manifest.toml` describes the extension package. Source is provided under **GPL-3.0-or-later**; see `LICENSE`.

Blender references: [extension installation and packaging](https://docs.blender.org/manual/en/4.2/advanced/extensions/getting_started.html), [3D cursor API](https://docs.blender.org/api/4.2/bpy.types.View3DCursor.html), [Cursor transform orientation](https://docs.blender.org/manual/en/4.2/editors/3dview/controls/orientation.html).
