"""BRWP: planos Y/Cb/Cr float32 little-endian para CDNG_INJECT_PLANES."""
from __future__ import annotations

import os
import struct

import numpy as np

MAGIC = b'BRWP'


def write_brwp(path, y, cb, cr):
    y = np.asarray(y, dtype=np.float32)
    cb = np.asarray(cb, dtype=np.float32)
    cr = np.asarray(cr, dtype=np.float32)
    if y.ndim != 2 or cb.ndim != 2 or cr.ndim != 2:
        raise ValueError('Y/Cb/Cr must be HxW')
    h, w = y.shape
    if w % 2 or h % 2:
        raise ValueError('Y size must be even')
    if cb.shape != (h // 2, w // 2) or cr.shape != cb.shape:
        raise ValueError('Cb/Cr must be Y/2')
    os.makedirs(os.path.dirname(os.path.abspath(path)) or '.', exist_ok=True)
    with open(path, 'wb') as f:
        f.write(MAGIC + struct.pack('<II', w, h))
        f.write(np.ascontiguousarray(y).tobytes())
        f.write(np.ascontiguousarray(cb).tobytes())
        f.write(np.ascontiguousarray(cr).tobytes())
    return path


def read_brwp(path):
    with open(path, 'rb') as f:
        data = f.read()
    if len(data) < 12 or data[:4] != MAGIC:
        raise ValueError('not BRWP: %s' % path)
    w, h = struct.unpack_from('<II', data, 4)
    n_y = w * h
    n_c = (w // 2) * (h // 2)
    need = 12 + 4 * (n_y + 2 * n_c)
    if len(data) < need:
        raise ValueError('truncated BRWP')
    off = 12
    y = np.frombuffer(data, np.float32, n_y, off).reshape(h, w).copy()
    off += n_y * 4
    cb = np.frombuffer(data, np.float32, n_c, off).reshape(h // 2, w // 2).copy()
    off += n_c * 4
    cr = np.frombuffer(data, np.float32, n_c, off).reshape(h // 2, w // 2).copy()
    return y, cb, cr
