"""Manifiesto del dataset (benchmark/manifest.json): carga, selección y validación contra los metadatos de los DNG."""
import glob
import json
import math
import os
import struct

STATUSES = ('complete', 'pending')
REQUIRED = ('id', 'series', 'category', 'status', 'clip', 'iso', 'baseline_exposure', 'white_balance', 'frames', 'references')


class ManifestError(ValueError):
    pass


def load(path):
    with open(path, encoding='utf-8') as f:
        doc = json.load(f)
    doc['_path'] = os.path.abspath(path)
    problems = structure(doc)
    if problems:
        raise ManifestError('%s:\n  %s' % (path, '\n  '.join(problems)))
    return doc


def structure(doc):
    """Coherencia interna, sin tocar el dataset."""
    p = []
    if doc.get('schema') != 1:
        p.append('schema debe ser 1')
    for key in ('encode', 'decode', 'analysis', 'visualization', 'camera', 'tests'):
        if key not in doc:
            p.append('falta la sección %s' % key)
    if p:
        return p
    enc = doc['encode']
    if enc.get('compression') not in ('quality', 'ratio'):
        p.append('encode.compression debe ser quality o ratio')
    seen = set()
    for t in doc['tests']:
        tid = t.get('id', '?')
        for key in REQUIRED:
            if key not in t:
                p.append('%s: falta %s' % (tid, key))
        if tid in seen:
            p.append('%s: id repetido' % tid)
        seen.add(tid)
        if t.get('status') not in STATUSES:
            p.append('%s: status %r (válidos: %s)' % (tid, t.get('status'), ', '.join(STATUSES)))
        if t.get('status') == 'complete' and not t.get('frames'):
            p.append('%s: completa pero sin fotogramas' % tid)
        frames = t.get('frames') or []
        if len(set(frames)) != len(frames) or any(not isinstance(f, int) or f < 0 for f in frames):
            p.append('%s: fotogramas inválidos %s' % (tid, frames))
        for ref in t.get('references') or []:
            if ref.get('frame') not in frames:
                p.append('%s: la referencia %s es del fotograma %s, que no está en frames' % (tid, ref.get('file'), ref.get('frame')))
            unknown = set(ref.get('decode', {})) - {'exposure', 'iso', 'kelvin', 'tint'}
            if unknown:
                p.append('%s: ajustes de revelado no admitidos en la referencia: %s' % (tid, sorted(unknown)))
        if t.get('scene') and t['scene'] not in doc.get('scenes', {}):
            p.append('%s: escena %r no declarada' % (tid, t['scene']))
    return p


def select(doc, tests=None, series=None, include_pending=False):
    """Pruebas por id (lista) o serie (lista); por defecto solo las completas."""
    chosen = []
    for t in doc['tests']:
        if tests and t['id'] not in tests:
            continue
        if series and t['series'] not in series:
            continue
        if t['status'] != 'complete' and not include_pending and not tests:
            continue
        chosen.append(t)
    if tests:
        missing = set(tests) - {t['id'] for t in chosen}
        if missing:
            raise ManifestError('pruebas desconocidas: %s' % ', '.join(sorted(missing)))
    return chosen


def iso_for_baseline_exposure(be):
    """Modelo ISO de la Pocket OG (docs/pocket-og-exposure.md): ISO = 400 · 2^(BE − 1.1)."""
    return 400 * 2 ** (be - 1.1)


def clip_dir(root, clip):
    return os.path.join(os.path.expanduser(root), clip)


def dng_files(directory):
    return sorted(glob.glob(os.path.join(directory, '*.dng')))


def dng_tags(path):
    """BaselineExposure, AsShotNeutral y TimeCodes del IFD0 de un DNG."""
    with open(path, 'rb') as f:
        b = f.read()
    e = '<' if b[:2] == b'II' else '>'
    o = struct.unpack(e + 'I', b[4:8])[0]
    n = struct.unpack(e + 'H', b[o:o + 2])[0]
    out = {}
    for i in range(n):
        t, ty, c, v = struct.unpack(e + 'HHII', b[o + 2 + 12 * i:o + 14 + 12 * i])
        if t == 50730:
            x = struct.unpack(e + 'ii', b[v:v + 8]); out['baseline_exposure'] = x[0] / x[1]
        elif t == 50728:
            x = struct.unpack(e + '6I', b[v:v + 24]); out['as_shot_neutral'] = [x[2 * k] / x[2 * k + 1] for k in range(3)]
        elif t == 51043:
            d = b[v:v + 8]

            def bcd(x):
                return (x >> 4) * 10 + (x & 15)
            out['timecode'] = '%02d:%02d:%02d:%02d' % (bcd(d[3]), bcd(d[2] & 0x7f), bcd(d[1] & 0x7f), bcd(d[0] & 0x3f))
    return out


def against_dataset(doc, root, references_root, tests, require_references=True):
    """Contrasta cada prueba con los DNG reales y, si require_references, con los EXR. Devuelve (errores, avisos)."""
    errors, warnings = [], []
    for t in tests:
        tid = t['id']
        d = clip_dir(root, t['clip'])
        files = dng_files(d)
        if not files:
            errors.append('%s: no hay DNG en %s' % (tid, d))
            continue
        if t.get('frame_count') is not None and len(files) != t['frame_count']:
            errors.append('%s: %d DNG en disco, el manifiesto dice %d' % (tid, len(files), t['frame_count']))
        for f in t['frames']:
            if f >= len(files):
                errors.append('%s: fotograma %d fuera de rango (0–%d)' % (tid, f, len(files) - 1))
        sample = sorted({0, len(files) - 1} | {f for f in t['frames'] if f < len(files)})
        for k in sample:
            tags = dng_tags(files[k])
            be = tags.get('baseline_exposure')
            if be is None or not math.isclose(be, t['baseline_exposure'], abs_tol=1e-6):
                errors.append('%s: fotograma %d tiene BaselineExposure %s, el manifiesto %s' % (tid, k, be, t['baseline_exposure']))
            elif round(iso_for_baseline_exposure(be)) != t['iso']:
                errors.append('%s: BaselineExposure %s implica ISO %d, el manifiesto dice %s' % (tid, be, round(iso_for_baseline_exposure(be)), t['iso']))
            neutral = t['white_balance'].get('as_shot_neutral')
            got = tags.get('as_shot_neutral')
            if neutral and (not got or any(abs(a - b) > 1e-4 for a, b in zip(neutral, got))):
                errors.append('%s: fotograma %d tiene AsShotNeutral %s, el manifiesto %s' % (tid, k, got, neutral))
        for ref in t['references']:
            path = os.path.join(references_root, ref['file']) if references_root else None
            missing = not path or not os.path.isfile(path)
            if missing and require_references:
                (errors if t['status'] == 'complete' else warnings).append('%s: falta la referencia %s' % (tid, path or ref['file']))
            elif missing:
                warnings.append('%s: referencia %s ausente (modo sin EXR)' % (tid, path or ref['file']))
        if t['status'] == 'pending':
            missing = [k for k in ('aperture',) if t.get(k) is None]
            if t['white_balance'].get('kelvin') is None and t['category'] == 'white_balance':
                missing.append('white_balance.kelvin')
            if not t['frames']:
                missing.append('frames')
            warnings.append('%s: pendiente de completar (%s)' % (tid, ', '.join(missing) or 'revisar notas'))
    return errors, warnings


def resolve_paths(doc, dataset=None, references=None):
    """Raíz del dataset y de las referencias: argumentos explícitos, o los valores del manifiesto."""
    root = os.path.abspath(os.path.expanduser(dataset or doc['dataset']['default_root']))
    refs = references or doc['dataset'].get('references_root')
    if refs and not os.path.isabs(os.path.expanduser(refs)):
        refs = os.path.normpath(os.path.join(root, refs))
    return root, os.path.abspath(os.path.expanduser(refs)) if refs else None
