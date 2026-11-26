"""Inspección de BRAW para identidad de imagen (independiente del moov variable).

El SHA-256 del fichero completo no es identidad de imagen: el átomo moov puede
cambiar entre corridas idénticas. La comparación de imagen usa mdat / payloads.
"""
import hashlib
import struct

BMDF_SIZE = 1024
FIRST_SLICE = 0x1200


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def read_atoms(buf, off, end):
    while off + 8 <= end:
        sz, typ = struct.unpack('>I4s', buf[off:off + 8])
        if sz == 1:
            sz = struct.unpack('>Q', buf[off + 8:off + 16])[0]
        if sz == 0:
            sz = end - off
        yield off, sz, typ
        off += sz


def atom_bytes(path, kind=b'mdat'):
    with open(path, 'rb') as f:
        data = f.read()
    for o, s, t in read_atoms(data, 0, len(data)):
        if t == kind:
            return data[o:o + s]
    return None


def atom_sha256(path, kind=b'mdat'):
    blob = atom_bytes(path, kind)
    return sha256_bytes(blob) if blob is not None else None


def video_samples(path):
    """[(offset, size)] de la primera pista de vídeo."""
    with open(path, 'rb') as stream:
        length = stream.seek(0, 2)
        offset = 0
        moov = None
        while offset < length:
            stream.seek(offset)
            size, kind = struct.unpack('>I4s', stream.read(8))
            if size == 1:
                size = struct.unpack('>Q', stream.read(8))[0]
            elif size == 0:
                size = length - offset
            if size < 8 or offset + size > length:
                raise ValueError('átomo de nivel superior inválido')
            if kind == b'moov':
                stream.seek(offset)
                moov = stream.read(size)
                break
            offset += size
        else:
            raise ValueError('falta moov')
    f = moov
    trak = next((o, s) for o, s, t in read_atoms(f, 8, len(f)) if t == b'trak')

    def find(path_, o, s):
        for t in path_:
            o, s = next((oo, ss) for oo, ss, tt in read_atoms(f, o + 8, o + s) if tt == t)
        return o, s

    stbl_o, stbl_s = find([b'mdia', b'minf', b'stbl'], *trak)
    stsz = next((o, s) for o, s, t in read_atoms(f, stbl_o + 8, stbl_o + stbl_s) if t == b'stsz')
    co = next((o, s, t) for o, s, t in read_atoms(f, stbl_o + 8, stbl_o + stbl_s) if t in (b'co64', b'stco'))
    n = struct.unpack('>I', f[stsz[0] + 16:stsz[0] + 20])[0]
    sizes = struct.unpack('>%dI' % n, f[stsz[0] + 20:stsz[0] + 20 + 4 * n])
    m = struct.unpack('>I', f[co[0] + 12:co[0] + 16])[0]
    if co[2] == b'co64':
        offs = struct.unpack('>%dQ' % m, f[co[0] + 16:co[0] + 16 + 8 * m])
    else:
        offs = struct.unpack('>%dI' % m, f[co[0] + 16:co[0] + 16 + 4 * m])
    return list(zip(offs, sizes))


def split_file_chunk(chunk):
    if chunk[:4] != b'braw':
        raise ValueError('chunk sin marca braw')
    size = struct.unpack('>I', chunk[4:8])[0]
    hdr = chunk[8:8 + 120]
    planes, slices = hdr[2], hdr[3]
    n = planes * slices
    table = list(struct.unpack('>%dI' % n, chunk[0x80:0x80 + 4 * n]))
    gap = chunk[0x80 + 4 * n:FIRST_SLICE]
    bounds = table + [size]
    sl = [chunk[bounds[i]:bounds[i + 1]] for i in range(n)]
    return hdr, table, gap, sl


def frame_payloads(path):
    """Por fotograma: q, tamaños y sha del payload comprimido (slices), bmdf y cabecera."""
    samples = video_samples(path)
    with open(path, 'rb') as f:
        data = f.read()
    out = []
    for i, (off, size) in enumerate(samples):
        sample = data[off:off + size]
        bmdf = sample[:BMDF_SIZE]
        chunk = sample[BMDF_SIZE:]
        csize = struct.unpack('>I', chunk[4:8])[0]
        chunk = chunk[:csize]
        hdr, table, gap, slices = split_file_chunk(chunk)
        payload = b''.join(slices)
        out.append({
            'index': i,
            'sample_size': size,
            'chunk_size': csize,
            'q': int(hdr[1]),
            'bmdf_sha256': sha256_bytes(bmdf),
            'hdr_sha256': sha256_bytes(bytes(hdr)),
            'gap_sha256': sha256_bytes(gap),
            'payload_sha256': sha256_bytes(payload),
            'slice_sha256': [sha256_bytes(s) for s in slices],
            'slice_bytes': [len(s) for s in slices],
        })
    return out


def media_fingerprint(path):
    """Huella de la imagen almacenada: mdat + payloads (el moov puede variar entre corridas)."""
    frames = frame_payloads(path)
    return {
        'mdat_sha256': atom_sha256(path, b'mdat'),
        'moov_sha256': atom_sha256(path, b'moov'),
        'frames': [{
            'index': f['index'], 'q': f['q'], 'payload_sha256': f['payload_sha256'],
            'sample_size': f['sample_size'], 'bmdf_sha256': f['bmdf_sha256'],
            'hdr_sha256': f['hdr_sha256'],
        } for f in frames],
        'payloads_sha256': sha256_bytes(''.join(f['payload_sha256'] for f in frames).encode()),
    }


def same_media(a, b):
    """True si el contenido de imagen (mdat/payloads/q) coincide; ignora el moov."""
    fa, fb = media_fingerprint(a), media_fingerprint(b)
    if fa['mdat_sha256'] and fb['mdat_sha256']:
        return fa['mdat_sha256'] == fb['mdat_sha256']
    return fa['frames'] == fb['frames']
