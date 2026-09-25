# Bonsai Storey Toolbar

Choose an IFC building storey and work in a locked orthographic plan at its elevation. Use **3D View** to resume orbiting and perspective. Version 1.2 fixes blank clipped plans, reads hidden storey placements correctly, and displays filled mesh sections at range limits.

## Use

The controls appear in the 3D Viewport's tool header, directly below its main header. They are also available in **N sidebar > IFC Storeys**.

1. Open an IFC model in Bonsai.
2. Click the storey name / **Select Storey**, then choose a floor. The menu reads `IfcBuildingStorey` entities from the current IFC project and sorts them by elevation.
3. The selected floor becomes Bonsai's default spatial container. The cursor, modelling axes and optional Workplane Toolkit grid move to its world placement. The view looks perpendicular to that plane, in orthographic projection.
4. **Plan Locked** prevents orbiting, axis-view shortcuts and perspective/camera toggles. Panning, zooming and editing remain available.
5. **3D View** unlocks navigation and restores the previous view direction in perspective. It retains the selected storey, workplane and current pan/zoom. If the previous view was straight down, an oblique view is used.
6. Click **Plan Locked** or select another storey to resume plan work.

All ordinary 3D viewports showing the same scene in open Blender windows follow the selected plan. The storey selector updates when reopened; additional IFC storeys do not require reinstalling the add-on.

## View range

Click **View Range** beside **3D View**, or open **N sidebar > IFC Storeys > View Range**.

- **Enable View Range** clips geometry outside the lower and upper elevations. Geometry crossing a boundary is cut at that boundary. Disabling it restores the viewport's previous clipping.
- **Level to Level** is the default: selected floor to the next higher loaded floor in the same building. Equal-height duplicate levels are skipped. Switching floors updates these automatic limits.
- Choose different **Lower limit** and **Upper limit** reference levels if needed. Each has a positive or negative **Offset**, shown in the scene's length units.
- At the highest level, **Height** defines the upper limit above that level (initially 3 m).
- **Custom Elevations** provides absolute lower and upper values, initially copied from the current level-based range.
- **Reset to Level-to-Level** restores the automatic levels and zero offsets.
- The effective **Visible** elevations are shown below the controls. If the upper limit is not above the lower one, clipping is suspended and the panel explains the error.
- View range remains active in **3D View**, so you can orbit the same vertical slice.

View ranges use Solid or Wireframe shading and Object Mode, including Bonsai's object-based modelling tools. Other shading modes switch to Solid while the range is enabled; the previous mode returns when it is disabled. Range clipping pauses in Edit Mode or while Section Box/Bonsai clipping planes are active, and resumes when those controls are cleared. Changing the viewport clipping directly with Alt+B turns this range off. Existing object visibility flags and IFC geometry are never changed. View range settings are saved with the .blend file; temporary clipping planes are restored safely around saving and re-created after loading.

## Installation and removal

Install `bonsai_storey_toolbar-1.2.0.zip` using Blender **Edit > Preferences > Add-ons > Install from Disk**, then enable **Bonsai Storey Toolbar**. Save preferences if automatic preference saving is disabled. This is a regular Blender add-on and requires Bonsai to be enabled, including its bundled Shapely 2.1 dependency. Workplane Toolkit is optional.

To remove it, disable **Bonsai Storey Toolbar** in Preferences. Native view locks acquired by the add-on are released. The selected default storey and workplane remain available for modelling.

## Scope

- Tested with Blender 5.2.1 LTS and Bonsai 0.8.5.
- Storeys must be loaded as Blender objects. Unloaded storeys are shown disabled, because Bonsai requires their objects for placement elevation.
- Horizontal storeys can have a rotated XY orientation. Tilted levels are rejected because Bonsai's native wall/slab authoring uses a constant world-Z elevation.
- Exit Quad View before using the plan lock.
- View range affects viewport display only. It does not create IFC sections, change render visibility, generate drawing cut fills, or constrain arbitrary mesh edits.
- Cut faces are display overlays for closed mesh sections, including openings and disconnected parts. They do not add selectable geometry. Open/non-manifold meshes may not form a fillable section.
- Objects merely touching a range boundary are excluded using a tolerance of at most 0.01 mm. Display sections are sampled up to 1 mm inside the limits to avoid numerical cracks; native geometry uses the requested limits with that small clipping tolerance.
- The Workplane Toolkit's own panel may say its axes changed because this add-on uses a custom orientation required by Bonsai. Select the storey again to reapply this toolbar's setup.
- Save the IFC with Bonsai's normal save command when you want to save model edits. The toolbar does not save or overwrite your IFC file.

## Validation

Automated tests use actual IFC fixtures in a separate Blender process, with a small adapter for Bonsai's container/object lookup. They exercise elevation and rotation, custom modelling axes, floor switching, pan/zoom preservation, native orbit/perspective locks, 3D restoration, missing model/storey handling and add-on cleanup. The open Bonsai session was also checked for workplane placement and plan/3D switching.

Version 1.2 additionally tests excluded storey transforms, section area, holes, concave and disconnected geometry, range boundary contact, and cleanup. Live checks on the user's model verified l0 (0–3 m): 7 walls; l1 (3–6 m): 8 walls; l2 (6–9 m): 4 walls, 2 doors and 3 furniture objects. Custom 3.2–5.8 m and adjusted 3–5.99 m ranges included only the eight l1 walls. IFC contents and object visibility flags remained unchanged.

License: GPL-3.0-or-later.
