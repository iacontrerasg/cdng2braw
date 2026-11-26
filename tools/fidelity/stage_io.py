"""Lectura de volcados EXP-004 (CDNG_DUMP_STAGES). No convierte entre dominios."""
from __future__ import annotations

import hashlib
import json
import os

import numpy as np


class StageError(ValueError):
    pass


def _load_json(path):
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def read_buffer(sidecar_path):
    meta = _load_json(sidecar_path)
    raw = os.path.join(os.path.dirname(sidecar_path), meta['path'])
    data = open(raw, 'rb').read()
    if len(data) != int(meta['nbytes']):
        raise StageError('%s: nbytes %s ≠ %d' % (raw, meta['nbytes'], len(data)))
    dtype = {'float32': '<f4', 'int16': '<i2', 'uint8': '<u1'}[meta['dtype']]
    arr = np.frombuffer(data, dtype)
    w, h, c = int(meta['width']), int(meta['height']), int(meta['channels'])
    layout = meta.get('layout', '')
    if c == 1:
        arr = arr.reshape(h, w)
    elif 'interleaved' in layout or layout == 'HxWx3_interleaved':
        arr = arr.reshape(h, w, c)
    else:
        arr = arr.reshape(c, h, w)
    sha = hashlib.sha256(data).hexdigest()
    meta = dict(meta)
    meta['sha256'] = sha
    meta['sidecar'] = os.path.abspath(sidecar_path)
    meta['bin'] = os.path.abspath(raw)
    return arr, meta


def load_frame_dir(directory):
    """Todos los sidecars *.json de un frame_*."""
    out = {}
    for name in sorted(os.listdir(directory)):
        if not name.endswith('.json'):
            continue
        arr, meta = read_buffer(os.path.join(directory, name))
        key = '%s_%s' % (meta['stage'], meta['plane'])
        out[key] = {'array': arr, 'meta': meta}
    return out


def upsample_420_centered(plane, height, width):
    """Réplica 2×2 del 4:2:0 centrado (cada muestra croma cubre el cuarteto). No es el kernel del SDK."""
    up = np.repeat(np.repeat(plane, 2, axis=0), 2, axis=1)
    return up[:height, :width]


def ycbcr_to_codes(y, cb, cr):
    """code_from_ycc del JSON OG (R,G,B códigos de curva)."""
    r = y - 0.5 * cb + 1.5 * cr
    g = y - 0.5 * cb - 0.5 * cr
    b = y + 1.5 * cb - 0.5 * cr
    return np.stack([r, g, b], -1)


def codes_to_ycbcr(rgb):
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    y = (r + 2 * g + b) / 4
    cb = (b - g) / 2
    cr = (r - g) / 2
    return y, cb, cr
