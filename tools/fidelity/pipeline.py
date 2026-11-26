"""Conversión con el worker (cdng-convert) y decodificación con brawdec, con caché direccionada por contenido.

La clave de caché es el sha256 de todo lo que determina la salida: binario y modelos del worker, sha de cada DNG del
tramo, petición de codificación, variables CDNG_* experimentales efectivas; y para la decodificación, sha del BRAW,
brawdec, framework del SDK, perfiles de Camera Support y ajustes de revelado. Antes de reutilizar se relee
identity.json y se recalcula el sha del fichero: si algo no coincide es un error, nunca una reutilización.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import time

from . import braw_inspect, identity
from .images import read_f32


class CacheError(RuntimeError):
    pass


class PipelineError(RuntimeError):
    pass


# Variables CDNG_* que pueden cambiar el BRAW o la imagen. Sus valores efectivos (o null si no aplica) entran en la
# identidad de caché y en setup. Añadir aquí cualquier nueva opción experimental.
OUTPUT_ENV = (
    'CDNG_RATE',                 # search: búsqueda exacta de q por fotograma en modo ratio
    'CDNG_NOISE_SYNTH',          # desactiva o fuerza la síntesis de ruido
    'CDNG_DEADZONE',             # zona muerta del cuantizador AC (uniforme legacy)
    'CDNG_DEADZONE_POLICY',      # EXP-003: ruta JSON política AC (mutuamente excluyente con DEADZONE)
    'CDNG_GREEN',                # demosaic del verde (dir = media direccional)
    'CDNG_CHROMA_FILTER',        # longitud del filtro de croma
    'CDNG_RESIZE_CHROMA',        # sites = reescalado sin corregir fase
    'CDNG_SHARPEN',              # nitidez nativa al ampliar
    'CDNG_SDK_TOE',              # pie de la curva de señal
    'CDNG_NR_THREADS',           # hilos de NR (puede alterar el resultado con NR activo)
    'CDNG_RESOURCES',            # raíz de model/; si se hereda, no es el worker empaquetado
    'CDNG_OG_DEBLOCK_PRECOMP',   # Pocket OG: 1/on (defecto) o 0/off — precompensación antibloques
    'CDNG_OG_DEBLOCK_ALPHA',     # EXP-001b: intensidad ∈ {0,0.25,0.5,0.75,1}; solo con PRECOMP on
    'CDNG_OG_DEBLOCK_BOUNDED',   # EXP-001b: 1/on — precompensación restringida (excluye ALPHA)
)

# Diagnóstico o i18n: no alteran el BRAW. No entran en la identidad de caché; se pasan al worker solo si se
# declaran en encode.diagnostic_env o están en el entorno (y entonces se registran en run.json).
DIAGNOSTIC_ENV = (
    'CDNG_PIPELINE_TRACE',
    'CDNG_PROFILE_LOG',
    'CDNG_DUMP_CANVAS',
    'CDNG_LANGUAGE',
    'CDNG_BIT_BUDGET',     # EXP-002a: contabiliza bits por plano/símbolo (no altera payload)
    'CDNG_COEFF_STATS',    # EXP-002a: histogramas DCT (no altera payload)
    'CDNG_STATS_DIR',      # directorio de volcados bit-budget / coeff-stats
    'CDNG_STATS_LABEL',    # etiqueta opcional (conversion_key) embebida en JSONL
    'CDNG_ALIGN_SOFT',     # banco: registrar fallo de alineación sin abortar (no altera BRAW)
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
        raise PipelineError('experimental_env no admite variables de diagnóstico (%s); usa encode.diagnostic_env' %
                            ', '.join(DIAGNOSTIC_ENV))

    diag_declared = dict(encode.get('diagnostic_env') or {})
    diag_unknown = sorted(set(diag_declared) - set(DIAGNOSTIC_ENV))
    if diag_unknown:
        raise PipelineError('diagnostic_env declara variables desconocidas: %s' % ', '.join(diag_unknown))
    if any(k in diag_declared for k in OUTPUT_ENV):
        raise PipelineError('diagnostic_env no admite variables de salida (usa experimental_env)')

    effective = {name: None for name in OUTPUT_ENV}
    if encode['compression'] == 'ratio' and encode.get('rate_search', True):
        effective['CDNG_RATE'] = 'search'
    for name, value in declared.items():
        if name in OUTPUT_ENV:
            effective[name] = None if value is None else str(value)
    if effective.get('CDNG_DEADZONE') is not None:
        try:
            z = float(effective['CDNG_DEADZONE'])
        except (TypeError, ValueError) as e:
            raise PipelineError('CDNG_DEADZONE inválido: %r' % effective['CDNG_DEADZONE']) from e
        if not (0.0 <= z <= 0.5):
            raise PipelineError('CDNG_DEADZONE fuera de rango [0, 0.5]: %g' % z)
    if effective.get('CDNG_DEADZONE') is not None and effective.get('CDNG_DEADZONE_POLICY') is not None:
        raise PipelineError('CDNG_DEADZONE y CDNG_DEADZONE_POLICY son mutuamente excluyentes')
    policy_meta = None
    if effective.get('CDNG_DEADZONE_POLICY') is not None:
        path = os.path.abspath(effective['CDNG_DEADZONE_POLICY'])
        if not os.path.isfile(path):
            raise PipelineError('CDNG_DEADZONE_POLICY no existe: %s' % path)
        try:
            with open(path, encoding='utf-8') as f:
                doc = json.load(f)
        except (OSError, ValueError) as e:
            raise PipelineError('CDNG_DEADZONE_POLICY ilegible: %s (%s)' % (path, e)) from e
        if not isinstance(doc, dict) or doc.get('schema') != 1:
            raise PipelineError('CDNG_DEADZONE_POLICY: schema debe ser 1')
        if not doc.get('name') or not doc.get('mode'):
            raise PipelineError('CDNG_DEADZONE_POLICY: name y mode requeridos')
        sha = identity.sha256_file(path)
        policy_meta = {
            'path': path,
            'sha256': sha,
            'name': doc.get('name'),
            'mode': doc.get('mode'),
            'schema': doc.get('schema'),
        }
        # Identidad de caché: contenido (SHA) + ruta; el entorno del worker recibe solo la ruta.
        effective['CDNG_DEADZONE_POLICY'] = '%s#%s' % (path, sha)

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
    # CDNG_DEADZONE_POLICY en effective lleva path#sha; el worker recibe solo path.
    env = {}
    for k, v in effective.items():
        if v is None:
            continue
        if k == 'CDNG_DEADZONE_POLICY' and policy_meta is not None:
            env[k] = policy_meta['path']
        else:
            env[k] = v
    # EXP-001b: coherencia PRECOMP / ALPHA / BOUNDED (el worker también rechaza).
    pre = env.get('CDNG_OG_DEBLOCK_PRECOMP')
    alpha = env.get('CDNG_OG_DEBLOCK_ALPHA')
    bounded = env.get('CDNG_OG_DEBLOCK_BOUNDED')
    pre_off = pre is not None and pre.lower() in ('0', 'off', 'false', 'no')
    bounded_on = bounded is not None and bounded.lower() in ('1', 'on', 'true', 'yes')
    if pre_off and alpha is not None:
        raise PipelineError('CDNG_OG_DEBLOCK_ALPHA no admite valor con CDNG_OG_DEBLOCK_PRECOMP=0')
    if pre_off and bounded_on:
        raise PipelineError('CDNG_OG_DEBLOCK_BOUNDED=1 contradice CDNG_OG_DEBLOCK_PRECOMP=0')
    if bounded_on and alpha is not None:
        raise PipelineError('CDNG_OG_DEBLOCK_BOUNDED y CDNG_OG_DEBLOCK_ALPHA son mutuamente excluyentes')
    if alpha is not None:
        try:
            av = float(alpha)
        except ValueError as e:
            raise PipelineError('CDNG_OG_DEBLOCK_ALPHA inválido: %s' % alpha) from e
        if not any(abs(av - a) < 1e-12 for a in (0.0, 0.25, 0.5, 0.75, 1.0)):
            raise PipelineError('CDNG_OG_DEBLOCK_ALPHA debe ser 0|0.25|0.5|0.75|1 (got %s)' % alpha)
    # Diagnóstico: manifiesto explícito gana; el ambiente se registra y también se pasa (no altera el BRAW).
    diagnostic = {k: ambient[k] for k in DIAGNOSTIC_ENV if k in ambient}
    for k, v in diag_declared.items():
        if v is None:
            diagnostic.pop(k, None)
        else:
            diagnostic[k] = str(v)
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


def requires_pipeline_trace(diagnostic):
    return bool(diagnostic) and diagnostic.get('CDNG_PIPELINE_TRACE') not in (None, '', '0')


def requires_encode_stats(diagnostic):
    """EXP-002a: presupuesto/histogramas se emiten en encode(); hay que ejecutar el worker."""
    if not diagnostic:
        return False
    if diagnostic.get('CDNG_BIT_BUDGET') not in (None, '', '0'):
        return True
    if diagnostic.get('CDNG_COEFF_STATS') not in (None, '', '0'):
        return True
    return False


def conversion_diagnostics(diagnostic):
    """Diagnósticos que exigen ejecutar el worker aunque el BRAW ya esté en caché."""
    if not diagnostic:
        return False
    if requires_pipeline_trace(diagnostic):
        return True
    if diagnostic.get('CDNG_DUMP_CANVAS'):
        return True
    if diagnostic.get('CDNG_PROFILE_LOG'):
        return True
    if requires_encode_stats(diagnostic):
        return True
    return False


def parse_worker_events(stdout):
    """Todas las líneas JSON del stdout del worker; falla si alguna línea `{…}` no es JSON válido."""
    events = []
    for i, line in enumerate(stdout.splitlines()):
        text = line.strip()
        if not text or text[0] != '{':
            continue
        try:
            events.append(json.loads(text))
        except ValueError as e:
            raise PipelineError('evento JSON del worker ilegible (línea %d): %s' % (i + 1, text[:240])) from e
    return events


def _stamp_trace_text(trace_path, key, evaluated_braw_sha, producer_braw_sha, generated, trace_body_sha):
    with open(trace_path, encoding='utf-8', errors='replace') as f:
        lines = f.read().splitlines(True)
    while lines and lines[0].startswith(('# conversion_key ', '# braw_sha256 ', '# producer_braw_sha256 ',
                                         '# evaluated_braw_sha256 ', '# generated ', '# trace_sha256 ',
                                         '# trace_body_sha256 ')):
        lines.pop(0)
    stamp = ('# conversion_key %s\n# evaluated_braw_sha256 %s\n# producer_braw_sha256 %s\n'
             '# braw_sha256 %s\n# generated %s\n# trace_body_sha256 %s\n' % (
                 key, evaluated_braw_sha, producer_braw_sha, evaluated_braw_sha, generated, trace_body_sha))
    with open(trace_path, 'w', encoding='utf-8') as f:
        f.write(stamp)
        f.writelines(lines)


def _publish_fresh_trace(instr_dir, directory, key, material, evaluated_braw_sha, producer_braw_sha,
                         diagnostic, media_equivalent, instrumented_meta):
    """Publica la traza recién generada en instr_dir. Una traza antigua en directory no basta."""
    src = os.path.join(instr_dir, 'clip.pipeline-trace.txt')
    if not os.path.isfile(src):
        raise PipelineError('CDNG_PIPELINE_TRACE solicitado pero el worker no escribió %s' % src)
    with open(src, 'rb') as f:
        raw = f.read()
    if not raw.strip():
        raise PipelineError('CDNG_PIPELINE_TRACE: la traza en %s está vacía' % src)
    body_sha = identity.sha256_bytes(raw)
    dest = os.path.join(directory, 'clip.pipeline-trace.txt')
    same = os.path.exists(dest) and os.path.samefile(src, dest)
    if not same:
        # No reutilizar una traza previa de la caché: borrar antes de copiar la fresca.
        if os.path.isfile(dest):
            os.remove(dest)
        shutil.copy2(src, dest)
    generated = time.strftime('%Y-%m-%dT%H:%M:%S')
    _stamp_trace_text(dest, key, evaluated_braw_sha, producer_braw_sha, generated, body_sha)
    with open(dest, 'rb') as f:
        published_sha = hashlib.sha256(f.read()).hexdigest()
    # Verificar inmediatamente el SHA del fichero sellado publicado.
    with open(dest, 'rb') as f:
        verify_sha = hashlib.sha256(f.read()).hexdigest()
    if verify_sha != published_sha:
        raise PipelineError('SHA-256 de la traza publicada inconsistente tras el sello')
    meta = {
        'conversion_key': key,
        'material': material,
        'diagnostic_env': diagnostic,
        'generated': generated,
        'trace_sha256': published_sha,
        'trace_body_sha256': body_sha,
        'evaluated_braw_sha256': evaluated_braw_sha,
        'producer_braw_sha256': producer_braw_sha,
        'braw_file_sha_equal': evaluated_braw_sha == producer_braw_sha,
        'media_equivalent': bool(media_equivalent),
        # Solo con payload + metadata de revelado equivalente (no basta mdat solo si moov estructural difiere).
        'binds_to_evaluated_image': bool(media_equivalent),
        'instrumented': instrumented_meta,
        # Alias históricos
        'braw_sha256': evaluated_braw_sha,
        'instrumented_braw_sha256': producer_braw_sha,
    }
    path = os.path.join(directory, 'pipeline-trace.identity.json')
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(meta, f, indent=1, sort_keys=True, ensure_ascii=False)
        f.write('\n')
    return path, meta


def _require_requested_traces(directory, diagnostic, expect_body_sha=None, expect_published_sha=None):
    if diagnostic.get('CDNG_PIPELINE_TRACE') not in (None, '', '0'):
        path = os.path.join(directory, 'clip.pipeline-trace.txt')
        ident = os.path.join(directory, 'pipeline-trace.identity.json')
        if not os.path.isfile(path) or not os.path.isfile(ident):
            raise PipelineError('CDNG_PIPELINE_TRACE solicitado pero falta la traza o su identidad en %s' % directory)
        with open(ident, encoding='utf-8') as f:
            meta = json.load(f)
        if not meta.get('generated') or not meta.get('trace_body_sha256') or not meta.get('trace_sha256'):
            raise PipelineError('pipeline-trace.identity.json incompleto (falta generated/trace_*_sha256)')
        if expect_body_sha is not None and meta.get('trace_body_sha256') != expect_body_sha:
            raise PipelineError('la traza publicada no coincide con la generada en esta ejecución')
        with open(path, 'rb') as f:
            got = hashlib.sha256(f.read()).hexdigest()
        if got != meta.get('trace_sha256'):
            raise PipelineError('SHA-256 de la traza publicada no coincide con pipeline-trace.identity.json')
        if expect_published_sha is not None and got != expect_published_sha:
            raise PipelineError('SHA-256 de la traza publicada no coincide con el esperado de esta ejecución')
        if meta.get('producer_braw_sha256') and meta.get('evaluated_braw_sha256'):
            if (not meta.get('media_equivalent') and
                    meta.get('binds_to_evaluated_image')):
                raise PipelineError('binds_to_evaluated_image contradice media_equivalent')


def _run_convert_worker(converter, clip_dir, staged, req, env, diagnostic, tmp):
    """Ejecuta cdng-convert en tmp/; devuelve (out_path, events, completed, seconds)."""
    src = os.path.join(tmp, 'staging', os.path.basename(clip_dir))
    os.makedirs(src)
    for f in staged:
        os.symlink(os.path.realpath(f), os.path.join(src, os.path.basename(f)))
    out = os.path.join(tmp, 'clip.braw')
    request = dict(req, input=src, output=out)
    with open(os.path.join(tmp, 'request.json'), 'w') as f:
        json.dump(request, f, indent=1)
    child_env = worker_environ(os.environ)
    child_env.update(env)
    child_env.update(diagnostic)
    t0 = time.monotonic()
    r = subprocess.run([os.path.join(converter, 'cdng-convert'), '--request', os.path.join(tmp, 'request.json')],
                       capture_output=True, text=True, env=child_env)
    seconds = time.monotonic() - t0
    log_path = os.path.join(tmp, 'convert.log')
    with open(log_path, 'w', encoding='utf-8') as f:
        f.write(r.stdout + ('\n--- stderr ---\n' + r.stderr if r.stderr else ''))
    events = parse_worker_events(r.stdout)
    done = [e for e in events if e.get('event') == 'completed']
    if r.returncode or not done or not os.path.isfile(out):
        errors = [e.get('message') for e in events if e.get('event') == 'error']
        raise PipelineError('el worker falló (código %d): %s; ver %s' % (
            r.returncode, errors or r.stderr.strip()[-500:], log_path))
    if done[0].get('replaced_frames'):
        raise PipelineError('el worker sustituyó fotogramas %s por negro' % done[0]['replaced_frames'])
    for e in events:
        if e.get('event') != 'started':
            continue
        if 'og_deblock_precomp' in e and not isinstance(e['og_deblock_precomp'], bool):
            raise PipelineError('started.og_deblock_precomp debe ser booleano JSON, got %r' % e['og_deblock_precomp'])
        if 'og_deblock_bounded' in e and not isinstance(e['og_deblock_bounded'], bool):
            raise PipelineError('started.og_deblock_bounded debe ser booleano JSON, got %r' % e['og_deblock_bounded'])
        if 'og_deblock_alpha' in e and not isinstance(e['og_deblock_alpha'], (int, float)):
            raise PipelineError('started.og_deblock_alpha debe ser número JSON, got %r' % e['og_deblock_alpha'])
    shutil.rmtree(os.path.join(tmp, 'staging'))
    return out, events, done[0], seconds


def convert(converter, converter_id, clip_dir, files, start, count, encode, cache, log, no_cache=False):
    """Convierte los DNG [start, start + count) de un clip. Devuelve la entrada de caché (ruta, sha, evento final)."""
    staged = files[start:start + count]
    req, effective, env, diagnostic = encode_request(encode)
    if diagnostic:
        log('diagnóstico al worker (no entra en la identidad de caché): %s' %
            ', '.join('%s=%s' % kv for kv in sorted(diagnostic.items())))
    material = conversion_material(converter_id, clip_dir, staged, start, req, effective)
    key = identity.sha256_json(material)
    directory = os.path.join(cache, 'braw', key[:24])
    want_diag = conversion_diagnostics(diagnostic)
    rate_note = ' búsqueda exacta' if env.get('CDNG_RATE') == 'search' else ''
    exp_note = ''
    active = {k: v for k, v in effective.items() if v is not None}
    if active:
        exp_note = '; experimental %s' % ','.join('%s=%s' % kv for kv in sorted(active.items()))
    convert_msg = 'convirtiendo %s fotogramas %d–%d (%s%s%s) con worker %s' % (
        material['clip'], start, start + count - 1,
        'q %s' % req['q'] if 'q' in req else 'ratio %s:1' % req['ratio'], rate_note, exp_note,
        converter_id['cdng_convert_sha256'][:12])

    if os.path.isdir(directory) and not no_cache:
        saved = _verify(directory, material, 'clip.braw')
        if not want_diag:
            log('BRAW reutilizado %s (%s, fotogramas %d–%d; worker %s; identidad verificada)' % (
                key[:12], material['clip'], start, start + count - 1, converter_id['cdng_convert_sha256'][:12]))
            return dict(saved, path=os.path.join(directory, 'clip.braw'), key=key, reused=True,
                        experimental_env=effective, inputs=material['inputs'], trace=None)
        # Caché de imagen intacta: regenerar instrumentación en tmp; no contaminar completed/compression.
        log('BRAW en caché %s; regenerando instrumentación (metadatos de conversión = caché evaluada)' % key[:12])
        log(convert_msg + ' [diagnóstico]')
        instr = directory + '.instr.tmp'
        shutil.rmtree(instr, ignore_errors=True)
        os.makedirs(instr)
        try:
            out, events, done, seconds = _run_convert_worker(
                converter, clip_dir, staged, req, env, diagnostic, instr)
            producer_sha = identity.sha256_file(out)
            evaluated_path = os.path.join(directory, 'clip.braw')
            eq = braw_inspect.equivalence(evaluated_path, out)
            media_eq = eq['binds_to_evaluated_image']
            if producer_sha != saved['sha256']:
                log('aviso: SHA del contenedor BRAW instrumentado (%s) ≠ caché (%s); '
                    'payload=%s meta_revelado=%s → binds=%s' % (
                        producer_sha[:12], saved['sha256'][:12],
                        eq['payload_equal'], eq['decode_relevant_meta_equal'], media_eq))
            if not media_eq:
                log('aviso: payload o metadata de revelado (tkhd/stsd/stsz) difieren; '
                    'binds_to_evaluated_image=false (mdat igual no basta por sí solo)')
            instrumented = {
                'seconds': seconds,
                'completed': done,
                'braw_sha256': producer_sha,
                'media_equivalent': media_eq,
                'file_sha_equal': producer_sha == saved['sha256'],
                'equivalence': eq,
                'mdat_sha256': eq.get('mdat_sha256_b'),
                'moov_sha256': eq.get('moov_sha256_b'),
            }
            src_log = os.path.join(instr, 'convert.log')
            if os.path.isfile(src_log):
                shutil.copy2(src_log, os.path.join(directory, 'convert.instrumented.log'))
            trace_id, trace_meta = None, None
            if requires_pipeline_trace(diagnostic):
                # Solo publicar traza si nació en instr.tmp; una traza vieja en directory no basta.
                trace_id, trace_meta = _publish_fresh_trace(
                    instr, directory, key, material, saved['sha256'], producer_sha,
                    diagnostic, media_eq, instrumented)
                _require_requested_traces(directory, diagnostic,
                                         expect_body_sha=trace_meta['trace_body_sha256'],
                                         expect_published_sha=trace_meta['trace_sha256'])
        finally:
            shutil.rmtree(instr, ignore_errors=True)
        # completed/seconds del BRAW efectivamente evaluado (caché); instrumented aparte.
        return dict(saved, path=evaluated_path, key=key, reused=True,
                    experimental_env=effective, inputs=material['inputs'],
                    completed=saved['completed'], seconds=saved['seconds'],
                    instrumented=instrumented, media_equivalent=media_eq,
                    binds_to_evaluated_image=media_eq, equivalence=eq,
                    trace=trace_id, trace_refreshed=bool(trace_id), trace_meta=trace_meta)

    if os.path.isdir(directory):
        shutil.rmtree(directory)
    tmp = directory + '.tmp'
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    log(convert_msg)
    out, events, done, seconds = _run_convert_worker(
        converter, clip_dir, staged, req, env, diagnostic, tmp)
    saved = {'material': material, 'sha256': identity.sha256_file(out), 'completed': done, 'seconds': seconds}
    with open(os.path.join(tmp, 'identity.json'), 'w') as f:
        json.dump(saved, f, indent=1, sort_keys=True)
    trace_id = None
    trace_meta = None
    if want_diag and requires_pipeline_trace(diagnostic):
        instrumented = {
            'seconds': seconds,
            'completed': done,
            'braw_sha256': saved['sha256'],
            'media_equivalent': True,
            'file_sha_equal': True,
        }
        # Traza fresca en tmp (mismo directorio que se publicará).
        trace_id, trace_meta = _publish_fresh_trace(
            tmp, tmp, key, material, saved['sha256'], saved['sha256'],
            diagnostic, True, instrumented)
        _require_requested_traces(tmp, diagnostic,
                                 expect_body_sha=trace_meta['trace_body_sha256'],
                                 expect_published_sha=trace_meta['trace_sha256'])
    os.rename(tmp, directory)
    # Tras el rename, la ruta de identidad debe apuntar al directorio final (no al .tmp).
    if trace_id is not None:
        trace_id = os.path.join(directory, 'pipeline-trace.identity.json')
        if trace_meta is not None:
            trace_meta = dict(trace_meta)
            # Recalcular/verificar SHA sobre la ruta final publicada.
            published = os.path.join(directory, 'clip.pipeline-trace.txt')
            with open(published, 'rb') as f:
                final_sha = hashlib.sha256(f.read()).hexdigest()
            if final_sha != trace_meta.get('trace_sha256'):
                raise PipelineError('SHA-256 de la traza cambió tras renombrar la caché')
            _require_requested_traces(directory, diagnostic,
                                     expect_body_sha=trace_meta['trace_body_sha256'],
                                     expect_published_sha=final_sha)
    return dict(saved, path=os.path.join(directory, 'clip.braw'), key=key, reused=False,
                experimental_env=effective, inputs=material['inputs'],
                instrumented=None, media_equivalent=True, binds_to_evaluated_image=True,
                trace=trace_id, trace_meta=trace_meta)


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


def verify_decode(settings, info, expected_iso=None):
    """Compara lo solicitado con lo que brawdec reportó. Distingue aplicado / no verificable / error.

    Atributos críticos (gamut, gamma, csgn, hlry, scale, exposure, y la presencia de iso/kelvin/tint)
    deben aparecer en la salida de brawdec: su ausencia detiene la evaluación. As Shot no puede
    contrastarse aún contra AsShotNeutral del DNG; si el SDK reporta ISO y se conoce el ISO del
    manifiesto, se exige coincidencia numérica.
    """
    requested = dict(settings)
    if expected_iso is not None:
        requested['expected_iso'] = expected_iso
    applied, unverifiable, errors = {}, [], []

    def take_clip(name, want, cast=lambda x: x, critical=True):
        entry = info['clip'].get(name)
        if not entry or 'value' not in entry:
            (errors if critical else unverifiable).append(
                '%s no reportado por brawdec' % name if critical else name)
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
        errors.append('scale no reportado por brawdec')
    else:
        applied['scale'] = info['scale']
        if settings.get('scale', 'full') != info['scale']:
            errors.append('scale: solicitado %r, SDK %r' % (settings.get('scale', 'full'), info['scale']))

    for name, setting_key in (('iso', 'iso'), ('exposure', 'exposure'), ('kelvin', 'kelvin'), ('tint', 'tint')):
        want = settings.get(setting_key)
        entry = info['frame'].get(name)
        if not entry or 'value' not in entry:
            errors.append('%s no reportado por brawdec' % name)
            continue
        got = entry['value']
        applied[name] = got
        if want is None or want == 'as_shot':
            if name == 'exposure' and abs(float(got)) > 1e-6:
                errors.append('exposure: As Shot/0 EV esperado, SDK %s' % got)
            if name == 'iso' and expected_iso is not None and abs(float(got) - float(expected_iso)) > 0.5:
                errors.append('iso As Shot: manifiesto %s, SDK %s' % (expected_iso, got))
            continue
        if abs(float(got) - float(want)) > 1e-6:
            errors.append('%s: solicitado %s, SDK %s' % (name, want, got))

    return {'requested': requested, 'applied': applied, 'unverifiable': sorted(set(unverifiable)),
            'ok': not errors, 'errors': errors}


def decode(brawdec, framework, decoder_id, braw, braw_sha, index, settings, cache, log, no_cache=False,
           expected_iso=None):
    """Revela un fotograma del BRAW a RGB float. Devuelve (imagen, entrada de caché)."""
    # info_schema 2: frame_info anidado + decode_verification (invalida cachés del schema 1).
    material = {'kind': 'decode', 'info_schema': 2, 'decoder': decoder_id, 'braw_sha256': braw_sha,
                'index': index, 'settings': settings, 'expected_iso': expected_iso}
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
    verification = verify_decode(settings, info, expected_iso=expected_iso)
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
