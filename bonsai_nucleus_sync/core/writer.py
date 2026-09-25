"""Author only a source-owned USD subtree, always on the host main thread."""
from pxr import Gf, Sdf, Usd, UsdGeom, Vt
from .protocol import validate

ROOT = '/World/BonsaiSync'


class StageWriter:
    def __init__(self, stage, layer):
        self.stage = stage
        self.layer = layer
        self.streams = {}

    def apply(self, packet):
        validate(packet)  # Validate everything before the first USD mutation.
        source, stream, seq = packet['source'], packet['stream'], packet['seq']
        previous = self.streams.get(source)
        if previous and previous[0] == stream:
            if seq == previous[1]:
                return {'seq': seq, 'duplicate': True}
            if seq != previous[1] + 1:
                raise ValueError('Sequence gap: reconnect with a full snapshot.')
        elif not packet['full'] or seq != 1:
            raise ValueError('New stream requires full snapshot sequence 1.')
        scope_path = ROOT + '/s_' + source
        scope = self.stage.GetPrimAtPath(scope_path)
        if scope and scope.GetCustomDataByKey('bonsai:source') != source:
            raise ValueError('Source namespace is occupied by unrelated USD content.')
        # New objects in deltas must include geometry, even if they are empties.
        for obj in packet['objects']:
            if 'mesh' not in obj and not self.stage.GetPrimAtPath(scope_path + '/o_' + obj['id']):
                raise ValueError('New object requires mesh data or null.')
        # Never use Sdf.ChangeBlock around Usd queries/Define calls: composition
        # must remain current. Explicit EditContext leaves the user's target intact.
        with Usd.EditContext(self.stage, self.layer):
            scope = UsdGeom.Xform.Define(self.stage, scope_path).GetPrim()
            scope.SetCustomDataByKey('bonsai:source', source)
            scope.SetCustomDataByKey('bonsai:protocol', 1)
            xform = UsdGeom.Xformable(scope)
            transform = xform.MakeMatrixXform()
            scale = packet['meters_per_unit'] / UsdGeom.GetStageMetersPerUnit(self.stage)
            conversion = Gf.Matrix4d().SetScale(scale)
            if UsdGeom.GetStageUpAxis(self.stage) == UsdGeom.Tokens.y:
                conversion = conversion * Gf.Matrix4d().SetRotate(Gf.Rotation(Gf.Vec3d(1, 0, 0), -90))
            transform.Set(conversion)
            # Blender matrices are world-space: ignore external parent transforms.
            xform.SetResetXformStack(True)
            incoming = {'o_' + o['id'] for o in packet['objects']}
            removed = set(packet['removed'])
            if packet['full']:
                removed.update(p.GetName()[2:] for p in scope.GetAllChildren()
                               if p.GetName().startswith('o_') and p.GetName() not in incoming)
            for oid in removed:
                prim = self.stage.GetPrimAtPath(scope_path + '/o_' + oid)
                if prim:
                    # Deactivation hides weaker-layer prims too, including after merge.
                    prim.SetActive(False)
            for obj in packet['objects']:
                path = scope_path + '/o_' + obj['id']
                prim = self.stage.GetPrimAtPath(path)
                if prim:
                    prim.SetActive(True)
                prim = UsdGeom.Xform.Define(self.stage, path).GetPrim()
                prim.SetDisplayName(obj['name'])
                prim.SetCustomDataByKey('bonsai:object', obj['id'])
                prim.SetCustomDataByKey('ifc', obj.get('ifc', {}))
                # Wire matrix is Blender row-major, translation in the last column.
                UsdGeom.Xformable(prim).MakeMatrixXform().Set(Gf.Matrix4d(*obj['matrix']).GetTranspose())
                UsdGeom.Imageable(prim).CreateVisibilityAttr().Set(
                    UsdGeom.Tokens.inherited if obj['visible'] else UsdGeom.Tokens.invisible)
                if 'mesh' in obj:
                    self._mesh(path + '/Geometry', obj['mesh'])
        self.streams[source] = (stream, seq)
        return {'seq': seq, 'objects': len(packet['objects']), 'removed': len(removed)}

    def _mesh(self, path, data):
        prim = self.stage.GetPrimAtPath(path)
        if data is None:
            if prim:
                prim.SetActive(False)
            return
        if prim:
            prim.SetActive(True)
        mesh = UsdGeom.Mesh.Define(self.stage, path)
        points = Vt.Vec3fArray([Gf.Vec3f(*p) for p in data['points']])
        mesh.CreatePointsAttr().Set(points)
        mesh.CreateFaceVertexCountsAttr().Set(Vt.IntArray(data['counts']))
        mesh.CreateFaceVertexIndicesAttr().Set(Vt.IntArray(data['indices']))
        mesh.CreateSubdivisionSchemeAttr().Set(UsdGeom.Tokens.none)
        mesh.CreateDoubleSidedAttr().Set(True)
        mesh.CreateExtentAttr().Set(UsdGeom.PointBased.ComputeExtent(points) if len(points) else
                                    Vt.Vec3fArray([Gf.Vec3f(0), Gf.Vec3f(0)]))
        mesh.CreateDisplayColorPrimvar(UsdGeom.Tokens.uniform).Set(
            Vt.Vec3fArray([Gf.Vec3f(*v) for v in data['colors']]))
        mesh.CreateDisplayOpacityPrimvar(UsdGeom.Tokens.uniform).Set(Vt.FloatArray(data['opacity']))
        normals = data.get('normals', [])
        if normals:
            mesh.CreateNormalsAttr().Set(Vt.Vec3fArray([Gf.Vec3f(*v) for v in normals]))
            mesh.SetNormalsInterpolation(UsdGeom.Tokens.faceVarying)
        else:
            mesh.CreateNormalsAttr().Block()
        uv = UsdGeom.PrimvarsAPI(mesh).CreatePrimvar('st', Sdf.ValueTypeNames.TexCoord2fArray,
                                                   UsdGeom.Tokens.faceVarying)
        uv.Set(Vt.Vec2fArray([Gf.Vec2f(*v) for v in data.get('uv', [])]))
