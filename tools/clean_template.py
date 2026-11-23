#!/usr/bin/env python3
"""Deja la plantilla BRAW del conversor (model/cam/orig.braw) sin nada de la grabación de la que salió.

    .venv/bin/python tools/clean_template.py model/cam/orig.braw

La plantilla es un clip de una Pocket Cinema Camera 4K: el conversor toma de ella el moov (pistas, claves de
metadatos) y la cabecera y los metadatos del primer frame, y no usa sus imágenes. Lo que identificaba a la cámara y a
la toma pasaba a cada BRAW convertido, así que aquí se quita:

- las claves post_3dlut_* (la LUT de Blackmagic incrustada, 431 KB, y su nombre);
- camera_id, clip_number y date_recorded, con valores neutros (el motor escribe los de cada toma al convertir);
- la imagen: los slices de cada frame se ponen a cero. La cabecera, la tabla de slices y los metadatos por frame
  (genéricos: velocidad, ángulo de obturación, ISO y balance) se quedan, que es lo que el motor lee.

Reescribe el fichero en su sitio. El moov va al final, así que los offsets de los frames no cambian. Es idempotente.
"""
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import braw_container as BC

DROP_PREFIX = 'post_3dlut_'
NEUTRAL = {'camera_id': b'00000000-0000-0000-0000-000000000000', 'clip_number': b'A001_00000000_C001',
           'date_recorded': b'2000:01:01'}
BMDF = 1024


def atom(kind, body):
    return struct.pack('>I4s', 8 + len(body), kind) + body


def clean_meta(meta):
    """meta del moov (cuerpo, sin cabecera): hdlr, keys, ilst."""
    parts = [(t, meta[o + 8:o + s]) for o, s, t in BC.read_atoms(meta, 0, len(meta))]
    keys = dict(parts)[b'keys']
    count = struct.unpack('>I', keys[4:8])[0]
    names, p = [], 8
    for _ in range(count):
        size = struct.unpack('>I', keys[p:p + 4])[0]
        names.append(keys[p + 8:p + size].decode())
        p += size
    items = {}
    ilst = dict(parts)[b'ilst']
    for o, s, t in BC.read_atoms(ilst, 0, len(ilst)):
        items[struct.unpack('>I', t)[0]] = ilst[o + 8:o + s]
    kept, new_keys, new_ilst = [], b'', b''
    for i, name in enumerate(names, 1):
        if name.startswith(DROP_PREFIX):
            continue
        kept.append(name)
        new_keys += atom(b'mdta', name.encode())
        body = items.get(i)
        if body is None:
            continue
        if name in NEUTRAL:
            out = b''
            for o, s, t in BC.read_atoms(body, 0, len(body)):
                child = body[o + 8:o + s]
                out += atom(t, child[:8] + NEUTRAL[name] if t == b'data' else child)
            body = out
        new_ilst += atom(struct.pack('>I', len(kept)), body)
    missing = set(NEUTRAL) - set(kept)
    if missing:
        raise SystemExit(f'faltan claves en la plantilla: {sorted(missing)}')
    out = b''
    for t, body in parts:
        if t == b'keys':
            body = keys[:4] + struct.pack('>I', len(kept)) + new_keys
        elif t == b'ilst':
            body = new_ilst
        out += atom(t, body)
    return out


def main(path):
    path = Path(path)
    data = bytearray(path.read_bytes())
    top = list(BC.read_atoms(data, 0, len(data)))
    if top[-1][2] != b'moov':
        raise SystemExit('el moov tiene que ir al final')
    # imagen a cero (solo los slices; cabecera, tabla y hueco se quedan)
    for offset, size in BC.video_samples(str(path)):
        chunk = bytes(data[offset + BMDF:offset + size])
        hdr, table, gap, slices = BC.split_file_chunk(chunk)
        start = offset + BMDF + table[0]
        end = offset + BMDF + struct.unpack('>I', chunk[4:8])[0]
        data[start:end] = bytes(end - start)
    mo, ms, _ = top[-1]
    moov = bytes(data[mo + 8:mo + ms])
    body = b''
    for o, s, t in BC.read_atoms(moov, 0, len(moov)):
        child = moov[o + 8:o + s]
        body += atom(t, clean_meta(child) if t == b'meta' else child)
    path.write_bytes(bytes(data[:mo]) + atom(b'moov', body))
    print(f'{path}: {mo + 8 + len(body)} bytes')


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else 'model/cam/orig.braw')
