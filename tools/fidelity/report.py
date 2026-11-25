"""metrics.csv y report.html autocontenido (PNG en base64). Las métricas salen de metrics.json; los tiempos, de run.json."""
import base64
import csv
import html
import os

from .images import png_bytes
from .metrics import flatten
from . import viz

SKIP = ('.profile.', '.png.')


def write_csv(path, metrics):
    rows = []
    for key in sorted(metrics['results']):
        for metric, value in flatten('', metrics['results'][key], []):
            if any(s in '.' + metric + '.' for s in SKIP) or metric.startswith('png.') or metric.startswith('setup.'):
                continue
            rows.append((key, metric, value))
    with open(path, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f, lineterminator='\n')
        w.writerow(['result', 'metric', 'value'])
        for r in rows:
            w.writerow([r[0], r[1], fmt_csv(r[2])])


def fmt_csv(v):
    return repr(v) if isinstance(v, float) else v


def esc(x):
    return html.escape(str(x))


def num(v, digits=6):
    if isinstance(v, bool) or v is None:
        return esc(v)
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return '%.*g' % (digits, v)
    return esc(v)


def img_tag(path, title='', width=None):
    if not path or not os.path.isfile(path):
        return '<em>falta %s</em>' % esc(os.path.basename(path or ''))
    with open(path, 'rb') as f:
        data = base64.b64encode(f.read()).decode()
    style = ' style="width:%s"' % width if width else ''
    return '<img src="data:image/png;base64,%s" title="%s"%s>' % (data, esc(title), style)


def colorbar_tag(stops):
    data = base64.b64encode(png_bytes(viz.colorbar(stops))).decode()
    return '<img src="data:image/png;base64,%s" style="width:256px;height:12px">' % data


def table(headers, rows):
    out = ['<table><tr>%s</tr>' % ''.join('<th>%s</th>' % esc(h) for h in headers)]
    for r in rows:
        out.append('<tr>%s</tr>' % ''.join('<td>%s</td>' % c for c in r))
    out.append('</table>')
    return ''.join(out)


def kv(d, keys=None):
    keys = keys or list(d)
    return table(['métrica', 'valor'], [[esc(k), num(d.get(k))] for k in keys if k in d])


CSS = """body{font-family:-apple-system,Helvetica,Arial,sans-serif;margin:24px;color:#222;max-width:1500px}
h1{font-size:22px}h2{font-size:18px;border-bottom:1px solid #ccc;padding-top:12px}h3{font-size:15px}
table{border-collapse:collapse;margin:6px 0 14px;font-size:12px}td,th{border:1px solid #ddd;padding:3px 6px;text-align:right}
th{background:#f3f3f3}td:first-child,th:first-child{text-align:left}img{image-rendering:pixelated;max-width:100%}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(460px,1fr));gap:10px}.grid figure{margin:0}
figcaption{font-size:12px;color:#555}.improved{background:#dff5df}.worse{background:#f9dcdc}.equivalent{color:#777}
.note{font-size:12px;color:#555}code{font-size:12px}details{margin:6px 0}"""


def identity_section(run):
    g, c, d = run['git'], run['converter'], run['decoder']
    rows = [['commit', esc(g.get('commit'))], ['rama', esc(g.get('branch'))], ['árbol modificado', esc(g.get('dirty'))],
            ['cdng-convert sha256', '<code>%s</code>' % esc(c['cdng_convert_sha256'])], ['worker', esc(c['path'])],
            ['brawdec sha256', '<code>%s</code>' % esc(d['brawdec_sha256'])], ['SDK', esc(d['framework'])],
            ['SDK sha256', '<code>%s</code>' % esc(d['framework_sha256'])]]
    rows += [['Camera Support: ' + esc(k), '<code>%s</code>' % esc(v)] for k, v in sorted(d['camera_support'].items())]
    rows += [['modelo: ' + esc(k), '<code>%s</code>' % esc(v[:16])] for k, v in sorted(c['model'].items())]
    rows += [['dataset', esc(run['dataset'])], ['referencias', esc(run['references'])],
             ['entorno', esc(', '.join('%s %s' % kv_ for kv_ in sorted(run['environment'].items())))]]
    return '<h2>Identidad de la ejecución</h2>' + table(['campo', 'valor'], rows)


SUMMARY = [('PSNR norm. (dB)', 'normalized.psnr_db'), ('PSNR sin norm. (dB)', 'raw.psnr_db'), ('MAE norm.', 'normalized.mae'),
           ('RMSE norm.', 'normalized.rmse'), ('correlación', 'normalized.correlation_rgb'),
           ('luminancia %', 'normalized.luminance_rel_error_pct'), ("Δu'v' bordes", 'luma_chroma.edges.duv_mean'),
           ("Δu'v' plano", 'luma_chroma.flat.duv_mean'), ('nitidez bordes', 'sharpness.gradient_energy_ratio_edges'),
           ('detalle (alineación)', 'alignment.detail_correlation')]


def get(d, path):
    for p in path.split('.'):
        if not isinstance(d, dict) or p not in d:
            return None
        d = d[p]
    return d


def fidelity_section(res, png_dir, title):
    f = res
    out = ['<h3>%s</h3>' % esc(title)]
    if 'identical' in f:
        out.append('<p>Idéntica bit a bit al baseline: <b>%s</b></p>' % esc(f['identical']))
    out.append('<p class="note">Normalización: ambas imágenes × %s (a ISO %s). PSNR con pico %s sobre todas las muestras RGB, sin recortar.</p>' % (
        num(f['normalization']['factor']), esc(f['normalization']['to_iso']), num(f['normalized']['psnr_peak'])))
    out.append(table(['', 'MAE', 'RMSE', 'PSNR (dB)', 'correlación'],
                     [[esc(k)] + [num(f[k].get(m)) for m in ('mae', 'rmse', 'psnr_db', 'correlation_rgb')] for k in ('raw', 'normalized')]))
    pc = f['normalized']['per_channel']
    out.append(table(['canal', 'error medio con signo', 'MAE', 'correlación', 'ref mín', 'ref máx', 'cand mín', 'cand máx',
                      'ref <0', 'cand <0', 'ref >1', 'cand >1'],
                     [[c, num(pc[c]['signed_mean_error']), num(pc[c]['mae']), num(pc[c]['correlation']),
                       num(f['reference_channels'][c]['min']), num(f['reference_channels'][c]['max']),
                       num(f['candidate_channels'][c]['min']), num(f['candidate_channels'][c]['max']),
                       num(f['reference_channels'][c]['negative_fraction']), num(f['candidate_channels'][c]['negative_fraction']),
                       num(f['reference_channels'][c]['above_one_fraction']), num(f['candidate_channels'][c]['above_one_fraction'])]
                      for c in 'RGB']))
    out.append('<p class="note">Mín/máx y fracciones negativas o &gt;1 sobre los valores sin normalizar.</p>')
    pct = f['normalized']['abs_error_percentiles']
    extra = [['error de luminancia media (%)', num(f['normalized']['luminance_rel_error_pct'])]]
    extra += [['|error| p%s' % esc(p), num(v)] for p, v in pct.items()]
    if 'ceiling' in f:
        extra += [['techo de señal', num(f['ceiling']['value'])], ['fracción en el techo (ref)', num(f['ceiling']['reference_fraction'])],
                  ['fracción en el techo (cand)', num(f['ceiling']['candidate_fraction'])]]
    extra += [['nitidez: energía de gradiente en bordes (cand/ref)', num(f['sharpness']['gradient_energy_ratio_edges'])],
              ['nitidez: energía laplaciana en bordes (cand/ref)', num(f['sharpness']['laplacian_energy_ratio_edges'])],
              ['fracción de píxeles de borde', num(f['edges']['mask_fraction'])]]
    if 'alignment' in f:
        extra += [['alineación (dy, dx)', esc(f['alignment']['shift'])], ['correlación del detalle', num(f['alignment']['detail_correlation'])]]
    out.append(table(['métrica', 'valor'], extra))
    out.append('<b>Error por intervalo de luminancia (Y de la referencia normalizada)</b>')
    out.append(table(['Y desde', 'Y hasta', 'píxeles', 'MAE', 'RMSE', 'error con signo', 'luminancia %'],
                     [[num(b['low']), num(b['high']), num(b['pixels']), num(b.get('mae')), num(b.get('rmse')),
                       num(b.get('signed_mean_error')), num(b.get('luminance_rel_error_pct'))] for b in f['luminance_bins']]))
    lc = f['luma_chroma']
    keys = ('pixels', 'luma_rel_error_mean', 'luma_rel_error_abs_mean', 'duv_mean', 'duv_p95', 'duv_p99')
    out.append("<b>Luma y croma (Δu'v' CIE 1976; píxeles con Y &gt; umbral)</b>")
    out.append(table(['zona'] + list(keys), [[z] + [num(lc[z].get(k)) for k in keys] for z in ('all', 'edges', 'flat')]))
    e = f['edges']
    out.append('<b>Bordes de la referencia: dentro / fuera de la máscara</b>')
    out.append(table(['zona', 'MAE', 'RMSE', 'PSNR'], [[z] + [num(e[z].get(k)) for k in ('mae', 'rmse', 'psnr_db')] for z in ('inside', 'outside')]))
    out.append('<b>Regiones neutras de la referencia</b>' + kv(f['neutral']))
    png = f.get('png', {})
    figs = [('reference', 'Referencia (visualización: ×2^%s, gamma 1/2.2)'), ('candidate', 'Candidata'),
            ('abs_error', '|error| máximo por canal'), ('signed_error', 'error de luminancia con signo'),
            ('chroma_error', "Δu'v' (gris: referencia demasiado oscura)"), ('edge_mask', 'máscara de bordes'),
            ('histogram', 'histograma del error con signo (R, G, B; eje y log)'), ('roi_overlay', 'ROI')]
    out.append('<div class="grid">')
    for name, cap in figs:
        if name in png:
            out.append('<figure>%s<figcaption>%s</figcaption></figure>' % (img_tag(os.path.join(png_dir, png[name])), esc(cap)))
    out.append('</div>')
    if 'roi_summary' in f:
        out.append(roi_section(f, png, png_dir))
    return ''.join(out)


def roi_section(f, png, png_dir):
    out = ['<b>Resumen por categoría de ROI</b>']
    cats = sorted(f['roi_summary'])
    keys = sorted({k for c in cats for k in f['roi_summary'][c]})
    out.append(table(['categoría'] + keys, [[esc(c)] + [num(f['roi_summary'][c].get(k)) for k in keys] for c in cats]))
    rects = [(n, m) for n, m in sorted(f['rois'].items()) if m['type'] != 'edge']
    out.append('<details><summary>ROI rectangulares (%d)</summary>' % len(rects))
    out.append(table(['ROI', 'categoría', 'MAE', 'RMSE', 'PSNR', 'luminancia %', "Δu'v' media", 'ruido ref G', 'ruido cand G', 'neutro ref→D65', 'neutro cand→D65'],
                     [[esc(n), esc(m['category']), num(m['mae']), num(m['rmse']), num(m['psnr_db']), num(m['luminance_rel_error_pct']),
                       num(m['mean_duv']), num(m['reference_noise_rgb'][1]), num(m['candidate_noise_rgb'][1]),
                       num(get(m, 'neutral.reference_duv_to_d65')), num(get(m, 'neutral.candidate_duv_to_d65'))] for n, m in rects]))
    out.append('</details>')
    edges = [(n, m) for n, m in sorted(f['rois'].items()) if m['type'] == 'edge']
    if edges:
        out.append('<b>Perfiles perpendiculares a bordes</b> <span class="note">(overshoot/undershoot relativos al salto; subida 10–90 % en píxeles; franja = separación entre canales ausente en la referencia, relativa al salto)</span>')
        out.append(table(['borde', 'RMSE perfil', 'overshoot ref', 'overshoot cand', 'undershoot ref', 'undershoot cand',
                          'subida ref', 'subida cand', 'franja máx', "Δu'v' media", 'perfil'],
                         [[esc(n), num(m['profile_rmse']), num(m['reference']['overshoot']), num(m['candidate']['overshoot']),
                           num(m['reference']['undershoot']), num(m['candidate']['undershoot']), num(m['reference']['rise_10_90']),
                           num(m['candidate']['rise_10_90']), num(m['fringe_max']), num(m.get('duv_mean')),
                           img_tag(os.path.join(png_dir, png.get('profile_' + n, '')), n)] for n, m in edges]))
        out.append('<p class="note">Perfil: referencia en trazo grueso claro, candidata en trazo fino (R, G, B).</p>')
    zooms = [k for k in sorted(png) if k.startswith('zoom_')]
    if zooms:
        out.append('<details><summary>Ampliaciones (vecino más próximo): referencia | candidata | |error|</summary><div class="grid">')
        for z in zooms:
            out.append('<figure>%s<figcaption>%s</figcaption></figure>' % (img_tag(os.path.join(png_dir, png[z])), esc(z[5:])))
        out.append('</div></details>')
    return ''.join(out)


def comparison_section(comparison):
    out = ['<h2>Comparación con %s</h2>' % esc(comparison.get('baseline'))]
    counts = comparison['counts']
    out.append('<p>%s</p>' % ', '.join('%s: <b>%d</b>' % (esc(k), v) for k, v in sorted(counts.items())))
    if comparison.get('same_build') is not None:
        out.append('<p>Misma compilación: %s</p>' % esc(comparison['same_build']))
    if comparison.get('setup_differences'):
        out.append('<p><b>Configuraciones distintas</b> (comparación pedida explícitamente):</p>')
        rows = [[esc(k), esc(d['field']), esc(d['base']), esc(d['candidate'])] for k, v in sorted(comparison['setup_differences'].items()) for d in v]
        out.append(table(['resultado', 'campo', 'base', 'candidata'], rows[:60]))
    changed = [r for r in comparison['rows'] if r['status'] != 'equivalent']
    out.append('<p class="note">Estado según la dirección de cada métrica y la tolerancia de benchmark/thresholds.json. Se muestran las métricas que cambian (%d de %d).</p>' % (len(changed), len(comparison['rows'])))
    out.append(table(['resultado', 'métrica', 'base', 'candidata', 'estado'],
                     [[esc(r['result']), esc(r['metric']), num(r['base']), num(r['candidate']),
                       '<span class="%s">%s</span>' % (r['status'], esc(r['status']))] for r in changed[:2000]]))
    if len(changed) > 2000:
        out.append('<p class="note">Lista truncada a 2000 filas; comparison.json tiene todas.</p>')
    if comparison.get('acceptance'):
        out.append('<b>Criterios de aceptación</b>')
        out.append(table(['resultado', 'métrica', 'valor', 'cumple', 'fundamento'],
                         [[esc(a['result']), esc(a['metric']), num(a['value']), esc(a['pass']), esc(a['basis'])] for a in comparison['acceptance']]))
    return ''.join(out)


def write_html(path, metrics, run, comparison, doc):
    png_dir = os.path.join(os.path.dirname(path), 'png')
    vis = doc['visualization']
    parts = ['<!doctype html><html lang="es"><meta charset="utf-8"><title>Banco de fidelidad %s</title><style>%s</style>' % (esc(run['run_id']), CSS),
             '<h1>Banco de fidelidad de imagen — %s</h1>' % esc(run['run_id']),
             '<p class="note">Métricas sobre RGB float lineal DCI-P3 D65 sin recortar, sin gamma, sin 8 bits. Las imágenes son solo visualización '
             '(×2^%s, recorte, gamma 1/2.2). Escalas fijas del manifiesto: |error| 0–%s, error con signo ±%s, Δu\'v\' 0–%s, histograma ±%s.</p>' % (
                 num(vis['display_exposure']), num(vis['abs_error_max']), num(vis['signed_error_max']), num(vis['chroma_error_max']), num(vis['histogram_max'])),
             '<p>Escala secuencial %s · divergente %s</p>' % (colorbar_tag(viz._SEQUENTIAL), colorbar_tag(viz._DIVERGING))]
    rows = []
    for key in sorted(metrics['results']):
        r = metrics['results'][key]
        f = r.get('reference_fidelity')
        comp = r['compression']
        rows.append([esc(key), esc(r['setup']['iso']), esc(get(r, 'setup.reference.file') or '—')] +
                    [num(get(f, p)) if f else '—' for _, p in SUMMARY] +
                    [num(comp.get('frame_bytes')), num(comp.get('frame_ratio'), 4),
                     esc(get(r, 'regression.identical')) if 'regression' in r else '—'])
    parts.append('<h2>Resumen</h2>')
    parts.append(table(['resultado', 'ISO', 'referencia'] + [s for s, _ in SUMMARY] + ['bytes del fotograma', 'compresión', 'idéntica al baseline'], rows))
    parts.append('<p class="note">compresión = ancho·alto·12/8 / bytes del fotograma en el BRAW (frame_bytes que informa el SDK).</p>')
    if comparison:
        parts.append(comparison_section(comparison))
    for key in sorted(metrics['results']):
        r = metrics['results'][key]
        parts.append('<h2 id="%s">%s — %s, fotograma %d</h2>' % (esc(key), esc(key), esc(r['setup']['clip']), r['frame']))
        s = r['setup']
        parts.append(table(['campo', 'valor'], [['categoría', esc(r['category'])], ['tramo convertido', esc(s['range'])],
                                                 ['codificación', esc(s['encode'])], ['revelado', esc(s['decode'])],
                                                 ['DNG sha256', '<code>%s</code>' % esc(s['dng_sha256'])],
                                                 ['candidata sha256', '<code>%s</code>' % esc(r['candidate_sha256'])],
                                                 ['referencia', esc(s.get('reference'))], ['compresión', esc(r['compression'])]]))
        if 'reference_fidelity' in r:
            parts.append(fidelity_section(r['reference_fidelity'], png_dir, 'Fidelidad frente a la referencia de Resolve'))
        if 'regression' in r:
            parts.append(fidelity_section(r['regression'], png_dir, 'Regresión frente al baseline (baseline como referencia)'))
        if 'dark_frame' in r:
            d = r['dark_frame']
            keys = ('mean', 'std', 'p0_1', 'p99_9', 'row_noise_std', 'column_noise_std')
            parts.append('<h3>Dark frame</h3>' + table(['canal'] + list(keys), [[c] + [num(d[c][k]) for k in keys] for c in 'RGB']))
    t = run['timing']
    parts.append('<h2>Tiempos (no deterministas; fuera de metrics.json)</h2>')
    parts.append(table(['paso', 'clave', 'segundos', 'reutilizado'],
                       [['conversión', esc('%s %s' % (c['test'], c['range'])), num(c['seconds'], 4), esc(c['reused'])] for c in t['conversions']] +
                       [['revelado', esc(d['result']), num(d['seconds'], 4), esc(d['reused'])] for d in t['decodes']] +
                       [['total', '', num(t['total_seconds'], 4), '']]))
    parts.append(identity_section(run))
    if run.get('warnings'):
        parts.append('<h2>Avisos</h2><ul>%s</ul>' % ''.join('<li>%s</li>' % esc(w) for w in run['warnings']))
    parts.append('<p class="note">config_sha256 %s</p></html>' % esc(metrics['config_sha256']))
    with open(path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(parts))


def write_comparison_html(path, comparison, base, cand):
    parts = ['<!doctype html><html lang="es"><meta charset="utf-8"><title>Comparación</title><style>%s</style>' % CSS,
             '<h1>Comparación de ejecuciones</h1>',
             table(['', 'base', 'candidata'], [
                 ['ejecución', esc(base['run']['run_id']), esc(cand['run']['run_id'])],
                 ['commit', esc(base['run']['git'].get('commit')), esc(cand['run']['git'].get('commit'))],
                 ['cdng-convert', '<code>%s</code>' % esc(base['metrics']['build']['cdng_convert_sha256']), '<code>%s</code>' % esc(cand['metrics']['build']['cdng_convert_sha256'])],
                 ['decodificador', '<code>%s</code>' % esc(base['metrics']['build']['decoder_sha256'][:16]), '<code>%s</code>' % esc(cand['metrics']['build']['decoder_sha256'][:16])]]),
             comparison_section(comparison)]
    keys = sorted(set(base['metrics']['results']) & set(cand['metrics']['results']))
    rows = []
    for k in keys:
        fb, fc = base['metrics']['results'][k].get('reference_fidelity'), cand['metrics']['results'][k].get('reference_fidelity')
        if fb and fc:
            for label, p in SUMMARY:
                rows.append([esc(k), esc(label), num(get(fb, p)), num(get(fc, p))])
    if rows:
        parts.append('<h2>Métricas principales</h2>' + table(['resultado', 'métrica', 'base', 'candidata'], rows))
    parts.append('</html>')
    with open(path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(parts))
