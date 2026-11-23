#!/usr/bin/env python3
"""Escritura de frames BRAW en forma de fichero y sustitución in situ en un .braw plantilla.

Forma de fichero del chunk 'braw' (big-endian), medida en los samples:
  +0  'braw' u32 tamaño | +8 cabecera 120 bytes (versión 2, cuantizador, planos, slices, ancho, alto, filas/slice,
  escalera, 2×u16 variables, anchos de franja en +0x38) | +0x80 tabla u32 BE × (planos×slices) de offsets desde el
  inicio del chunk | +0x270 … 0x1200 hueco (se copia del frame plantilla) | 0x1200 primer slice.
Un frame = 'bmdf' (1024 bytes de metadatos) + chunk; el sample del fichero está relleno hasta su tamaño original.
"""
import struct, sys, os
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

FIRST_SLICE = 0x1200
BMDF_SIZE = 1024


def read_atoms(b, off, end):
    while off + 8 <= end:
        sz, typ = struct.unpack(">I4s", b[off:off + 8])
        if sz == 1: sz = struct.unpack(">Q", b[off + 8:off + 16])[0]
        if sz == 0: sz = end - off
        yield off, sz, typ; off += sz


def video_samples(path):
    """devuelve [(offset, size)] de los samples de la pista de vídeo brxq (primera pista)."""
    # Only load moov. Long conversions must not allocate the whole mdat just
    # to validate their sample count or inspect offsets.
    with open(path, "rb") as stream:
        length = stream.seek(0, 2); offset = 0
        while offset < length:
            stream.seek(offset)
            size, kind = struct.unpack('>I4s', stream.read(8))
            if size == 1: size = struct.unpack('>Q', stream.read(8))[0]
            elif size == 0: size = length - offset
            if size < 8 or offset + size > length:
                raise ValueError('invalid top-level atom')
            if kind == b'moov':
                stream.seek(offset); f = stream.read(size); break
            offset += size
        else:
            raise ValueError('missing moov')
    mo, ms = 0, len(f)
    trak = next((o, s) for o, s, t in read_atoms(f, mo + 8, mo + ms) if t == b"trak")
    def find(path_, o, s):
        for t in path_:
            o, s = next((oo, ss) for oo, ss, tt in read_atoms(f, o + 8, o + s) if tt == t)
        return o, s
    stbl_o, stbl_s = find([b"mdia", b"minf", b"stbl"], *trak)
    stsz = next((o, s) for o, s, t in read_atoms(f, stbl_o + 8, stbl_o + stbl_s) if t == b"stsz")
    co = next((o, s, t) for o, s, t in read_atoms(f, stbl_o + 8, stbl_o + stbl_s) if t in (b"co64", b"stco"))
    n = struct.unpack(">I", f[stsz[0] + 16:stsz[0] + 20])[0]
    sizes = struct.unpack(f">{n}I", f[stsz[0] + 20:stsz[0] + 20 + 4 * n])
    m = struct.unpack(">I", f[co[0] + 12:co[0] + 16])[0]
    offs = struct.unpack(f">{m}Q", f[co[0] + 16:co[0] + 16 + 8 * m]) if co[2] == b"co64" else struct.unpack(f">{m}I", f[co[0] + 16:co[0] + 16 + 4 * m])
    return list(zip(offs, sizes))


def split_file_chunk(chunk):
    """chunk en forma de fichero → (cabecera 120 B, tabla [int], hueco bytes, [slices bytes])."""
    assert chunk[:4] == b"braw"
    size = struct.unpack(">I", chunk[4:8])[0]
    hdr = chunk[8:8 + 120]
    planes, slices = hdr[2], hdr[3]
    n = planes * slices
    table = list(struct.unpack(f">{n}I", chunk[0x80:0x80 + 4 * n]))
    gap = chunk[0x80 + 4 * n:FIRST_SLICE]
    bounds = table + [size]
    sl = [chunk[bounds[i]:bounds[i + 1]] for i in range(n)]
    return hdr, table, gap, sl


def build_file_chunk(hdr, gap, slices):
    """compone un chunk en forma de fichero a partir de la cabecera (120 B), el hueco y la lista de slices (cada uno
    ya relleno a múltiplo de 8 bytes)."""
    n = len(slices)
    table = []; off = FIRST_SLICE
    for s in slices:
        assert len(s) % 8 == 0
        table.append(off); off += len(s)
    body = b"braw" + struct.pack(">I", off) + bytes(hdr) + struct.pack(f">{n}I", *table) + gap + b"".join(slices)
    assert len(body) == off
    return body


class Geometry:
    """geometría de un frame: tamaño entregado (w, h) y almacenado (ws, hs), franjas y slices. Reglas (medidas con el
    SDK, 2026-10-31): anchura almacenada múltiplo de 32 ≥ w+32 (4 franjas múltiplo de 16, dos y dos), altura múltiplo
    de 8 ≥ h+16, slices de 72 filas (el último más corto), imagen centrada (crop_origin = márgenes)."""
    def __init__(self, w, h, ws=None, hs=None, rows=72, nstrips=4):
        """rows: filas por slice (cabecera, offset 8; la cámara usa 72); nstrips: franjas de columnas (el Video Assist
        usa 5 de 768/784 px con anchura almacenada w+16; nuestra regla por defecto sigue siendo 4 franjas «dos y dos»)."""
        self.w, self.h, self.rows, self.nstrips = w, h, rows, nstrips
        self.ws = ws or -(-(w + 32) // 32) * 32; self.hs = hs or -(-(h + 16) // 8) * 8
        # el SDK se cuelga con más de 33 slices por franja (9 nov 2026): por encima de 2304 líneas, más filas por slice
        if -(-self.hs // rows) > 32: self.rows = rows = -(-(-(-self.hs // 32)) // 8) * 8
        units = self.ws // 16
        if nstrips == 4:
            a = units // 2 // 2 * 16; b = (self.ws - 2 * a) // 2
            self.strips = [a, a, b, b]; assert a % 16 == 0 and b % 16 == 0 and sum(self.strips) == self.ws
        else:
            base, extra = divmod(units, nstrips)   # las franjas más anchas al final, como en el Video Assist
            self.strips = [16 * (base + (1 if i >= nstrips - extra else 0)) for i in range(nstrips)]
            assert sum(self.strips) == self.ws and min(self.strips) >= 16
        self.bounds = []; x = 0
        for s_ in self.strips: self.bounds.append((x, x + s_)); x += s_
        self.slices = -(-self.hs // rows); self.n = nstrips * self.slices
        self.x0, self.y0 = (self.ws - w) // 2, (self.hs - h) // 2

    def header(self, hdr0):
        """cabecera de 120 B a partir de la de referencia (q, escalera… se conservan)."""
        hdr = bytearray(hdr0); hdr[2] = self.nstrips; hdr[3] = self.slices
        struct.pack_into(">HHH", hdr, 4, self.ws, self.hs, self.rows)
        hdr[0x38:0x38 + 8] = bytes(8); struct.pack_into(f">{self.nstrips}H", hdr, 0x38, *self.strips)
        return bytes(hdr)

    def gap(self, gap0):
        """hueco 0x80+4n … 0x1200: ceros + tabla u16 BE 0x0021 × 96 en 0x1080 + los 16 bytes finales de la referencia.
        La tabla tiene SIEMPRE 96 entradas (64 coeficientes de luma + 32 de croma, el paso plano 33), tanto en la
        Pocket 4K (124 slices) como en el Video Assist (155): no es por slice. Escribirla por slice (n < 96, p. ej.
        Full HD con 4 franjas × 16 slices) dejaba a cero las entradas de croma y el decodificador la descartaba
        (medido el 1 nov 2026; era el problema D3)."""
        base = 0x80 + 4 * self.n; g = bytearray(FIRST_SLICE - base)
        assert base <= 0x1080, "demasiados slices para la tabla de offsets"
        for i in range(96): struct.pack_into(">H", g, 0x1080 - base + 2 * i, 0x21)
        g[-16:] = gap0[-16:]
        return bytes(g)

    def patch_moov(self, b, mo):
        """parchea en el fichero (bytearray) desde mo: stsd (u16 w,h), tkhd (16.16), floats crop/sensor y origen."""
        for old, new in ((struct.pack(">HH", 4128, 2176), struct.pack(">HH", self.ws, self.hs)),
                         (struct.pack(">II", 4128 << 16, 2176 << 16), struct.pack(">II", self.ws << 16, self.hs << 16)),
                         (struct.pack(">ff", 4096, 2160), struct.pack(">ff", self.w, self.h)),
                         (struct.pack(">ff", 16, 8), struct.pack(">ff", self.x0, self.y0))):
            i = mo
            while True:
                i = b.find(old, i)
                if i < 0: break
                b[i:i + len(old)] = new; i += len(new)
        return b


def replace_frame(path, out_path, frame, slices, hdr_patch=None):
    """escribe out_path = path con el chunk del frame sustituido por uno construido con `slices` (lista de bytes
    en el orden de la tabla). El sample conserva su tamaño (relleno con ceros)."""
    f = bytearray(open(path, "rb").read())
    off, size = video_samples(path)[frame]
    bmdf = f[off:off + BMDF_SIZE]
    assert bmdf[4:8] == b"bmdf"
    old = bytes(f[off + BMDF_SIZE:off + size])
    hdr, table, gap, old_slices = split_file_chunk(old)
    if hdr_patch: hdr = bytes(hdr_patch(bytearray(hdr)))
    new = build_file_chunk(hdr, gap, slices)
    if len(new) > size - BMDF_SIZE: raise ValueError(f"chunk nuevo ({len(new)} B) mayor que el sample ({size - BMDF_SIZE} B)")
    f[off + BMDF_SIZE:off + size] = new + b"\0" * (size - BMDF_SIZE - len(new))
    open(out_path, "wb").write(f)
    return old_slices


if __name__ == "__main__":
    src = sys.argv[1]
    for i, (o, s) in enumerate(video_samples(src)): print(f"frame {i}: offset {o} tamaño {s}")
