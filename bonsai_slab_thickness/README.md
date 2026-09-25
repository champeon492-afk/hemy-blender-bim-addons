# Bonsai Slab, Wall and Column Sizes

Adds a **Thickness** control to the Slab Tool and Wall Tool for the selected single-layer type. The control appears beside the type's creation parameters and in Parameter Adjustments when an existing slab or wall is selected.

The value is entered in Blender's scene length units. Each slab or wall type keeps its own thickness. When another type is selected, the control shows that type's current thickness, and new elements drawn from it use that value. Editing a type regenerates its existing elements. If two types share a material layer set, the edited type first gets its own copy, preserving the other type's thickness and occurrence offsets. A wall type without a material layer shows **Thickness: set for this type**; setting a value creates a single material layer for that wall type. Multilayer types continue to use Bonsai's Material Layers editor, because their individual layer thicknesses need to be chosen separately.

The **Column Tool** has a **Profile** control for each column type. Rectangular profiles expose width and depth; circular profiles expose diameter. Changing one type regenerates its existing columns. If profile sets or shapes are shared, the edited type receives its own copy. A column type without a profile shows **Profile size: set for this type**; setting it creates a rectangular profile for that type. Other profile shapes and composite profiles continue to use Bonsai's profile editor.

Tested for registration, slab/wall/column type isolation, and wall/column initialization against Blender 5.2.1 and Bonsai 0.8.5. The ZIP does not change sizes in an IFC project until you use a control.

Install the ZIP through Blender's **Edit → Preferences → Get Extensions → Install from Disk**, then enable **Bonsai Slab, Wall and Column Sizes** if prompted.
