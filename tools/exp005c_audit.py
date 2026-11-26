"""EXP-005c: auditoría de tags DNG Pocket OG (sin demosaic)."""
from __future__ import annotations

import json
import os
import struct

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# TIFF/DNG
T_BYTE, T_ASCII, T_SHORT, T_LONG, T_RATIONAL = 1, 2, 3, 4, 5
T_SBYTE, T_UNDEF, T_SSHORT, T_SLONG, T_SRATIONAL = 6, 7, 8, 9, 10
T_FLOAT, T_DOUBLE = 11, 12
SIZES = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8}


def _u(fmt, data, off, le):
    endian = '<' if le else '>'
    return struct.unpack_from(endian + fmt, data, off)[0]


def read_tags(path, max_ifds=8):
    with open(path, 'rb') as f:
        data = f.read(1 << 22)  # cabecera + IFDs (DNG OG cabe)
    if data[:2] not in (b'II', b'MM'):
        raise ValueError('not TIFF')
    le = data[:2] == b'II'
    magic = _u('H', data, 2, le)
    if magic not in (42, 43):
        raise ValueError('not TIFF/DNG')
    off = _u('I', data, 4, le)
    tags = {}
    for _ in range(max_ifds):
        if off + 2 > len(data) or off == 0:
            break
        n = _u('H', data, off, le)
        p = off + 2
        for i in range(n):
            tag = _u('H', data, p, le)
            typ = _u('H', data, p + 2, le)
            cnt = _u('I', data, p + 4, le)
            val = _u('I', data, p + 8, le)
            nbytes = SIZES.get(typ, 1) * cnt
            if nbytes <= 4:
                blob = data[p + 8:p + 12]
            else:
                blob = data[val:val + nbytes] if val + nbytes <= len(data) else b''
            tags.setdefault(tag, {'type': typ, 'count': cnt, 'blob': blob, 'value': val})
            p += 12
        off = _u('I', data, p, le) if p + 4 <= len(data) else 0
    return tags, le


def _nums(entry, le, n=None):
    typ, blob, cnt = entry['type'], entry['blob'], entry['count']
    if not blob:
        return []
    endian = '<' if le else '>'
    if typ == T_SHORT:
        fmt = endian + 'H' * min(cnt, len(blob) // 2)
        return list(struct.unpack(fmt, blob[:struct.calcsize(fmt)]))[:n or cnt]
    if typ == T_LONG:
        fmt = endian + 'I' * min(cnt, len(blob) // 4)
        return list(struct.unpack(fmt, blob[:struct.calcsize(fmt)]))[:n or cnt]
    if typ in (T_RATIONAL, T_SRATIONAL):
        out = []
        fmt = 'Ii' if typ == T_SRATIONAL else 'II'
        for i in range(min(cnt, len(blob) // 8)):
            num, den = struct.unpack_from(endian + fmt, blob, i * 8)
            out.append(num / den if den else float('nan'))
        return out[:n or cnt]
    if typ == T_BYTE:
        return list(blob[:n or cnt])
    return []


def inspect_dng(path):
    tags, le = read_tags(path)
    def g(tag):
        return tags[tag] if tag in tags else None
    cfa = _nums(g(33422), le) if g(33422) else None
    cfa_dim = _nums(g(33421), le) if g(33421) else None
    return {
        'path': os.path.basename(path),
        'compression': (_nums(g(259), le) or [None])[0] if g(259) else None,
        'photometric': (_nums(g(262), le) or [None])[0] if g(262) else None,
        'cfa_repeat': cfa_dim,
        'cfa_pattern': cfa,
        'black_level': _nums(g(50714), le) if g(50714) else None,
        'white_level': _nums(g(50717), le) if g(50717) else None,
        'default_crop_origin': _nums(g(50719), le) if g(50719) else None,
        'default_crop_size': _nums(g(50720), le) if g(50720) else None,
        'active_area': _nums(g(50829), le) if g(50829) else None,
        'image_width': (_nums(g(256), le) or [None])[0] if g(256) else None,
        'image_length': (_nums(g(257), le) or [None])[0] if g(257) else None,
        'bits': _nums(g(258), le) if g(258) else None,
        'baseline_exposure': _nums(g(50730), le) if g(50730) else None,
        'unique_camera_model': None,
        'is_compressed': bool(g(259) and (_nums(g(259), le) or [1])[0] != 1),
        'little_endian': le,
    }


def find_frame(dataset, clip, frame_index):
    folder = os.path.join(dataset, clip)
    if not os.path.isdir(folder):
        return None
    files = sorted(n for n in os.listdir(folder) if n.lower().endswith('.dng'))
    if frame_index >= len(files):
        return None
    return os.path.join(folder, files[frame_index])


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--dataset', default=os.path.expanduser('~/Desktop/BMD'))
    args = ap.parse_args()
    clip = 'BMPCC_1_2000-03-02_0756_C0039'
    path = find_frame(args.dataset, clip, 44)
    out = {'status': 'missing_dng'}
    if path:
        out = inspect_dng(path)
        out['status'] = 'ok'
        out['clip'] = clip
        out['frame'] = 44
    print(json.dumps(out, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
