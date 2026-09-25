# Bonsai IFC Grid Toolbar 1.1

For Blender 5.2 and Bonsai 0.8.5.

## Compact controls

The left toolbar has one **IFC Grid** button. It opens **New Grid**, **Draw Line**, and a small **▼** options menu. The same compact controls appear in **N → IFC Grid**. Press **T** if the left toolbar is hidden.

**▼** contains Offset Axis, Rename Axis, Top Plan, the new-axis family and label, snap distance, and display options.

## Stretch one end

1. Select a straight IFC grid axis in Object Mode.
2. Drag the orange ring around either end bubble and release it at the desired length.
3. The opposite endpoint stays fixed. The selected endpoint slides along the existing axis, preserving its direction and IFC label.

Hold **Ctrl** to snap the total axis length to the configured step. **Esc** or right-click cancels without changing the geometry. **Ctrl+Z** undoes a completed stretch. An endpoint cannot cross the opposite end; the minimum remaining length is 1 mm.

Endpoint handles require Blender's **Show Gizmos** and **Show Overlays** toggles. If grids are locked, click **Unlock Grids to Stretch** in the compact panel. If looking directly down the length of an axis, use **▼ → Top Plan** to see and drag its ends. Existing curved axes are not stretched by this tool.

## Create a grid

1. Open or create an IFC project and set the default spatial container in Bonsai.
2. Click **IFC Grid → New Grid**, then set row/column counts and spacing.
3. Click **Create Grid**. The grid starts at the 3D cursor's XY location and the default container's elevation.

**More settings** reveals starting labels, unequal gaps, end extension, rotation, origin and elevation offset. Labels default to **A, B, C…** and **1, 2, 3…**. **01** preserves leading zeroes; letters continue from **Z** to **AA**. Complete comma-separated label lists are also accepted.

Custom gaps are in metres: `6, 4.5, 6` gives three gaps for four axes. A single gap repeats; blank fields use the regular spacing setting. More settings retains its values when collapsed.

## Draw and edit lines

Select a grid or axis, then choose **Draw Line** and click the two endpoints. Drawing automatically aligns to the grid's plan view when necessary. Hold **Shift** to constrain to the chosen axis family or **Ctrl** to snap to the grid plane's increment. **Esc** cancels.

Choose U (letters) or V (numbers) and an optional next label in **▼**. A new grid must have both U and V axes so it can be reopened from IFC. Leave the label blank for automatic numbering.

**▼ → Offset Axis** opens a small distance-and-label dialog. Positive distance offsets to the left when looking from the first endpoint toward the second. **▼ → Rename Axis** changes the IFC label and Blender name. Duplicate labels in a grid are rejected.

## Saving and display

Save the IFC through Bonsai after creating or editing grid geometry. Save the `.blend` to retain toolbar preferences. The tool updates real `IfcGridAxis` curves, including one-end stretches; label bubbles and dashed highlighting are viewport decorations. Printed drawing styles remain controlled by Bonsai.

To reinstall, use **Preferences → Add-ons → Install from Disk** with `bonsai_grid_toolbar-1.1.0.zip`. Disable **Bonsai IFC Grid Toolbar** to remove its interface and decorations; IFC geometry remains.
