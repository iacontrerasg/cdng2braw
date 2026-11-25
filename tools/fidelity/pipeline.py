"""Conversión con el worker (cdng-convert) y decodificación con brawdec, con caché direccionada por contenido.

La clave de caché es el sha256 de todo lo que determina la salida: binario y modelos del worker, sha de cada DNG del
tramo, petición de codificación, variables CDNG_* experimentales efectivas; y para la decodificación, sha del BRAW,
brawdec, framework del SDK, perfiles de Camera Support y ajustes de revelado. Antes de reutilizar se relee
identity.json y se recalcula el sha del fichero: si algo no coincide es un error, nunca una reutilización.
"""
import json
import os
import re
import shutil
import subprocess
import time

from . import identity
from .images import read_f32


class CacheError(RuntimeError):
    pass


class PipelineError(RuntimeError):
    pass


# Variables CDNG_* que pueden cambiar el BRAW o la imagen. Sus valores efectivos (o null si no aplica) entran en la
# identidad de caché y en setup. Añadir aquí cualquier nueva opción experimental; no hace falta tocar el deblocking.
OUTPUT_ENV = (
    'CDNG_RATE',           # search: búsqueda exacta de q por fotograma en modo ratio
    'CDNG_NOISE_SYNTH',    # desactiva o fuerza la síntesis de ruido
    'CDNG_DEADZONE',       # zona muerta del cuantizador AC
    'CDNG_GREEN',          # demosaic del verde (dir = media direccional)
    'CDNG_CHROMA_FILTER',  # longitud del filtro de croma
    'CDNG_RESIZE_CHROMA',  # sites = reescalado sin corregir fase
    'CDNG_SHARPEN',        # nitidez nativa al ampliar
    'CDNG_SDK_TOE',        # pie de la curva de señal
    'CDNG_NR_THREADS',     # hilos de NR (puede alterar el resultado con NR activo)
    'CDNG_RESOURCES',      # raíz de model/; si se hereda, no es el worker empaquetado
)

# Diagnóstico o i18n: no deben alterar el BRAW. Si están en el entorno se registran y se quitan al lanzar el worker.
DIAGNOSTIC_ENV = (
    'CDNG_PIPELINE_TRACE',
    'CDNG_PROFILE_LOG',
    'CDNG_DUMP_CANVAS',
    'CDNG_LANGUAGE',
)


class Log:
    def __init__(self, path=None, echo=True):
        self.path, self.echo = path, echo
        if path:
            os.makedirs(os.path.dirname(path), exist_ok=True)

    def __call__(self, message):
        line = time.strftime('%H:%M:%S ') + message
        if self.echo:
            print(line, flush=True)
        if self.path:
            with open(self.path, 'a', encoding='utf-8') as f:
                f.write(line + '\n')


def ranges(frames, frame_count, max_gap=2):
    """Agrupa fotogramas en tramos contiguos a convertir (el worker necesita ≥ 2 DNG y rellena huecos con negro,
    así que cada tramo es contiguo). Devuelve [(inicio, cuenta, [fotogramas])]."""
    out = []
    for f in sorted(set(frames)):
        if out and f - (out[-1][0] + out[-1][1] - 1) <= max_gap:
            start = out[-1][0]
            out[-1] = (start, f - start + 1, out[-1][2] + [f])
        else:
            out.append((f, 1, [f]))
    fixed = []
    for start, count, fs in out:
        if count < 2:
            if start + 1 < frame_count:
                count = 2
            elif start > 0:
                start, count = start - 1, 2
            else:
                raise PipelineError('el clip tiene un solo fotograma: el worker necesita al menos dos')
        fixed.append((start, count, fs))
    return fixed


def ambient_cdng(environ=None):
    """{nombre: valor} de las CDNG_* presentes en el entorno."""
    env = os.environ if environ is None else environ
    return {k: v for k, v in env.items() if k.startswith('CDNG_')}


def encode_request(encode, environ=None):
    """Petición del worker + variables experimentales efectivas.

    encode.experimental_env declara overrides explícitos (p. ej. {"CDNG_DEADZONE": "0.25"}). Las CDNG_* del proceso
    que afectan al resultado y no estén declaradas (ni derivadas de rate_search) provocan error: no hay overrides
    silenciosos. Las de diagnóstico se registran y se eliminan al lanzar el worker.
    """
    req = {'compression': encode['compression'], 'workers': int(encode.get('workers', 2)), 'audio': False,
           'gyro_braw': False, 'gyro_project': False, 'gyro_gcsv': False, 'gyro_fsg2': False,
           'noise_synthesis': bool(encode.get('noise_synthesis', True)),
           'nr_luma': encode.get('nr_luma', 0), 'nr_chroma': encode.get('nr_chroma', 0)}
    if encode['compression'] == 'quality':
        req['q'] = int(encode['q'])
    else:
        req['ratio'] = float(encode['ratio'])

    declared = dict(encode.get('experimental_env') or {})
    unknown = sorted(set(declared) - set(OUTPUT_ENV) - set(DIAGNOSTIC_ENV))
    if unknown:
        raise PipelineError('experimental_env declara variables desconocidas: %s (añádelas a OUTPUT_ENV o DIAGNOSTIC_ENV)' %
                            ', '.join(unknown))
    if any(k in declared for k in DIAGNOSTIC_ENV):
        raise PipelineError('experimental_env no admite variables de diagnóstico (%s); quítalas del entorno o ignóralas' %
                            ', '.join(DIAGNOSTIC_ENV))

    effective = {name: None for name in OUTPUT_ENV}
    if encode['compression'] == 'ratio' and encode.get('rate_search', True):
        effective['CDNG_RATE'] = 'search'
    for name, value in declared.items():
        if name in OUTPUT_ENV:
            effective[name] = None if value is None else str(value)

    ambient = ambient_cdng(environ)
    surprises = []
    for name, value in sorted(ambient.items()):
        if name in DIAGNOSTIC_ENV:
            continue
        if name not in OUTPUT_ENV:
            surprises.append('%s=%s (no catalogada: declárala en OUTPUT_ENV o quítala)' % (name, value))
            continue
        want = effective.get(name)
        if want is None:
            # Afecta al resultado y el banco no la ha pedido: override silencioso.
            surprises.append('%s=%s (afecta al resultado y no está en experimental_env)' % (name, value))
        elif want != value:
            surprises.append('%s: entorno %s ≠ valor efectivo %s' % (name, value, want))
    if surprises:
        raise PipelineError('variables CDNG_* heredadas no declaradas (evita overrides silenciosos):\n  ' +
                            '\n  '.join(surprises))

    # Solo las que tienen valor concreto van al entorno del proceso hijo.
    env = {k: v for k, v in effective.items() if v is not None}
    diagnostic = {k: ambient[k] for k in DIAGNOSTIC_ENV if k in ambient}
    return req, effective, env, diagnostic


def _verify(directory, material, payload):
    path = os.path.join(directory, 'identity.json')
    try:
        with open(path, encoding='utf-8') as f:
            saved = json.load(f)
    except (OSError, ValueError) as e:
        raise CacheError('%s: identidad ilegible (%s); bórralo o usa --no-cache' % (directory, e))
    if saved.get('material') != material:
        raise CacheError('%s: la identidad guardada no coincide con la pedida; bórralo o usa --no-cache' % directory)
    got = identity.sha256_file(os.path.join(directory, payload))
    if got != saved.get('sha256'):
        raise CacheError('%s: %s cambió desde que se guardó (sha %s ≠ %s)' % (directory, payload, got[:12], str(saved.get('sha256'))[:12]))
    return saved


def conversion_material(converter_id, clip_dir, staged, start, req, effective_env):
    """Identidad de caché de un BRAW: worker, DNG del tramo, petición y entorno experimental efectivo."""
    return {'kind': 'braw', 'info_schema': 2,
            'converter': {'cdng_convert_sha256': converter_id['cdng_convert_sha256'], 'model': converter_id['model']},
            'clip': os.path.basename(clip_dir), 'first_frame': start,
            'inputs': [{'index': start + i, 'name': os.path.basename(f), 'sha256': identity.sha256_file(f)}
                       for i, f in enumerate(staged)],
            'request': req, 'experimental_env': effective_env}


def worker_environ(process_env):
    """Entorno del hijo: hereda el proceso y quita toda CDNG_* (las experimentales se añaden después)."""
    return {k: v for k, v in process_env.items() if not k.startswith('CDNG_')}


def convert(converter, converter_id, clip_dir, files, start, count, encode, cache, log, no_cache=False):
    """Convierte los DNG [start, start + count) de un clip. Devuelve la entrada de caché (ruta, sha, evento final)."""
    staged = files[start:start + count]
    req, effective, env, diagnostic = encode_request(encode)
    if diagnostic:
        log('aviso: CDNG_* de diagnóstico en el entorno (no se pasan al worker): %s' %
            ', '.join('%s=%s' % kv for kv in sorted(diagnostic.items())))
    material = conversion_material(converter_id, clip_dir, staged, start, req, effective)
    key = identity.sha256_json(material)
    directory = os.path.join(cache, 'braw', key[:24])
    if os.path.isdir(directory) and not no_cache:
        saved = _verify(directory, material, 'clip.braw')
        log('BRAW reutilizado %s (%s, fotogramas %d–%d; worker %s; identidad verificada)' % (
            key[:12], material['clip'], start, start + count - 1, converter_id['cdng_convert_sha256'][:12]))
        return dict(saved, path=os.path.join(directory, 'clip.braw'), key=key, reused=True,
                    experimental_env=effective, inputs=material['inputs'])
    if os.path.isdir(directory):
        shutil.rmtree(directory)
    tmp = directory + '.tmp'
    shutil.rmtree(tmp, ignore_errors=True)
    src = os.path.join(tmp, 'staging', os.path.basename(clip_dir))
    os.makedirs(src)
    for f in staged:
        os.symlink(os.path.realpath(f), os.path.join(src, os.path.basename(f)))
    out = os.path.join(tmp, 'clip.braw')
    request = dict(req, input=src, output=out)
    with open(os.path.join(tmp, 'request.json'), 'w') as f:
        json.dump(request, f, indent=1)
    rate_note = ''
    if env.get('CDNG_RATE') == 'search':
        rate_note = ' búsqueda exacta'
    exp_note = ''
    active = {k: v for k, v in effective.items() if v is not None}
    if active:
        exp_note = '; experimental %s' % ','.join('%s=%s' % kv for kv in sorted(active.items()))
    log('convirtiendo %s fotogramas %d–%d (%s%s%s) con worker %s' % (
        material['clip'], start, start + count - 1,
        'q %s' % req['q'] if 'q' in req else 'ratio %s:1' % req['ratio'], rate_note, exp_note,
        converter_id['cdng_convert_sha256'][:12]))
    child_env = worker_environ(os.environ)
    child_env.update(env)
    t0 = time.monotonic()
    r = subprocess.run([os.path.join(converter, 'cdng-convert'), '--request', os.path.join(tmp, 'request.json')],
                       capture_output=True, text=True, env=child_env)
    seconds = time.monotonic() - t0
    with open(os.path.join(tmp, 'convert.log'), 'w', encoding='utf-8') as f:
        f.write(r.stdout + ('\n--- stderr ---\n' + r.stderr if r.stderr else ''))
    events = []
    for line in r.stdout.splitlines():
        try:
            events.append(json.loads(line))
        except ValueError:
            pass
    done = [e for e in events if e.get('event') == 'completed']
    if r.returncode or not done or not os.path.isfile(out):
        errors = [e.get('message') for e in events if e.get('event') == 'error']
        raise PipelineError('el worker falló (código %d): %s; ver %s' % (r.returncode, errors or r.stderr.strip()[-500:], os.path.join(tmp, 'convert.log')))
    if done[0].get('replaced_frames'):
        raise PipelineError('el worker sustituyó fotogramas %s por negro' % done[0]['replaced_frames'])
    shutil.rmtree(os.path.join(tmp, 'staging'))
    saved = {'material': material, 'sha256': identity.sha256_file(out), 'completed': done[0], 'seconds': seconds}
    with open(os.path.join(tmp, 'identity.json'), 'w') as f:
        json.dump(saved, f, indent=1, sort_keys=True)
    os.rename(tmp, directory)
    return dict(saved, path=os.path.join(directory, 'clip.braw'), key=key, reused=False,
                experimental_env=effective, inputs=material['inputs'])


def decode_args(settings):
    """Ajustes de revelado → argumentos de brawdec. iso/kelvin/tint ausentes = As Shot del clip."""
    args = ['--gamut', settings['gamut'], '--gamma', settings['gamma'], '--csgen', str(settings['color_science_gen']),
            '--clip-number', 'hlry', str(settings['highlight_recovery']), '--scale', settings.get('scale', 'full')]
    for key in ('iso', 'exposure', 'kelvin', 'tint'):
        value = settings.get(key)
        if value is not None and value != 'as_shot':
            args += ['--' + key, str(value)]
    return args


def _parse_effective_value(raw):
    if raw.startswith('"') and raw.endswith('"'):
        return raw[1:-1]
    try:
        if '.' in raw or 'e' in raw.lower():
            return float(raw)
        return int(raw)
    except ValueError:
        try:
            return float(raw)
        except ValueError:
            return raw


def parse_frame_info(text):
    """Atributos efectivos reportados por brawdec --frame-info (clip + fotograma + escala)."""
    info = {'clip': {}, 'frame': {}, 'scale': None, 'frame_bytes': None}
    m = re.search(r'imagen en el fichero (\d+) bytes', text)
    if m:
        info['frame_bytes'] = int(m.group(1))
    m = re.search(r'^effective scale (\S+)\s*$', text, re.M)
    if m:
        info['scale'] = m.group(1)
    for m in re.finditer(r'effective clip (\w+) type \d+ status (\S+) value (.+)$', text, re.M):
        name, status, value = m.group(1), m.group(2), m.group(3).strip()
        entry = {'status': status}
        if status == '0' or status == '0x0':
            entry['value'] = _parse_effective_value(value)
        info['clip'][name] = entry
    for key, name in (('wbkv', 'kelvin'), ('wbtn', 'tint'), ('fiso', 'iso'), ('expo', 'exposure'), ('agpf', 'agpf')):
        m = re.search(r'effective %s type \d+ status (\S+) value (\S+)' % key, text)
        if m:
            entry = {'status': m.group(1)}
            if m.group(1) in ('0', '0x0'):
                entry['value'] = float(m.group(2))
            info['frame'][name] = entry
    # Compatibilidad con logs antiguos (hlry vía clip attribute …).
    if 'hlry' not in info['clip']:
        m = re.search(r'clip attribute hlry=(\S+) type \d+ status (\S+)', text)
        if m and m.group(2) in ('0', '0x0'):
            info['clip']['hlry'] = {'status': m.group(2), 'value': float(m.group(1))}
    return info


def verify_decode(settings, info):
    """Compara lo solicitado con lo que brawdec reportó. Distingue aplicado / no verificable / error."""
    requested = dict(settings)
    applied, unverifiable, errors = {}, [], []

    def take_clip(name, want, cast=lambda x: x):
        entry = info['clip'].get(name)
        if not entry or 'value' not in entry:
            unverifiable.append(name)
            return
        got = cast(entry['value'])
        applied[name] = got
        if want is not None and got != want and str(got) != str(want):
            errors.append('%s: solicitado %r, SDK %r' % (name, want, got))

    take_clip('gamut', settings.get('gamut'), str)
    take_clip('gamma', settings.get('gamma'), str)
    take_clip('csgn', settings.get('color_science_gen'), lambda v: int(v))
    take_clip('hlry', settings.get('highlight_recovery'), lambda v: float(v))

    if info.get('scale') is None:
        unverifiable.append('scale')
    else:
        applied['scale'] = info['scale']
        if settings.get('scale', 'full') != info['scale']:
            errors.append('scale: solicitado %r, SDK %r' % (settings.get('scale', 'full'), info['scale']))

    for name, setting_key in (('iso', 'iso'), ('exposure', 'exposure'), ('kelvin', 'kelvin'), ('tint', 'tint')):
        want = settings.get(setting_key)
        entry = info['frame'].get(name)
        if not entry or 'value' not in entry:
            unverifiable.append(name)
            continue
        got = entry['value']
        applied[name] = got
        if want is None or want == 'as_shot':
            # As Shot: no hay valor numérico solicitado; solo comprobamos que el SDK dio un valor.
            continue
        if abs(float(got) - float(want)) > 1e-6:
            errors.append('%s: solicitado %s, SDK %s' % (name, want, got))

    return {'requested': requested, 'applied': applied, 'unverifiable': sorted(set(unverifiable)),
            'ok': not errors, 'errors': errors}


def decode(brawdec, framework, decoder_id, braw, braw_sha, index, settings, cache, log, no_cache=False):
    """Revela un fotograma del BRAW a RGB float. Devuelve (imagen, entrada de caché)."""
    # info_schema 2: frame_info anidado + decode_verification (invalida cachés del schema 1).
    material = {'kind': 'decode', 'info_schema': 2, 'decoder': decoder_id, 'braw_sha256': braw_sha,
                'index': index, 'settings': settings}
    key = identity.sha256_json(material)
    directory = os.path.join(cache, 'decode', key[:24])
    if os.path.isdir(directory) and not no_cache:
        saved = _verify(directory, material, 'image.f32')
        log('revelado reutilizado %s (BRAW %s índice %d; identidad verificada)' % (key[:12], braw_sha[:12], index))
        return read_f32(os.path.join(directory, 'image.f32')), dict(saved, path=os.path.join(directory, 'image.f32'), key=key, reused=True)
    if os.path.isdir(directory):
        shutil.rmtree(directory)
    tmp = directory + '.tmp'
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    out = os.path.join(tmp, 'image.f32')
    cmd = [brawdec, braw, str(index), '--framework', framework, '--frame-info', '--out', out] + decode_args(settings)
    t0 = time.monotonic()
    r = subprocess.run(cmd, capture_output=True, text=True)
    seconds = time.monotonic() - t0
    with open(os.path.join(tmp, 'decode.log'), 'w', encoding='utf-8') as f:
        f.write(' '.join(cmd) + '\n' + r.stdout + r.stderr)
    if r.returncode or not os.path.isfile(out):
        raise PipelineError('brawdec falló (código %d): %s' % (r.returncode, (r.stdout + r.stderr).strip()[-500:]))
    info = parse_frame_info(r.stdout + r.stderr)
    verification = verify_decode(settings, info)
    if not verification['ok']:
        raise PipelineError('el revelado no coincide con lo solicitado: %s' % '; '.join(verification['errors']))
    if verification['unverifiable']:
        log('aviso: atributos de revelado no verificables en la salida de brawdec: %s' %
            ', '.join(verification['unverifiable']))
    saved = {'material': material, 'sha256': identity.sha256_file(out), 'frame_info': info,
             'decode_verification': verification, 'seconds': seconds}
    with open(os.path.join(tmp, 'identity.json'), 'w') as f:
        json.dump(saved, f, indent=1, sort_keys=True)
    os.rename(tmp, directory)
    return read_f32(os.path.join(directory, 'image.f32')), dict(saved, path=os.path.join(directory, 'image.f32'), key=key, reused=False)
