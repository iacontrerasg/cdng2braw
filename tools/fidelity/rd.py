"""Análisis rate–distortion EXP-002: agregación de corridas, curvas, almacenamiento, incremental."""
import csv
import json
import math
import os
import re
from collections import defaultdict

from . import images, metrics as M, identity


PHOTOSITES_1080P = 1920 * 1080
RAW_BYTES_12BIT = PHOTOSITES_1080P * 12 / 8


def load_run(path):
    metrics = json.load(open(os.path.join(path, 'metrics.json'), encoding='utf-8'))
    run = json.load(open(os.path.join(path, 'run.json'), encoding='utf-8'))
    return {'dir': os.path.abspath(path), 'metrics': metrics, 'run': run,
            'id': os.path.basename(os.path.abspath(path))}


def parse_exp002_id(run_id):
    """exp002-a04-a1-q18 → {scope, alpha, q, ratio, ...}."""
    out = {'id': run_id, 'alpha': None, 'q': None, 'ratio': None, 'mode': None, 'tag': None}
    m = re.search(r'-a(1|050|075|0|bd)-', run_id)
    if m:
        out['alpha'] = {'1': 1.0, '050': 0.5, '075': 0.75, '0': 0.0, 'bd': None}[m.group(1)]
        if m.group(1) == 'bd':
            out['tag'] = 'bounded'
        elif m.group(1) == '0':
            out['tag'] = 'ablation'
    m = re.search(r'-q(\d{2})(?:-|$)', run_id)
    if m:
        out['q'] = int(m.group(1))
        out['mode'] = 'quality'
    m = re.search(r'-r(\d{2})(?:-|$)', run_id)
    if m:
        out['ratio'] = int(m.group(1))
        out['mode'] = 'ratio'
    return out


def frame_row(run, key, result):
    rf = result.get('reference_fidelity') or {}
    n = rf.get('normalized') or {}
    sh = rf.get('sharpness') or {}
    lc = (rf.get('luma_chroma') or {}).get('edges') or {}
    neut = rf.get('neutral') or {}
    eb = rf.get('experimental_block') or {}
    comp = result.get('compression') or {}
    fb = comp.get('frame_bytes')
    raw = RAW_BYTES_12BIT
    q_eff = None
    if comp.get('q_min') is not None and comp.get('q_max') is not None:
        q_eff = 0.5 * (comp['q_min'] + comp['q_max'])
    bits_per_site = (8.0 * fb / PHOTOSITES_1080P) if fb else None
    return {
        'run': run['id'], 'result': key, 'frame': result.get('frame'),
        'psnr': n.get('psnr_db'), 'rmse': n.get('rmse'),
        'duv_edges': lc.get('duv_mean'), 'duv_neutral': neut.get('duv_mean'),
        'grad': sh.get('gradient_energy_ratio_edges'),
        'lap': sh.get('laplacian_energy_ratio_edges'),
        'block_bmi': eb.get('border_minus_interior_mae'),
        'frame_bytes': fb, 'braw_bytes': comp.get('braw_bytes'),
        'ratio_achieved': comp.get('braw_ratio_achieved'),
        'frame_ratio': comp.get('frame_ratio') or ((raw / fb) if fb else None),
        'q_min': comp.get('q_min'), 'q_max': comp.get('q_max'), 'q_eff': q_eff,
        'bits_per_site': bits_per_site,
        'mb_s_24fps': (fb * 24 / 1e6) if fb else None,
        'gb_h_24fps': (fb * 24 * 3600 / 1e9) if fb else None,
    }


def collect(runs):
    rows = []
    for run in runs:
        meta = parse_exp002_id(run['id'])
        seconds = (run['run'].get('timing') or {}).get('total_seconds')
        fresh = [c for c in (run['run'].get('timing') or {}).get('conversions', []) if not c.get('reused')]
        for key, result in sorted(run['metrics'].get('results', {}).items()):
            row = frame_row(run, key, result)
            row.update(meta)
            row['run_seconds'] = seconds
            row['fresh_convert_seconds'] = sum(c.get('seconds') or 0 for c in fresh) or None
            rows.append(row)
    return rows


def aggregate(rows, keys=('alpha', 'q', 'ratio', 'mode', 'tag')):
    groups = defaultdict(list)
    for r in rows:
        k = tuple(r.get(x) for x in keys)
        groups[k].append(r)
    out = []
    def sort_key(kv):
        return tuple((-1 if x is None else x) if not isinstance(x, str) else x for x in kv[0])

    for k, rs in sorted(groups.items(), key=sort_key):
        def avg(field):
            vals = [r[field] for r in rs if r.get(field) is not None and r[field] == r[field]]
            return sum(vals) / len(vals) if vals else None
        item = dict(zip(keys, k))
        for f in ('psnr', 'rmse', 'duv_edges', 'duv_neutral', 'grad', 'lap', 'block_bmi',
                  'frame_bytes', 'braw_bytes', 'ratio_achieved', 'frame_ratio', 'q_eff',
                  'bits_per_site', 'mb_s_24fps', 'gb_h_24fps', 'run_seconds', 'fresh_convert_seconds'):
            item[f] = avg(f)
        item['n'] = len(rs)
        out.append(item)
    return out


def storage_table(agg_rows, capacities_gb=(500, 1000, 2000)):
    """Estimaciones a 1920×1080 24 fps a partir de bytes/fotograma observados."""
    out = []
    for r in agg_rows:
        fb = r.get('frame_bytes')
        if not fb:
            continue
        mb_s = fb * 24 / 1e6
        gb_h = fb * 24 * 3600 / 1e9
        row = {
            'label': r, 'mb_s': mb_s, 'gb_min': gb_h / 60, 'gb_h': gb_h,
            'hours': {c: (c / gb_h if gb_h else None) for c in capacities_gb},
        }
        out.append(row)
    return out


def incremental_pair(base_run_dir, cand_run_dir, analysis_cfg, peak=1.0):
    """Distorsión incremental: frames candidatas vs las del run base (misma precomp, q distinto)."""
    base = load_run(base_run_dir)
    cand = load_run(cand_run_dir)
    out = {}
    for key, cres in cand['metrics']['results'].items():
        bres = base['metrics']['results'].get(key)
        if not bres:
            continue
        bpath = os.path.join(base['dir'], 'frames', key + '.f32')
        cpath = os.path.join(cand['dir'], 'frames', key + '.f32')
        if not (os.path.isfile(bpath) and os.path.isfile(cpath)):
            continue
        bimg = images.read_f32(bpath)
        cimg = images.read_f32(cpath)
        fid, _ = M.fidelity(bimg, cimg, analysis_cfg, norm=1.0, ceiling=None)
        # Error cruzado respecto a una referencia común no está aquí; solo incremental.
        out[key] = {
            'identical': bool((bimg == cimg).all()) if hasattr(bimg, '__eq__') else False,
            'normalized': fid.get('normalized'),
            'base_sha': bres.get('candidate_sha256'),
            'cand_sha': cres.get('candidate_sha256'),
        }
    return out


def write_csv(path, rows):
    if not rows:
        open(path, 'w').write('')
        return
    keys = sorted({k for r in rows for k in r})
    with open(path, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=keys, extrasaction='ignore')
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k) for k in keys})


def write_html(path, title, agg, series=None):
    def js(obj):
        return json.dumps(obj, allow_nan=False)
    # Series por alpha para curvas PSNR vs q / vs bits
    by_alpha = defaultdict(list)
    for r in agg:
        if r.get('mode') == 'quality' and r.get('q') is not None and r.get('alpha') is not None:
            by_alpha[r['alpha']].append(r)
    for a in by_alpha:
        by_alpha[a] = sorted(by_alpha[a], key=lambda x: x['q'])
    html = f"""<!DOCTYPE html>
<html lang="es"><head><meta charset="utf-8"><title>{title}</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
body{{font-family:ui-sans-serif,system-ui,sans-serif;margin:2rem;background:#111;color:#eee}}
h1,h2{{font-weight:600}} table{{border-collapse:collapse;width:100%;font-size:13px}}
td,th{{border:1px solid #333;padding:4px 8px;text-align:right}} th{{text-align:left}}
.card{{background:#1a1a1a;padding:1rem;margin:1rem 0;border-radius:8px}}
canvas{{max-height:360px}}
</style></head><body>
<h1>{title}</h1>
<p>EXP-002 — curvas a partir de corridas del banco (PSNR / croma / detalle frente a q y bitrate).</p>
<div class="card"><h2>PSNR vs q</h2><canvas id="psnr_q"></canvas></div>
<div class="card"><h2>PSNR vs bits/fotosito</h2><canvas id="psnr_bits"></canvas></div>
<div class="card"><h2>Δu'v' bordes vs q</h2><canvas id="duv_q"></canvas></div>
<div class="card"><h2>Gradiente vs q</h2><canvas id="grad_q"></canvas></div>
<div class="card"><h2>Tabla agregada</h2>
<table><tr><th>α</th><th>q/ratio</th><th>PSNR</th><th>Δu'v'e</th><th>grad</th><th>blockΔ</th>
<th>B/frame</th><th>bits/sitio</th><th>GB/h</th><th>q_eff</th><th>ratio</th></tr>
"""
    for r in agg:
        mode = f"q{r['q']}" if r.get('q') is not None else (f"{r['ratio']}:1" if r.get('ratio') else '—')
        html += ("<tr><td>{a}</td><td>{m}</td><td>{psnr:.3f}</td><td>{duv:.5f}</td><td>{g:.4f}</td>"
                 "<td>{b:.3e}</td><td>{fb:.0f}</td><td>{bps:.3f}</td><td>{gb:.2f}</td>"
                 "<td>{qe}</td><td>{ra}</td></tr>".format(
                     a=r.get('alpha'), m=mode,
                     psnr=r['psnr'] or 0, duv=r['duv_edges'] or 0, g=r['grad'] or 0,
                     b=r['block_bmi'] or 0, fb=r['frame_bytes'] or 0, bps=r['bits_per_site'] or 0,
                     gb=r['gb_h_24fps'] or 0,
                     qe=('%.1f' % r['q_eff']) if r.get('q_eff') is not None else '—',
                     ra=('%.3f' % r['ratio_achieved']) if r.get('ratio_achieved') is not None else '—'))
    html += "</table></div><script>\n"
    colors = {1.0: '#6cf', 0.5: '#fc6', 0.75: '#c6f', 0.0: '#aaa'}
    datasets_q, datasets_bits, datasets_duv, datasets_grad = [], [], [], []
    for a, rows in sorted(by_alpha.items()):
        c = colors.get(a, '#fff')
        datasets_q.append({'label': f'α={a}', 'data': [{'x': r['q'], 'y': r['psnr']} for r in rows if r.get('psnr') is not None],
                           'borderColor': c, 'tension': 0.2})
        datasets_bits.append({'label': f'α={a}', 'data': [{'x': r['bits_per_site'], 'y': r['psnr']} for r in rows
                                                          if r.get('psnr') is not None and r.get('bits_per_site')],
                              'borderColor': c, 'tension': 0.2})
        datasets_duv.append({'label': f'α={a}', 'data': [{'x': r['q'], 'y': r['duv_edges']} for r in rows if r.get('duv_edges') is not None],
                             'borderColor': c, 'tension': 0.2})
        datasets_grad.append({'label': f'α={a}', 'data': [{'x': r['q'], 'y': r['grad']} for r in rows if r.get('grad') is not None],
                              'borderColor': c, 'tension': 0.2})
    html += f"""
const scatter = (id, ds, xTitle, yTitle) => new Chart(document.getElementById(id), {{
  type:'scatter', data:{{datasets: ds.map(d => ({{...d, showLine:true}}))}},
  options:{{scales:{{x:{{title:{{display:true,text:xTitle}}}}, y:{{title:{{display:true,text:yTitle}}}}}}}}
}});
scatter('psnr_q', {js(datasets_q)}, 'q', 'PSNR (dB)');
scatter('psnr_bits', {js(datasets_bits)}, 'bits/fotosito', 'PSNR (dB)');
scatter('duv_q', {js(datasets_duv)}, 'q', \"Δu'v' bordes\");
scatter('grad_q', {js(datasets_grad)}, 'q', 'gradiente (ratio)');
</script></body></html>
"""
    with open(path, 'w', encoding='utf-8') as f:
        f.write(html)


def build_report(run_dirs, dest, analysis_cfg=None):
    os.makedirs(dest, exist_ok=True)
    runs = [load_run(d) for d in run_dirs]
    rows = collect(runs)
    agg = aggregate(rows)
    write_csv(os.path.join(dest, 'frames.csv'), rows)
    write_csv(os.path.join(dest, 'aggregate.csv'), agg)
    write_html(os.path.join(dest, 'rd.html'), 'EXP-002 Rate–Distortion', agg)
    summary = {
        'n_runs': len(runs), 'n_frames': len(rows), 'aggregate': agg,
        'storage_1080p24': [
            {'alpha': r.get('alpha'), 'q': r.get('q'), 'ratio': r.get('ratio'),
             'mb_s': r.get('mb_s_24fps'), 'gb_h': r.get('gb_h_24fps'),
             'hours_500gb': (500 / r['gb_h_24fps']) if r.get('gb_h_24fps') else None,
             'hours_1tb': (1000 / r['gb_h_24fps']) if r.get('gb_h_24fps') else None,
             'hours_2tb': (2000 / r['gb_h_24fps']) if r.get('gb_h_24fps') else None}
            for r in agg if r.get('frame_bytes')
        ],
    }
    with open(os.path.join(dest, 'summary.json'), 'w', encoding='utf-8') as f:
        json.dump(summary, f, indent=2, sort_keys=True)
    return summary
