"""Versioned, bounded wire format. No file paths or executable code in updates."""
import math
import re
from urllib.parse import urlsplit, urlunsplit, quote, unquote

VERSION = 1
MAX_BYTES = 64 * 1024 * 1024
MAX_OBJECTS = 100000
DEFAULT_STAGE = ''
ID = re.compile(r'^[a-f0-9]{32}$')


def stage_url(value):
    value = value.strip().replace('\\_', '_')
    parts = urlsplit(value)
    if (parts.scheme != 'omniverse' or not parts.hostname or parts.username
            or parts.password or parts.query or parts.fragment or '\\' in value):
        raise ValueError('Use an omniverse://host/folder/stage.usd URL without credentials or query.')
    path = unquote(parts.path)
    if any(p in ('.', '..') for p in path.split('/')) or any(ord(c) < 32 for c in path):
        raise ValueError('Invalid stage path.')
    if not path.lower().endswith(('.usd', '.usda', '.usdc')):
        raise ValueError('Stage URL must include a .usd, .usda, or .usdc filename.')
    return urlunsplit(('omniverse', parts.netloc.lower(), quote(path, safe='/_-.'), '', ''))


def identifier(value):
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise ValueError('Invalid source, stream, or object identifier.')
    return value


def number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('Expected a finite number.')
    return value


def vector(value, length):
    if not isinstance(value, list) or len(value) != length:
        raise ValueError('Invalid vector size.')
    for item in value:
        number(item)


def text_field(value, maximum=4096):
    if not isinstance(value, str) or len(value) > maximum:
        raise ValueError('Invalid text field.')


def validate(packet):
    if not isinstance(packet, dict) or packet.get('version') != VERSION:
        raise ValueError('Unsupported protocol version.')
    identifier(packet.get('source'))
    identifier(packet.get('stream'))
    if type(packet.get('seq')) is not int or packet['seq'] < 1:
        raise ValueError('Invalid sequence.')
    if type(packet.get('full')) is not bool:
        raise ValueError('Missing full snapshot flag.')
    if not 0 < number(packet.get('meters_per_unit')) <= 1e6:
        raise ValueError('Invalid scene scale.')
    packet['stage'] = stage_url(packet.get('stage', ''))
    objects, removed = packet.get('objects'), packet.get('removed')
    if not isinstance(objects, list) or not isinstance(removed, list):
        raise ValueError('Objects and removed must be arrays.')
    if len(objects) + len(removed) > MAX_OBJECTS:
        raise ValueError('Too many objects in one update.')
    seen = set()
    for obj in objects:
        if not isinstance(obj, dict):
            raise ValueError('Invalid object.')
        oid = identifier(obj.get('id'))
        if oid in seen:
            raise ValueError('Duplicate object id.')
        seen.add(oid)
        text_field(obj.get('name'))
        vector(obj.get('matrix'), 16)
        if type(obj.get('visible')) is not bool:
            raise ValueError('Invalid visibility.')
        metadata = obj.get('ifc', {})
        if not isinstance(metadata, dict) or len(metadata) > 10:
            raise ValueError('Invalid IFC metadata.')
        for key, value in metadata.items():
            if key not in ('GlobalId', 'Class', 'Name', 'Description', 'StepId'):
                raise ValueError('Unsupported IFC metadata key.')
            text_field(value)
        mesh = obj.get('mesh')
        if packet['full'] and 'mesh' not in obj:
            raise ValueError('A full snapshot must include mesh data or null.')
        if mesh is not None:
            if not isinstance(mesh, dict):
                raise ValueError('Invalid mesh.')
            points, counts, indices = mesh.get('points'), mesh.get('counts'), mesh.get('indices')
            if not all(isinstance(v, list) for v in (points, counts, indices)):
                raise ValueError('Invalid mesh arrays.')
            for point in points:
                vector(point, 3)
            if any(type(n) is not int or n < 3 for n in counts) or sum(counts) != len(indices):
                raise ValueError('Invalid face topology.')
            if any(type(i) is not int or not 0 <= i < len(points) for i in indices):
                raise ValueError('Mesh index out of bounds.')
            colors = mesh.get('colors', [])
            opacity = mesh.get('opacity', [])
            if len(colors) != len(counts) or len(opacity) != len(counts):
                raise ValueError('Expected one material color and opacity per face.')
            for color in colors:
                vector(color, 3)
            for alpha in opacity:
                if not 0 <= number(alpha) <= 1:
                    raise ValueError('Opacity must be between zero and one.')
            normals, uv = mesh.get('normals', []), mesh.get('uv', [])
            if normals and len(normals) != len(indices):
                raise ValueError('Invalid corner normals.')
            if uv and len(uv) != len(indices):
                raise ValueError('Invalid UV coordinates.')
            for normal in normals:
                vector(normal, 3)
            for value in uv:
                vector(value, 2)
    for oid in removed:
        identifier(oid)
        if oid in seen:
            raise ValueError('Object cannot be updated and removed together.')
    if len(set(removed)) != len(removed):
        raise ValueError('Duplicate removal.')
    return packet
