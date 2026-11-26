"""Fixtures sintéticos EXP-004: RGB BRWD y Bayer mosaicado sobre un DNG plantilla."""
from __future__ import annotations

import argparse
import json
import os
import struct
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools.fidelity import images

RGGB = {(0, 0): 0, (0, 1): 1, (1, 0): 1, (1, 1): 2}  # R,G,G,B


def _levels(h, w, lo, hi, axis=1, phase=0):
    img = np.zeros((h, w, 3), np.float32)
    if axis == 1:
        img[:, : w // 2 + phase] = lo
        img[:, w // 2 + phase:] = hi
    else:
        img[: h // 2 + phase] = lo
        img[h // 2 + phase:] = hi
    return img


def diagonal(h, w, lo, hi):
    yy, xx = np.mgrid[0:h, 0:w]
    mask = (xx + yy) < (w + h) // 2
    img = np.broadcast_to(hi, (h, w, 3)).copy().astype(np.float32)
    img[mask] = lo
    return img


def impulse(h, w, rgb, x, y):
    img = np.full((h, w, 3), 0.18, np.float32)
    img[y, x] = np.array(rgb, np.float32)
    return img


def lines(h, w, widths, axis=1, chroma=(0.4, 0.18, 0.18)):
    img = np.full((h, w, 3), 0.18, np.float32)
    x = 16
    for tw in widths:
        if axis == 1:
            img[:, x:x + tw] = chroma
        else:
            img[x:x + tw] = chroma
        x += tw + 16
    return img


def checker(h, w, tile, a, b):
    yy, xx = np.mgrid[0:h, 0:w]
    m = ((xx // tile) + (yy // tile)) % 2 == 0
    img = np.zeros((h, w, 3), np.float32)
    img[m] = a
    img[~m] = b
    return img


def chirp(h, w, chroma=False):
    x = np.linspace(0, 1, w, dtype=np.float32)
    freq = 2 + 40 * x
    phase = np.cumsum(freq) * (2 * np.pi / w)
    wave = 0.18 + 0.12 * np.sin(phase)
    img = np.stack([np.tile(wave, (h, 1))] * 3, -1).astype(np.float32)
    if chroma:
        img[..., 1] = 0.18
        img[..., 2] = 0.18 + 0.12 * np.sin(phase * 0.5)
    return img


def build_set(h=128, w=128):
    lo_n, hi_n = np.array([0.02] * 3, np.float32), np.array([0.6] * 3, np.float32)
    grey_a, grey_b = np.array([0.15] * 3, np.float32), np.array([0.35] * 3, np.float32)
    red, green = np.array([0.5, 0.05, 0.05], np.float32), np.array([0.05, 0.5, 0.05], np.float32)
    blue, yellow = np.array([0.05, 0.05, 0.5], np.float32), np.array([0.5, 0.5, 0.05], np.float32)
    fixtures = {
        'edge_neutral_v': _levels(h, w, lo_n, hi_n, 1, 0),
        'edge_neutral_v_phase1': _levels(h, w, lo_n, hi_n, 1, 1),
        'edge_neutral_h': _levels(h, w, lo_n, hi_n, 0, 0),
        'edge_grey_v': _levels(h, w, grey_a, grey_b, 1, 0),
        'edge_rg_v': _levels(h, w, red, green, 1, 0),
        'edge_by_v': _levels(h, w, blue, yellow, 1, 0),
        'edge_neutral_diag': diagonal(h, w, lo_n, hi_n),
        'flat_bg': np.full((h, w, 3), 0.18, np.float32),
        'impulse_chroma': impulse(h, w, (0.6, 0.05, 0.4), w // 2, h // 2),
        # Celdas 4:2:0 distintas: even (2x,2y) vs odd del cuarteto vecino (2x+3,2y+3).
        'impulse_even': impulse(h, w, (0.7, 0.05, 0.05), (w // 2) & ~1, (h // 2) & ~1),
        'impulse_odd': impulse(h, w, (0.7, 0.05, 0.05), ((w // 2) & ~1) + 3, ((h // 2) & ~1) + 3),
        'impulse_dct': impulse(h, w, (0.05, 0.05, 0.7), (w // 2 // 8) * 8 + 3, (h // 2 // 8) * 8 + 3),
        'edge_rg_h': _levels(h, w, red, green, 0, 0),
        'edge_rg_v_phase1': _levels(h, w, red, green, 1, 1),
        'lines_1_2_4_8': lines(h, w, [1, 2, 4, 8], 1),
        'checker_chroma': checker(h, w, 8, red, green),
        'chirp_luma': chirp(h, w, False),
        'chirp_chroma': chirp(h, w, True),
    }
    ramp = np.zeros((h, w, 3), np.float32)
    t = np.linspace(0.02, 0.6, w, dtype=np.float32)
    ramp[..., 0] = t
    ramp[..., 1] = t * 0.1
    ramp[..., 2] = t * 0.1
    fixtures['chroma_iso_ramp'] = ramp
    # Variar fase CFA / subsample / DCT: desplazar el borde 0–8 px.
    for ph in range(0, 9):
        fixtures['edge_neutral_v_p%d' % ph] = _levels(h, w, lo_n, hi_n, 1, ph - 4)
    return fixtures


def mosaic_rggb(rgb):
    h, w, _ = rgb.shape
    out = np.zeros((h, w), np.float32)
    for (r, c), ch in RGGB.items():
        out[r::2, c::2] = rgb[r::2, c::2, ch]
    return out


def write_fixture(directory, name, rgb, extra=None):
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, name + '.f32')
    images.write_f32(path, rgb)
    meta = {'name': name, 'width': int(rgb.shape[1]), 'height': int(rgb.shape[0]),
            'domain': 'linear_camera_rgb_synthetic',
            'note': 'Deterministic fixture; not optical CFA/PSF of the Pocket OG sensor'}
    if extra:
        meta.update(extra)
    with open(os.path.join(directory, name + '.json'), 'w', encoding='utf-8') as f:
        json.dump(meta, f, indent=2)
        f.write('\n')
    return path


# --- DNG mosaic patch (uncompressed strips or tiles, 16-bit) ---

def _u16(buf, off, le):
    return struct.unpack_from('<H' if le else '>H', buf, off)[0]


def _u32(buf, off, le):
    return struct.unpack_from('<I' if le else '>I', buf, off)[0]


def _ifd0(buf):
    if buf[:2] == b'II':
        le = True
    elif buf[:2] == b'MM':
        le = False
    else:
        raise ValueError('not TIFF')
    if _u16(buf, 2, le) != 42:
        raise ValueError('not TIFF42')
    return _u32(buf, 4, le), le


def _entries(buf, off, le):
    n = _u16(buf, off, le)
    out = {}
    for i in range(n):
        e = off + 2 + i * 12
        tag, typ, cnt = _u16(buf, e, le), _u16(buf, e + 2, le), _u32(buf, e + 4, le)
        val = buf[e + 8:e + 12]
        out[tag] = (typ, cnt, val, e + 8)
    return out


def _value_offset(val, le):
    return _u32(val, 0, le)


def patch_dng_mosaic(template, dest, mosaic_norm, black=256, white=4095):
    """Copia el DNG plantilla y sustituye el mosaico 16-bit sin comprimir. No altera tags de color."""
    buf = bytearray(open(template, 'rb').read())
    off, le = _ifd0(buf)
    ent = _entries(buf, off, le)
    compression = _u16(ent[259][2], 0, le) if 259 in ent else 1
    if compression != 1:
        raise ValueError('template DNG is compressed (tag 259=%s); cannot patch mosaic' % compression)
    bps = _u16(ent[258][2], 0, le) if 258 in ent else 16
    if bps != 16:
        raise ValueError('expected 16-bit samples, got %s' % bps)
    w = _u32(ent[256][2], 0, le) if ent[256][1] == 1 else _u16(ent[256][2], 0, le)
    h = _u32(ent[257][2], 0, le) if ent[257][1] == 1 else _u16(ent[257][2], 0, le)
    if mosaic_norm.shape != (h, w):
        # Permitir recorte: rellenar/recortar al tamaño del sensor del IFD.
        canvas = np.zeros((h, w), np.float32)
        hh, ww = min(h, mosaic_norm.shape[0]), min(w, mosaic_norm.shape[1])
        canvas[:hh, :ww] = mosaic_norm[:hh, :ww]
        mosaic_norm = canvas
    raw = np.clip(np.rint(mosaic_norm * (white - black) + black), 0, 65535).astype('<u2' if le else '>u2')
    payload = raw.tobytes()
    if 273 in ent:  # StripOffsets
        n = ent[273][1]
        if n != 1:
            raise ValueError('multi-strip DNG not supported (count=%s)' % n)
        start = _value_offset(ent[273][2], le) if n == 1 and ent[273][0] in (4, 3) else _u32(ent[273][2], 0, le)
        if ent[273][0] == 3:
            start = _u16(ent[273][2], 0, le)
        nbytes = _u32(ent[279][2], 0, le) if 279 in ent else len(payload)
        if nbytes != len(payload):
            raise ValueError('strip bytes %s ≠ mosaic %s' % (nbytes, len(payload)))
        buf[start:start + nbytes] = payload
    elif 324 in ent:  # TileOffsets — un tile único
        n = ent[324][1]
        if n != 1:
            raise ValueError('multi-tile DNG not supported')
        start = _u32(ent[324][2], 0, le)
        nbytes = _u32(ent[325][2], 0, le)
        if nbytes != len(payload):
            raise ValueError('tile bytes %s ≠ mosaic %s' % (nbytes, len(payload)))
        buf[start:start + nbytes] = payload
    else:
        raise ValueError('no StripOffsets/TileOffsets')
    os.makedirs(os.path.dirname(dest) or '.', exist_ok=True)
    open(dest, 'wb').write(buf)
    return dest


def main():
    p = argparse.ArgumentParser(description='EXP-004 synthetic fixtures')
    p.add_argument('--out', default=os.path.join(ROOT, 'out', 'benchmark', 'experiments', 'exp004', 'fixtures'))
    p.add_argument('--width', type=int, default=128)
    p.add_argument('--height', type=int, default=128)
    p.add_argument('--template-dng', help='DNG Pocket OG para mosaicar (ruta Bayer)')
    args = p.parse_args()
    fx = build_set(args.height, args.width)
    index = []
    for name, rgb in fx.items():
        path = write_fixture(args.out, name, rgb)
        index.append({'name': name, 'rgb': path, 'bayer': None})
        if args.template_dng:
            try:
                bayer = os.path.join(args.out, 'bayer', name + '.dng')
                patch_dng_mosaic(args.template_dng, bayer, mosaic_rggb(rgb))
                index[-1]['bayer'] = bayer
            except Exception as e:
                index[-1]['bayer_error'] = str(e)
    with open(os.path.join(args.out, 'index.json'), 'w', encoding='utf-8') as f:
        json.dump({'fixtures': index, 'cfa': 'RGGB'}, f, indent=2)
        f.write('\n')
    print('wrote', len(index), 'fixtures →', args.out)


if __name__ == '__main__':
    main()
