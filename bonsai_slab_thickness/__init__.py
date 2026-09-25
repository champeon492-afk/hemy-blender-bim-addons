# SPDX-License-Identifier: GPL-3.0-or-later
"""Type-specific slab, wall, and column size controls for Bonsai."""

bl_info = {
    "name": "Bonsai Slab, Wall and Column Sizes",
    "author": "Codex",
    "version": (1, 3, 0),
    "blender": (4, 2, 0),
    "location": "3D View > Slab, Wall or Column Tool > IFC Tool",
    "description": "Set type-specific slab and wall thickness or column profile size",
    "category": "3D View",
}

import bpy
import bonsai.tool as tool
from bpy.props import FloatProperty, IntProperty

_original_create_draw = None
_original_edit_draw = None
_patched_create_draw = None
_patched_edit_draw = None


def _slab_type(type_id):
    file = tool.Ifc.get()
    if not file or not type_id:
        return None
    try:
        entity = file.by_id(type_id)
    except RuntimeError:
        return None
    return entity if entity.is_a("IfcSlabType") else None


def _wall_type(type_id):
    file = tool.Ifc.get()
    if not file or not type_id:
        return None
    try:
        entity = file.by_id(type_id)
    except RuntimeError:
        return None
    return entity if entity.is_a("IfcWallType") else None


def _column_type(type_id):
    file = tool.Ifc.get()
    if not file or not type_id:
        return None
    try:
        entity = file.by_id(type_id)
    except RuntimeError:
        return None
    return entity if entity.is_a("IfcColumnType") else None


def _type_layers(slab_type):
    import ifcopenshell.util.element

    if slab_type is None:
        return None, ()
    layer_set = ifcopenshell.util.element.get_material(slab_type, should_inherit=False)
    if not layer_set or not layer_set.is_a("IfcMaterialLayerSet"):
        return None, ()
    return layer_set, tuple(layer_set.MaterialLayers or ())


def _can_initialize_wall_layer(wall_type):
    import ifcopenshell.util.element

    if wall_type is None:
        return False
    material = ifcopenshell.util.element.get_material(wall_type, should_inherit=False)
    return material is None or material.is_a("IfcMaterial")


def _initialize_wall_layer(file, wall_type, thickness):
    import ifcopenshell.api.material
    import ifcopenshell.util.element

    material = ifcopenshell.util.element.get_material(wall_type, should_inherit=False)
    if material is None:
        material = ifcopenshell.api.material.add_material(
            file, name=f"{wall_type.Name or 'Wall'} Core"
        )
    relation = ifcopenshell.api.material.assign_material(
        file, products=[wall_type], type="IfcMaterialLayerSet"
    )
    layer = ifcopenshell.api.material.add_layer(
        file, layer_set=relation.RelatingMaterial, material=material
    )
    ifcopenshell.api.material.edit_layer(
        file, layer=layer, attributes={"LayerThickness": thickness}
    )
    return layer


def _selected_type_id():
    from bonsai.bim.module.model.data import AuthoringData

    return int(AuthoringData.data.get("relating_type_data", {}).get("id") or 0)


def _active_slab_type_id(context):
    import ifcopenshell.util.element

    obj = context.active_object
    element = tool.Ifc.get_entity(obj) if obj else None
    if not element or not element.is_a("IfcSlab"):
        return 0
    slab_type = ifcopenshell.util.element.get_type(element)
    return slab_type.id() if slab_type and slab_type.is_a("IfcSlabType") else 0


def _active_wall_type_id(context):
    import ifcopenshell.util.element

    obj = context.active_object
    element = tool.Ifc.get_entity(obj) if obj else None
    if not element or not element.is_a("IfcWall"):
        return 0
    wall_type = ifcopenshell.util.element.get_type(element)
    return wall_type.id() if wall_type and wall_type.is_a("IfcWallType") else 0


def _active_column_type_id(context):
    import ifcopenshell.util.element

    obj = context.active_object
    element = tool.Ifc.get_entity(obj) if obj else None
    if not element or not element.is_a("IfcColumn"):
        return 0
    column_type = ifcopenshell.util.element.get_type(element)
    return column_type.id() if column_type and column_type.is_a("IfcColumnType") else 0


def _column_profile(column_type):
    import ifcopenshell.util.element

    if column_type is None:
        return None, None, None
    profile_set = ifcopenshell.util.element.get_material(column_type, should_inherit=False)
    if not profile_set or not profile_set.is_a("IfcMaterialProfileSet"):
        return None, None, None
    if profile_set.CompositeProfile or len(profile_set.MaterialProfiles or ()) != 1:
        return profile_set, None, None
    material_profile = profile_set.MaterialProfiles[0]
    return profile_set, material_profile, material_profile.Profile


def _column_profile_kind(profile):
    if profile is None:
        return None
    if profile.is_a() == "IfcRectangleProfileDef":
        return "RECTANGLE"
    if profile.is_a() == "IfcCircleProfileDef":
        return "CIRCLE"
    return None


def _can_initialize_column_profile(column_type):
    import ifcopenshell.util.element

    if column_type is None:
        return False
    material = ifcopenshell.util.element.get_material(column_type, should_inherit=False)
    return material is None or material.is_a("IfcMaterial")


def _initialize_column_profile(file, column_type, width, depth):
    import ifcopenshell.api.material
    import ifcopenshell.util.element

    material = ifcopenshell.util.element.get_material(column_type, should_inherit=False)
    if material is None:
        material = ifcopenshell.api.material.add_material(
            file, name=f"{column_type.Name or 'Column'} Core"
        )
    profile = file.create_entity(
        "IfcRectangleProfileDef", ProfileType="AREA", XDim=width, YDim=depth
    )
    relation = ifcopenshell.api.material.assign_material(
        file, products=[column_type], type="IfcMaterialProfileSet"
    )
    ifcopenshell.api.material.add_profile(
        file, profile_set=relation.RelatingMaterial, material=material, profile=profile
    )
    return profile


def _profile_set_is_shared(file, column_type, profile_set):
    return any(
        inverse.is_a("IfcRelAssociatesMaterial")
        and any(product != column_type for product in inverse.RelatedObjects)
        for inverse in file.get_inverse(profile_set)
    )


def _isolate_column_profile_set(file, column_type, profile_set):
    """Move this type and its column usages to a private copy of a shared set."""
    import ifcopenshell.api.material
    import ifcopenshell.util.element

    new_set = ifcopenshell.api.material.copy_material(file, material=profile_set)
    for relation in tuple(column_type.HasAssociations):
        if relation.is_a("IfcRelAssociatesMaterial") and relation.RelatingMaterial == profile_set:
            _split_material_relation(file, relation, {column_type}, new_set)

    usages = {}
    for occurrence in ifcopenshell.util.element.get_types(column_type):
        usage = ifcopenshell.util.element.get_material(occurrence, should_inherit=False)
        if usage and usage.is_a("IfcMaterialProfileSetUsage") and usage.ForProfileSet == profile_set:
            usages.setdefault(usage, set()).add(occurrence)

    for usage, chosen in usages.items():
        relations = tuple(
            inverse for inverse in file.get_inverse(usage) if inverse.is_a("IfcRelAssociatesMaterial")
        )
        all_products = {product for relation in relations for product in relation.RelatedObjects}
        if all_products <= chosen:
            usage.ForProfileSet = new_set
        else:
            new_usage = ifcopenshell.util.element.copy(file, usage)
            new_usage.ForProfileSet = new_set
            for relation in relations:
                _split_material_relation(file, relation, chosen, new_usage)
    return new_set


def _split_material_relation(file, relation, targets, material):
    """Associate only targets with material, keeping all other products intact."""
    import ifcopenshell.util.element

    chosen = tuple(obj for obj in relation.RelatedObjects if obj in targets)
    if not chosen:
        return
    remaining = tuple(obj for obj in relation.RelatedObjects if obj not in targets)
    if remaining:
        new_relation = ifcopenshell.util.element.copy(file, relation)
        new_relation.RelatedObjects = chosen
        new_relation.RelatingMaterial = material
        relation.RelatedObjects = remaining
    else:
        relation.RelatingMaterial = material


def _material_is_shared(file, slab_type, layer_set, layer):
    for inverse in file.get_inverse(layer_set):
        if inverse.is_a("IfcRelAssociatesMaterial") and any(
            product != slab_type for product in inverse.RelatedObjects
        ):
            return True
    return any(other != layer_set for other in layer.ToMaterialLayerSet)


def _isolate_type_layer(file, slab_type, layer_set):
    """Copy a shared layer set and move only this type's usages to the copy."""
    import ifcopenshell.api.material
    import ifcopenshell.util.element

    new_set = ifcopenshell.api.material.copy_material(file, material=layer_set)

    # Material associations can contain multiple types. Split such relations.
    for relation in tuple(slab_type.HasAssociations):
        if relation.is_a("IfcRelAssociatesMaterial") and relation.RelatingMaterial == layer_set:
            _split_material_relation(file, relation, {slab_type}, new_set)

    occurrences = set(ifcopenshell.util.element.get_types(slab_type))
    usages = {}
    for occurrence in occurrences:
        usage = ifcopenshell.util.element.get_material(occurrence, should_inherit=False)
        if usage and usage.is_a("IfcMaterialLayerSetUsage") and usage.ForLayerSet == layer_set:
            usages.setdefault(usage, set()).add(occurrence)

    for usage, chosen in usages.items():
        relations = tuple(
            inverse for inverse in file.get_inverse(usage) if inverse.is_a("IfcRelAssociatesMaterial")
        )
        all_products = {product for rel in relations for product in rel.RelatedObjects}
        if all_products <= chosen:
            usage.ForLayerSet = new_set
        else:
            new_usage = ifcopenshell.util.element.copy(file, usage)
            new_usage.ForLayerSet = new_set
            for relation in relations:
                _split_material_relation(file, relation, chosen, new_usage)
    return new_set, new_set.MaterialLayers[0]


class BIM_OT_slab_thickness(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.slab_thickness"
    bl_label = "Slab Type Thickness"
    bl_description = "Set thickness for this slab type and regenerate its existing slabs"
    bl_options = {"REGISTER", "UNDO"}

    type_id: IntProperty(options={"HIDDEN"})
    thickness: FloatProperty(
        name="Thickness",
        description="Thickness assigned to this slab type",
        subtype="DISTANCE",
        unit="LENGTH",
        min=0.000001,
        precision=4,
    )

    @classmethod
    def poll(cls, _context):
        return tool.Ifc.get() is not None

    def draw(self, _context):
        slab_type = _slab_type(self.type_id)
        self.layout.label(text=f"Type: {slab_type.Name or slab_type.id()}")
        self.layout.prop(self, "thickness")

    def invoke(self, context, _event):
        import ifcopenshell.util.unit

        _layer_set, layers = _type_layers(_slab_type(self.type_id))
        if len(layers) != 1:
            self.report({"ERROR"}, "This slab type needs one material layer; edit multilayer types in Material Layers")
            return {"CANCELLED"}
        self.thickness = layers[0].LayerThickness * ifcopenshell.util.unit.calculate_unit_scale(tool.Ifc.get())
        return context.window_manager.invoke_props_dialog(self)

    def _execute(self, _context):
        from bonsai.bim.module.model.slab import DumbSlabPlaner
        import ifcopenshell.api.material
        import ifcopenshell.util.unit

        slab_type = _slab_type(self.type_id)
        layer_set, layers = _type_layers(slab_type)
        if len(layers) != 1:
            self.report({"ERROR"}, "Select a single-layer slab type")
            return {"CANCELLED"}
        if self.thickness <= 0:
            self.report({"ERROR"}, "Thickness must be greater than zero")
            return {"CANCELLED"}

        file = tool.Ifc.get()
        layer = layers[0]
        if _material_is_shared(file, slab_type, layer_set, layer):
            layer_set, layer = _isolate_type_layer(file, slab_type, layer_set)

        ifcopenshell.api.material.edit_layer(
            file,
            layer=layer,
            attributes={"LayerThickness": self.thickness / ifcopenshell.util.unit.calculate_unit_scale(file)},
        )
        DumbSlabPlaner().regenerate_from_layer(layer)
        self.report({"INFO"}, f"Updated thickness for slab type {slab_type.Name or slab_type.id()}")
        return {"FINISHED"}


class BIM_OT_wall_thickness(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.wall_type_thickness"
    bl_label = "Wall Type Thickness"
    bl_description = "Set thickness for this wall type and regenerate its existing walls"
    bl_options = {"REGISTER", "UNDO"}

    type_id: IntProperty(options={"HIDDEN"})
    thickness: FloatProperty(
        name="Thickness",
        description="Thickness assigned to this wall type",
        subtype="DISTANCE",
        unit="LENGTH",
        min=0.000001,
        precision=4,
    )

    @classmethod
    def poll(cls, _context):
        return tool.Ifc.get() is not None

    def draw(self, _context):
        wall_type = _wall_type(self.type_id)
        self.layout.label(text=f"Type: {wall_type.Name or wall_type.id()}")
        self.layout.prop(self, "thickness")

    def invoke(self, context, _event):
        import ifcopenshell.util.unit

        wall_type = _wall_type(self.type_id)
        _layer_set, layers = _type_layers(wall_type)
        if not layers and _can_initialize_wall_layer(wall_type):
            self.thickness = 0.1
        elif len(layers) != 1:
            self.report({"ERROR"}, "This wall type needs one material layer; edit multilayer types in Material Layers")
            return {"CANCELLED"}
        else:
            self.thickness = layers[0].LayerThickness * ifcopenshell.util.unit.calculate_unit_scale(tool.Ifc.get())
        return context.window_manager.invoke_props_dialog(self)

    def _execute(self, _context):
        from bonsai.bim.module.model.wall import DumbWallPlaner
        import ifcopenshell.api.material
        import ifcopenshell.util.unit

        wall_type = _wall_type(self.type_id)
        layer_set, layers = _type_layers(wall_type)
        if self.thickness <= 0:
            self.report({"ERROR"}, "Thickness must be greater than zero")
            return {"CANCELLED"}

        file = tool.Ifc.get()
        thickness = self.thickness / ifcopenshell.util.unit.calculate_unit_scale(file)
        if not layers and _can_initialize_wall_layer(wall_type):
            layer = _initialize_wall_layer(file, wall_type, thickness)
        elif len(layers) == 1:
            layer = layers[0]
            if _material_is_shared(file, wall_type, layer_set, layer):
                layer_set, layer = _isolate_type_layer(file, wall_type, layer_set)
            ifcopenshell.api.material.edit_layer(
                file, layer=layer, attributes={"LayerThickness": thickness}
            )
        else:
            self.report({"ERROR"}, "Select a wall type with one layer or no assigned material")
            return {"CANCELLED"}
        DumbWallPlaner().regenerate_from_layer(layer)
        self.report({"INFO"}, f"Updated thickness for wall type {wall_type.Name or wall_type.id()}")
        return {"FINISHED"}


class BIM_OT_column_profile_size(bpy.types.Operator, tool.Ifc.Operator):
    bl_idname = "bim.column_type_profile_size"
    bl_label = "Column Type Profile Size"
    bl_description = "Set this column type's profile size and regenerate its existing columns"
    bl_options = {"REGISTER", "UNDO"}

    type_id: IntProperty(options={"HIDDEN"})
    width: FloatProperty(name="Width", subtype="DISTANCE", unit="LENGTH", min=0.000001, precision=4)
    depth: FloatProperty(name="Depth", subtype="DISTANCE", unit="LENGTH", min=0.000001, precision=4)
    diameter: FloatProperty(name="Diameter", subtype="DISTANCE", unit="LENGTH", min=0.000001, precision=4)

    @classmethod
    def poll(cls, _context):
        return tool.Ifc.get() is not None

    def draw(self, _context):
        column_type = _column_type(self.type_id)
        self.layout.label(text=f"Type: {column_type.Name or column_type.id()}")
        _profile_set, _material_profile, profile = _column_profile(column_type)
        if _column_profile_kind(profile) == "CIRCLE":
            self.layout.prop(self, "diameter")
        else:
            self.layout.prop(self, "width")
            self.layout.prop(self, "depth")

    def invoke(self, context, _event):
        import ifcopenshell.util.unit

        column_type = _column_type(self.type_id)
        _profile_set, _material_profile, profile = _column_profile(column_type)
        kind = _column_profile_kind(profile)
        if kind is None and not _can_initialize_column_profile(column_type):
            self.report({"ERROR"}, "This column type needs one rectangular or circular profile")
            return {"CANCELLED"}
        scale = ifcopenshell.util.unit.calculate_unit_scale(tool.Ifc.get())
        if kind == "RECTANGLE":
            self.width = profile.XDim * scale
            self.depth = profile.YDim * scale
        elif kind == "CIRCLE":
            self.diameter = 2 * profile.Radius * scale
        else:
            self.width = self.depth = 0.2
        return context.window_manager.invoke_props_dialog(self)

    def _execute(self, _context):
        from bonsai.bim.module.model.profile import DumbProfileRecalculator
        import ifcopenshell.api.profile
        import ifcopenshell.util.element
        import ifcopenshell.util.unit

        column_type = _column_type(self.type_id)
        profile_set, material_profile, profile = _column_profile(column_type)
        kind = _column_profile_kind(profile)
        if kind is None and not _can_initialize_column_profile(column_type):
            self.report({"ERROR"}, "Select a column type with one rectangular or circular profile")
            return {"CANCELLED"}
        if (kind == "CIRCLE" and self.diameter <= 0) or (
            kind != "CIRCLE" and (self.width <= 0 or self.depth <= 0)
        ):
            self.report({"ERROR"}, "Profile dimensions must be greater than zero")
            return {"CANCELLED"}

        file = tool.Ifc.get()
        scale = ifcopenshell.util.unit.calculate_unit_scale(file)
        if kind is None:
            profile = _initialize_column_profile(file, column_type, self.width / scale, self.depth / scale)
        else:
            if _profile_set_is_shared(file, column_type, profile_set):
                profile_set = _isolate_column_profile_set(file, column_type, profile_set)
                material_profile = profile_set.MaterialProfiles[0]
            # The shape may also be referenced by another material set or body representation.
            # Give this type its own profile before changing its dimensions.
            profile = ifcopenshell.api.profile.copy_profile(file, profile=profile)
            material_profile.Profile = profile
            if kind == "CIRCLE":
                attributes = {"Radius": self.diameter / (2 * scale)}
            else:
                attributes = {"XDim": self.width / scale, "YDim": self.depth / scale}
            ifcopenshell.api.profile.edit_profile(file, profile=profile, attributes=attributes)

        objs = [
            obj for occurrence in ifcopenshell.util.element.get_types(column_type)
            if (obj := tool.Ifc.get_object(occurrence))
        ]
        DumbProfileRecalculator().recalculate(objs)
        tool.Model.mark_thumbnail_for_update(column_type)
        self.report({"INFO"}, f"Updated profile size for column type {column_type.Name or column_type.id()}")
        return {"FINISHED"}


def _draw_type_thickness(layout, context, type_id, type_class):
    import ifcopenshell.util.unit

    type_entity = _slab_type(type_id) if type_class == "IfcSlabType" else _wall_type(type_id)
    _layer_set, layers = _type_layers(type_entity)
    if not layers:
        if type_class == "IfcWallType" and _can_initialize_wall_layer(type_entity):
            row = layout.row(align=True)
            row.operator(
                "bim.wall_type_thickness", text="Thickness: set for this type", icon="MOD_SOLIDIFY"
            ).type_id = type_id
        return
    row = layout.row(align=True)
    if len(layers) != 1:
        row.label(text="Thickness: edit material layers", icon="INFO")
        return
    metres = layers[0].LayerThickness * ifcopenshell.util.unit.calculate_unit_scale(tool.Ifc.get())
    system = context.scene.unit_settings.system
    shown = bpy.utils.units.to_string(system if system != "NONE" else "METRIC", "LENGTH", metres, precision=3)
    operator = "bim.slab_thickness" if type_class == "IfcSlabType" else "bim.wall_type_thickness"
    row.operator(operator, text=f"Thickness: {shown}", icon="MOD_SOLIDIFY").type_id = type_id


def _draw_column_profile_size(layout, context, type_id):
    import ifcopenshell.util.unit

    column_type = _column_type(type_id)
    _profile_set, _material_profile, profile = _column_profile(column_type)
    kind = _column_profile_kind(profile)
    row = layout.row(align=True)
    if kind is None:
        if _can_initialize_column_profile(column_type):
            row.operator(
                "bim.column_type_profile_size", text="Profile size: set for this type", icon="MESH_CUBE"
            ).type_id = type_id
        elif column_type is not None:
            row.label(text="Profile size: edit profile", icon="INFO")
        return

    scale = ifcopenshell.util.unit.calculate_unit_scale(tool.Ifc.get())
    system = context.scene.unit_settings.system
    def shown(value):
        return bpy.utils.units.to_string(
            system if system != "NONE" else "METRIC", "LENGTH", value * scale, precision=3
        )
    if kind == "RECTANGLE":
        label = f"Profile: {shown(profile.XDim)} x {shown(profile.YDim)}"
    else:
        label = f"Profile: Ø {shown(2 * profile.Radius)}"
    row.operator("bim.column_type_profile_size", text=label, icon="MESH_CUBE").type_id = type_id


def _draw_create_parameters(cls, context):
    _original_create_draw.__get__(None, cls)(context)
    from bonsai.bim.module.model.data import AuthoringData

    type_class = AuthoringData.data.get("ifc_class_current")
    if type_class in ("IfcSlabType", "IfcWallType"):
        _draw_type_thickness(cls.layout, context, _selected_type_id(), type_class)
    elif type_class == "IfcColumnType":
        _draw_column_profile_size(cls.layout, context, _selected_type_id())


def _draw_edit_parameters(cls, context):
    _original_edit_draw.__get__(None, cls)(context)
    from bonsai.bim.module.model.data import AuthoringData

    active_class = AuthoringData.data.get("active_class") or ""
    if active_class.startswith("IfcSlab"):
        _draw_type_thickness(cls.layout, context, _active_slab_type_id(context), "IfcSlabType")
    elif active_class.startswith("IfcWall"):
        _draw_type_thickness(cls.layout, context, _active_wall_type_id(context), "IfcWallType")
    elif active_class.startswith("IfcColumn"):
        _draw_column_profile_size(cls.layout, context, _active_column_type_id(context))


def register():
    global _original_create_draw, _original_edit_draw, _patched_create_draw, _patched_edit_draw
    from bonsai.bim.module.model.workspace import CreateObjectUI, EditObjectUI

    bpy.utils.register_class(BIM_OT_slab_thickness)
    bpy.utils.register_class(BIM_OT_wall_thickness)
    bpy.utils.register_class(BIM_OT_column_profile_size)
    _original_create_draw = CreateObjectUI.__dict__["draw_add_object_parameters"]
    _original_edit_draw = EditObjectUI.__dict__["draw_parameter_adjustments"]
    _patched_create_draw = classmethod(_draw_create_parameters)
    _patched_edit_draw = classmethod(_draw_edit_parameters)
    CreateObjectUI.draw_add_object_parameters = _patched_create_draw
    EditObjectUI.draw_parameter_adjustments = _patched_edit_draw


def unregister():
    from bonsai.bim.module.model.workspace import CreateObjectUI, EditObjectUI

    global _original_create_draw, _original_edit_draw, _patched_create_draw, _patched_edit_draw
    if CreateObjectUI.__dict__.get("draw_add_object_parameters") is _patched_create_draw:
        CreateObjectUI.draw_add_object_parameters = _original_create_draw
    if EditObjectUI.__dict__.get("draw_parameter_adjustments") is _patched_edit_draw:
        EditObjectUI.draw_parameter_adjustments = _original_edit_draw
    _original_create_draw = _original_edit_draw = _patched_create_draw = _patched_edit_draw = None
    bpy.utils.unregister_class(BIM_OT_column_profile_size)
    bpy.utils.unregister_class(BIM_OT_wall_thickness)
    bpy.utils.unregister_class(BIM_OT_slab_thickness)
