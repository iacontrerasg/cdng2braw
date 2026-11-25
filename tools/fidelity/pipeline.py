"""Conversión con el worker (cdng-convert) y decodificación con brawdec, con caché direccionada por contenido.

La clave de caché es el sha256 de todo lo que determina la salida: binario y modelos del worker, sha de cada DNG,
ajustes de codificación; y para la decodificación, sha del BRAW, brawdec, framework del SDK, perfiles de Camera
Support y ajustes de revelado. Antes de reutilizar se relee identity.json y se recalcula el sha del fichero: si algo
no coincide es un error, nunca una reutilización. Cada reutilización queda escrita en el registro de la ejecución.
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


def encode_request(encode):
    """Campos de la petición del worker que fijan la salida (además de las entradas)."""
    req = {'compression': encode['compression'], 'workers': int(encode.get('workers', 2)), 'audio': False,
           'gyro_braw': False, 'gyro_project': False, 'gyro_gcsv': False, 'gyro_fsg2': False,
           'noise_synthesis': bool(encode.get('noise_synthesis', True)),
           'nr_luma': encode.get('nr_luma', 0), 'nr_chroma': encode.get('nr_chroma', 0)}
    if encode['compression'] == 'quality':
        req['q'] = int(encode['q'])
    else:
        req['ratio'] = float(encode['ratio'])
    env = {}
    if encode['compression'] == 'ratio' and encode.get('rate_search', True):
        env['CDNG_RATE'] = 'search'
    return req, env


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


def convert(converter, converter_id, clip_dir, files, start, count, encode, cache, log, no_cache=False):
    """Convierte los DNG [start, start + count) de un clip. Devuelve la entrada de caché (ruta, sha, evento final)."""
    staged = files[start:start + count]
    req, env = encode_request(encode)
    material = {'kind': 'braw', 'converter': {'cdng_convert_sha256': converter_id['cdng_convert_sha256'], 'model': converter_id['model']},
                'clip': os.path.basename(clip_dir), 'first_frame': start,
                'inputs': [{'name': os.path.basename(f), 'sha256': identity.sha256_file(f)} for f in staged],
                'request': req, 'env': env}
    key = identity.sha256_json(material)
    directory = os.path.join(cache, 'braw', key[:24])
    if os.path.isdir(directory) and not no_cache:
        saved = _verify(directory, material, 'clip.braw')
        log('BRAW reutilizado %s (%s, fotogramas %d–%d; worker %s; identidad verificada)' % (
            key[:12], material['clip'], start, start + count - 1, converter_id['cdng_convert_sha256'][:12]))
        return dict(saved, path=os.path.join(directory, 'clip.braw'), key=key, reused=True)
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
    log('convirtiendo %s fotogramas %d–%d (%s) con worker %s' % (material['clip'], start, start + count - 1,
        'q %s' % req['q'] if 'q' in req else 'ratio %s:1%s' % (req['ratio'], ' búsqueda exacta' if env else ''), converter_id['cdng_convert_sha256'][:12]))
    t0 = time.monotonic()
    r = subprocess.run([os.path.join(converter, 'cdng-convert'), '--request', os.path.join(tmp, 'request.json')],
                       capture_output=True, text=True, env=dict(os.environ, **env))
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
    return dict(saved, path=os.path.join(directory, 'clip.braw'), key=key, reused=False)


def decode_args(settings):
    """Ajustes de revelado → argumentos de brawdec. iso/kelvin/tint ausentes = As Shot del clip."""
    args = ['--gamut', settings['gamut'], '--gamma', settings['gamma'], '--csgen', str(settings['color_science_gen']),
            '--clip-number', 'hlry', str(settings['highlight_recovery']), '--scale', settings.get('scale', 'full')]
    for key in ('iso', 'exposure', 'kelvin', 'tint'):
        value = settings.get(key)
        if value is not None and value != 'as_shot':
            args += ['--' + key, str(value)]
    return args


def parse_frame_info(text):
    info = {}
    m = re.search(r'imagen en el fichero (\d+) bytes', text)
    if m:
        info['frame_bytes'] = int(m.group(1))
    for key, name in (('wbkv', 'kelvin'), ('wbtn', 'tint'), ('fiso', 'iso'), ('expo', 'exposure')):
        m = re.search(r'effective %s type \d+ status 0 value (\S+)' % key, text)
        if m:
            info[name] = float(m.group(1))
    m = re.search(r'clip attribute hlry=(\S+) .*status 0', text)
    if m:
        info['highlight_recovery'] = float(m.group(1))
    return info


def decode(brawdec, framework, decoder_id, braw, braw_sha, index, settings, cache, log, no_cache=False):
    """Revela un fotograma del BRAW a RGB float. Devuelve (imagen, entrada de caché)."""
    material = {'kind': 'decode', 'decoder': decoder_id, 'braw_sha256': braw_sha, 'index': index, 'settings': settings}
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
    for key_, want in (('iso', settings.get('iso')), ('exposure', settings.get('exposure'))):
        if isinstance(want, (int, float)) and key_ in info and abs(info[key_] - want) > 1e-6:
            raise PipelineError('el SDK aplicó %s = %s en lugar de %s' % (key_, info[key_], want))
    if 'highlight_recovery' in info and info['highlight_recovery'] != settings['highlight_recovery']:
        raise PipelineError('el SDK dejó highlight recovery en %s' % info['highlight_recovery'])
    saved = {'material': material, 'sha256': identity.sha256_file(out), 'frame_info': info, 'seconds': seconds}
    with open(os.path.join(tmp, 'identity.json'), 'w') as f:
        json.dump(saved, f, indent=1, sort_keys=True)
    os.rename(tmp, directory)
    return read_f32(os.path.join(directory, 'image.f32')), dict(saved, path=os.path.join(directory, 'image.f32'), key=key, reused=False)
