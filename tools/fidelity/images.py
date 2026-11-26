"""Lectura y escritura de imágenes RGB float. Las métricas trabajan siempre sobre estos valores, sin recortar."""
import struct
import zlib

import numpy as np


class ImageError(ValueError):
    """Imagen ilegible, de otro tamaño o desalineada respecto a su pareja."""


def read_f32(path):
    """Salida de brawdec --out: cabecera "BRWD" + u32 ancho, alto, canales, planar; float32 little-endian."""
    with open(path, 'rb') as f:
        data = f.read()
    if len(data) < 20 or data[:4] != b'BRWD':
        raise ImageError('%s: no es una salida de brawdec' % path)
    w, h, c, planar = struct.unpack('<4I', data[4:20])
    if c != 3 or len(data) < 20 + 4 * w * h * c:
        raise ImageError('%s: tamaño inesperado' % path)
    a = np.frombuffer(data[20:20 + 4 * w * h * c], '<f4')
    a = a.reshape(c, h, w).transpose(1, 2, 0) if planar else a.reshape(h, w, c)
    return np.ascontiguousarray(a, dtype=np.float32)


def read_exr(path):
    """OpenEXR RGB (half o float) → float32 HxWx3, sin conversión de color."""
    try:
        import OpenEXR
        import Imath
    except ImportError as e:   # pragma: no cover - depende del entorno
        raise ImageError('Leer EXR necesita el paquete OpenEXR (make venv)') from e
    f = OpenEXR.InputFile(str(path))
    header = f.header()
    dw = header['dataWindow']
    w, h = dw.max.x - dw.min.x + 1, dw.max.y - dw.min.y + 1
    if not all(c in header['channels'] for c in 'RGB'):
        raise ImageError('%s: faltan canales R, G o B' % path)
    pt = Imath.PixelType(Imath.PixelType.FLOAT)
    planes = [np.frombuffer(f.channel(c, pt), np.float32).reshape(h, w) for c in 'RGB']
    return np.ascontiguousarray(np.stack(planes, -1)), exr_header(header)


def exr_header(header):
    """Cabecera EXR como diccionario JSON (los atributos de Resolve: ISO, espacio, timecode…)."""
    out = {}
    for key, value in header.items():
        if key == 'timeCode':
            out[key] = '%02d:%02d:%02d:%02d' % (value.hours, value.minutes, value.seconds, value.frame)
        elif isinstance(value, bytes):
            out[key] = value.decode('utf-8', 'replace')
        elif isinstance(value, (int, float, str)):
            out[key] = value
        else:
            out[key] = str(value)
    return out


def write_f32(path, img):
    """Formato de brawdec, para congelar salidas de referencia sin depender de otro formato."""
    img = np.ascontiguousarray(img, dtype='<f4')
    h, w, c = img.shape
    with open(path, 'wb') as f:
        f.write(b'BRWD' + struct.pack('<4I', w, h, c, 0) + img.tobytes())


def png_bytes(rgb8):
    """PNG RGB de 8 bits con la biblioteca estándar (solo visualización)."""
    rgb8 = np.ascontiguousarray(rgb8, dtype=np.uint8)
    if rgb8.ndim == 2:
        rgb8 = np.repeat(rgb8[..., None], 3, -1)
    h, w = rgb8.shape[:2]
    raw = b''.join(b'\0' + rgb8[y].tobytes() for y in range(h))

    def chunk(tag, data):
        return struct.pack('>I', len(data)) + tag + data + struct.pack('>I', zlib.crc32(tag + data) & 0xffffffff)
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0)) +
            chunk(b'IDAT', zlib.compress(raw, 6)) + chunk(b'IEND', b''))


def write_png(path, rgb8):
    with open(path, 'wb') as f:
        f.write(png_bytes(rgb8))


def check_pair(reference, candidate):
    """Rechaza parejas incomparables: tamaño, canales o valores no finitos."""
    if reference.ndim != 3 or reference.shape[2] != 3 or candidate.ndim != 3 or candidate.shape[2] != 3:
        raise ImageError('las imágenes deben ser RGB (HxWx3)')
    if reference.shape != candidate.shape:
        raise ImageError('tamaños distintos: referencia %s, candidata %s' % (reference.shape[:2], candidate.shape[:2]))
    for name, img in (('referencia', reference), ('candidata', candidate)):
        bad = int(np.count_nonzero(~np.isfinite(img)))
        if bad:
            raise ImageError('%s: %d valores no finitos' % (name, bad))


def _highpass(y):
    return y - (np.roll(y, 1, 0) + np.roll(y, -1, 0) + np.roll(y, 1, 1) + np.roll(y, -1, 1)) / 4


def alignment(reference, candidate, search=8):
    """Desplazamiento entero (dy, dx) que mejor alinea la candidata con la referencia, por correlación de fase del
    detalle de luminancia, y la correlación del detalle en ese desplazamiento."""
    a = _highpass(np.log2(np.maximum(reference.mean(-1), 1e-4)))
    b = _highpass(np.log2(np.maximum(candidate.mean(-1), 1e-4)))
    fa, fb = np.fft.rfft2(a), np.fft.rfft2(b)
    cross = fa * np.conj(fb)
    cross /= np.maximum(np.abs(cross), 1e-12)
    corr = np.fft.irfft2(cross, a.shape)
    h, w = a.shape
    best, shift = -np.inf, (0, 0)
    for dy in range(-search, search + 1):
        for dx in range(-search, search + 1):
            v = corr[dy % h, dx % w]
            if v > best:
                best, shift = v, (dy, dx)
    bb = np.roll(b, shift, (0, 1))
    m = (slice(search, -search), slice(search, -search))
    cc = float(np.corrcoef(a[m].ravel(), bb[m].ravel())[0, 1])
    return shift, cc


def check_alignment(reference, candidate, min_correlation):
    """La candidata debe coincidir con la referencia sin desplazamiento y con detalle correlado.

    CDNG_ALIGN_SOFT=1 (diagnóstico): no lanza; marca gate_failed. El umbral de aceptación no cambia.

    EXP-003: separa alineación geométrica de fidelidad de detalle. Una variante con detalle degradado
    pero shift=(0,0) se marca quality_rejected / detail_gate_pass=False, no como desalineación geométrica.
    """
    import os
    shift, cc = alignment(reference, candidate)
    geometric_aligned = shift == (0, 0)
    detail_gate_pass = bool(cc >= min_correlation)
    out = {
        'shift': list(shift),
        'detail_correlation': float(cc),
        'gate_min_correlation': float(min_correlation),
        'geometric_aligned': geometric_aligned,
        'detail_gate_pass': detail_gate_pass,
        'quality_rejected': bool(geometric_aligned and not detail_gate_pass),
    }
    soft = os.environ.get('CDNG_ALIGN_SOFT') not in (None, '', '0')
    if not geometric_aligned:
        out['gate_failed'] = True
        out['gate_reason'] = 'shift'
        if soft:
            return out
        raise ImageError('imágenes desalineadas: desplazamiento (dy, dx) = %s' % (shift,))
    if not detail_gate_pass:
        out['gate_failed'] = True
        out['gate_reason'] = 'detail_correlation'
        if soft:
            return out
        raise ImageError('el detalle no se corresponde (correlación %.3f < %.3f): ¿otro fotograma u otra escena?' % (cc, min_correlation))
    out['gate_failed'] = False
    out['gate_reason'] = None
    return out
