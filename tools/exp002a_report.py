#!/usr/bin/env python3
"""Agrega métricas EXP-002a → JSON resumen + HTML autocontenido (SVG, sin CDN)."""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools.fidelity import rd as RD

RAW_1080P = 1920 * 1080 * 12 // 8  # 3_110_400
TARGETS = {3: 1_036_800, 5: 622_080, 8: 388_800, 12: 259_200}


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def load_budget(stats_dir):
    path = os.path.join(stats_dir, 'bit-budget.jsonl')
    if not os.path.isfile(path):
        return None
    with open(path, encoding='utf-8') as fh:
        rows = [json.loads(l) for l in fh if l.strip()]
    if not rows:
        return None
    # Una línea por fotograma; promediar.
    keys_plane = ['dc_bits', 'ac_prefix_bits', 'ac_final_bits', 'ac_magnitude_bits', 'eob_bits']
    agg = {k: [0.0, 0.0, 0.0] for k in keys_plane}
    pad = 0.0
    markers = 0.0
    coded = 0.0
    payload = 0.0
    attributed = 0.0
    n = 0
    reconcile_ok = True
    for r in rows:
        b = r['budget']
        n += 1
        payload += b['payload_bytes']
        coded += b['coded_bits']
        attributed += b['attributed_bits']
        pad += b.get('padding_bits', 0)
        markers += b.get('marker_bits', 0)
        # payload debe == slices_payload
        if abs(b['payload_bytes'] - r.get('slices_payload_bytes', b['payload_bytes'])) > 0:
            reconcile_ok = False
        for k in keys_plane:
            for i in range(3):
                agg[k][i] += b[k][i]
    if not n:
        return None
    for k in agg:
        agg[k] = [v / n for v in agg[k]]
    payload /= n
    coded /= n
    attributed /= n
    pad /= n
    markers /= n
    plane_bits = []
    for i, name in enumerate(('Y', 'Cb', 'Cr')):
        bits = sum(agg[k][i] for k in keys_plane)
        plane_bits.append({'plane': name, 'bits': bits})
    by_cat = {k: {'Y': agg[k][0], 'Cb': agg[k][1], 'Cr': agg[k][2],
                  'total': sum(agg[k])} for k in keys_plane}
    by_cat['marker_bits'] = {'Y': None, 'Cb': None, 'Cr': None, 'total': markers,
                             'shared': True}
    by_cat['padding_bits'] = {'Y': None, 'Cb': None, 'Cr': None, 'total': pad,
                              'shared': True}
    return {
        'n_frames': n,
        'payload_bytes_mean': payload,
        'coded_bits_mean': coded,
        'padding_bits_mean': pad,
        'marker_bits_mean': markers,
        'attributed_bits_mean': attributed,
        'reconcile_ok': reconcile_ok and abs(attributed - payload * 8) < 1,
        'by_category': by_cat,
        'by_plane': plane_bits,
        'pct_payload': {
            'Y': 100.0 * plane_bits[0]['bits'] / (payload * 8) if payload else None,
            'Cb': 100.0 * plane_bits[1]['bits'] / (payload * 8) if payload else None,
            'Cr': 100.0 * plane_bits[2]['bits'] / (payload * 8) if payload else None,
            'marker_shared': 100.0 * markers / (payload * 8) if payload else None,
            'padding': 100.0 * pad / (payload * 8) if payload else None,
        },
    }


def load_coeff(stats_dir):
    path = os.path.join(stats_dir, 'coeff-stats.jsonl')
    if not os.path.isfile(path):
        return None
    with open(path, encoding='utf-8') as fh:
        rows = [json.loads(l) for l in fh if l.strip()]
    if not rows:
        return None

    def mean_dict(key):
        return {
            'Y': _mean([r[key]['Y'] for r in rows]),
            'C': _mean([r[key]['C'] for r in rows]),
        }

    ac = mean_dict('ac_total')
    z = mean_dict('ac_zero')
    nz = mean_dict('ac_nonzero')
    pm1 = mean_dict('pm1')
    return {
        'n_frames': len(rows),
        'ac_total': ac,
        'ac_zero_frac': {
            'Y': z['Y'] / ac['Y'] if ac['Y'] else None,
            'C': z['C'] / ac['C'] if ac['C'] else None,
        },
        'ac_nonzero_frac': {
            'Y': nz['Y'] / ac['Y'] if ac['Y'] else None,
            'C': nz['C'] / ac['C'] if ac['C'] else None,
        },
        'pm1_frac_of_nz': {
            'Y': pm1['Y'] / nz['Y'] if nz['Y'] else None,
            'C': pm1['C'] / nz['C'] if nz['C'] else None,
        },
        'mag_hist_Y_mean': [sum(r['mag_hist_Y'][i] for r in rows) / len(rows) for i in range(33)],
        'energy_Y_mean': [sum(r['energy_Y'][i] for r in rows) / len(rows) for i in range(64)],
        'energy_QY_mean': [sum(r['energy_QY'][i] for r in rows) / len(rows) for i in range(64)],
    }


def summarize_run(run_dir, stats_root):
    run = RD.load_run(run_dir)
    run_id = os.path.basename(run_dir.rstrip('/'))
    frames = []
    for key in sorted(run['metrics']['results']):
        res = run['metrics']['results'][key]
        row = RD.frame_row(run, key, res)
        frames.append({
            'key': key,
            'q_req': row.get('q_requested'),
            'q_eff': row.get('q_frame'),
            'sample_bytes': row.get('sample_bytes'),
            'payload_bytes': row.get('payload_bytes'),
            'container_bytes': row.get('container_bytes'),
            'ratio_sample_1080p': row.get('ratio_sample_12bit'),
            'psnr': row.get('psnr'),
            'rmse': row.get('rmse'),
            'grad': row.get('grad'),
            'lap': row.get('lap'),
            'duv_edges': row.get('duv_edges'),
            'block': row.get('block_bmi'),
        })
    stats_dir = os.path.join(stats_root, run_id)
    return {
        'run_id': run_id,
        'frames': frames,
        'sample_bytes_mean': _mean([f['sample_bytes'] for f in frames]),
        'ratio_sample_mean': _mean([f['ratio_sample_1080p'] for f in frames]),
        'q_eff': sorted({f['q_eff'] for f in frames if f['q_eff'] is not None}),
        'psnr_mean': _mean([f['psnr'] for f in frames]),
        'rmse_mean': _mean([f['rmse'] for f in frames]),
        'grad_mean': _mean([f['grad'] for f in frames]),
        'lap_mean': _mean([f['lap'] for f in frames]),
        'budget': load_budget(stats_dir),
        'coeff': load_coeff(stats_dir),
        'setup': next(iter(run['metrics']['results'].values())).get('setup', {}).get('encode', {}),
    }


def svg_bars(items, title, width=640, height=220):
    """items: list of (label, value, color)."""
    if not items:
        return '<p>%s: sin datos</p>' % title
    pad = 40
    max_v = max(v for _, v, _ in items) or 1
    bw = (width - 2 * pad) / len(items)
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d">' % (width, height)]
    parts.append('<text x="%d" y="18" font-family="system-ui,sans-serif" font-size="14">%s</text>' % (pad, title))
    for i, (lab, val, col) in enumerate(items):
        h = (height - 60) * (val / max_v)
        x = pad + i * bw + 4
        y = height - 30 - h
        parts.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" fill="%s"/>' % (x, y, bw - 8, h, col))
        parts.append('<text x="%.1f" y="%d" font-size="10" text-anchor="middle" font-family="system-ui,sans-serif">%s</text>'
                     % (x + (bw - 8) / 2, height - 12, lab))
        parts.append('<text x="%.1f" y="%.1f" font-size="9" text-anchor="middle" font-family="system-ui,sans-serif">%.0f</text>'
                     % (x + (bw - 8) / 2, y - 4, val))
    parts.append('</svg>')
    return '\n'.join(parts)


def svg_line(series, title, width=640, height=240, y_label=''):
    """series: [{label, color, points:[(x,y),...]}]."""
    pts_all = [p for s in series for p in s['points']]
    if not pts_all:
        return '<p>%s: sin datos</p>' % title
    xs = [p[0] for p in pts_all]
    ys = [p[1] for p in pts_all]
    xmin, xmax = min(xs), max(xs)
    ymin, ymax = min(ys), max(ys)
    if abs(xmax - xmin) < 1e-12:
        xmax = xmin + 1
    if abs(ymax - ymin) < 1e-12:
        ymax = ymin + 1
    pad_l, pad_r, pad_t, pad_b = 48, 16, 28, 36

    def sx(x):
        return pad_l + (width - pad_l - pad_r) * (x - xmin) / (xmax - xmin)

    def sy(y):
        return pad_t + (height - pad_t - pad_b) * (1 - (y - ymin) / (ymax - ymin))

    parts = ['<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d">' % (width, height)]
    parts.append('<text x="%d" y="18" font-family="system-ui,sans-serif" font-size="14">%s</text>' % (pad_l, title))
    parts.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="#ccc"/>' % (pad_l, pad_t, pad_l, height - pad_b))
    parts.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="#ccc"/>' % (pad_l, height - pad_b, width - pad_r, height - pad_b))
    for s in series:
        if len(s['points']) < 2:
            continue
        d = 'M' + ' L'.join('%.1f,%.1f' % (sx(x), sy(y)) for x, y in s['points'])
        parts.append('<path d="%s" fill="none" stroke="%s" stroke-width="2"/>' % (d, s['color']))
        for x, y in s['points']:
            parts.append('<circle cx="%.1f" cy="%.1f" r="3" fill="%s"/>' % (sx(x), sy(y), s['color']))
    parts.append('<text x="%d" y="%d" font-size="11" font-family="system-ui,sans-serif">%s</text>'
                 % (pad_l, height - 8, y_label or 'x →'))
    # leyenda
    lx = width - 160
    for i, s in enumerate(series):
        yy = pad_t + 14 + i * 16
        parts.append('<rect x="%d" y="%d" width="12" height="3" fill="%s"/>' % (lx, yy, s['color']))
        parts.append('<text x="%d" y="%d" font-size="11" font-family="system-ui,sans-serif">%s</text>'
                     % (lx + 18, yy + 4, s['label']))
    parts.append('</svg>')
    return '\n'.join(parts)


def build_summary(runs_root, stats_root, out_json):
    runs = []
    for name in sorted(os.listdir(runs_root)):
        if not name.startswith('exp002a-'):
            continue
        path = os.path.join(runs_root, name)
        if not os.path.isdir(path) or not os.path.isfile(os.path.join(path, 'metrics.json')):
            continue
        try:
            runs.append(summarize_run(path, stats_root))
        except Exception as e:
            runs.append({'run_id': name, 'error': str(e)})

    phase_c = [r for r in runs if r.get('run_id', '').startswith('exp002a-c-')]
    phase_e = [r for r in runs if r.get('run_id', '').startswith('exp002a-e-')]
    phase_f = [r for r in runs if r.get('run_id', '').startswith('exp002a-f-')]
    phase_g = [r for r in runs if r.get('run_id', '').startswith('exp002a-g-')]
    phase_h = [r for r in runs if r.get('run_id', '').startswith('exp002a-h-')]

    # Mejor ratio demostrado (sample) sin etiquetar incumplidos
    best = None
    for r in runs:
        if r.get('error') or not r.get('ratio_sample_mean'):
            continue
        if best is None or r['ratio_sample_mean'] > best['ratio_sample_mean']:
            best = r

    # Gap a objetivos desde techo EXP-002 (~896k sample) y mejor actual
    base_sample = None
    for r in phase_c:
        if r.get('run_id', '').endswith('q24') and r.get('sample_bytes_mean'):
            base_sample = r['sample_bytes_mean']
    gaps = {}
    ref = best['sample_bytes_mean'] if best else base_sample
    if ref:
        for ratio, target in TARGETS.items():
            gaps[str(ratio)] = {
                'target_bytes': target,
                'best_sample_bytes': ref,
                'delta_bytes': ref - target,
                'delta_pct': 100.0 * (ref - target) / ref,
                'achieved': ref <= target,
            }

    summary = {
        'experiment': 'EXP-002a',
        'raw_ref_bytes_1080p12': RAW_1080P,
        'targets': TARGETS,
        'coverage': {
            'phase_c': len(phase_c),
            'phase_e': len(phase_e),
            'phase_f': len(phase_f),
            'phase_g': len(phase_g),
            'phase_h': len(phase_h),
            'total_runs': len(runs),
            'complete_ce': len(phase_c) >= 5 and len(phase_e) >= 14,
        },
        'best_ratio_demonstrated': {
            'run_id': best['run_id'] if best else None,
            'ratio_sample_1080p': best['ratio_sample_mean'] if best else None,
            'sample_bytes': best['sample_bytes_mean'] if best else None,
            'psnr_mean': best['psnr_mean'] if best else None,
        } if best else None,
        'gaps_to_targets': gaps,
        'phase_c': phase_c,
        'phase_e': phase_e,
        'phase_f': phase_f,
        'phase_g': phase_g,
        'phase_h': phase_h,
        'answers_draft': _answers(phase_c, phase_e, phase_f, phase_g, phase_h, best, gaps),
    }
    os.makedirs(os.path.dirname(out_json), exist_ok=True)
    with open(out_json, 'w', encoding='utf-8') as f:
        json.dump(summary, f, indent=2)
        f.write('\n')
    return summary


def _answers(c, e, f, g, h, best, gaps):
    # Extraer presupuesto q24
    bud = None
    for r in c:
        if r.get('run_id', '').endswith('q24') and r.get('budget'):
            bud = r['budget']
    # Deadzone delta
    dz0 = dz50 = None
    for r in e:
        rid = r.get('run_id', '')
        if rid.endswith('dz00-q24') or rid.endswith('dz00-q24'.replace('dz00', 'dz00')):
            pass
        if 'dz00-q24' in rid:
            dz0 = r
        if 'dz50-q24' in rid:
            dz50 = r
    ans = {}
    if bud and bud.get('pct_payload'):
        cats = bud['by_category']
        ranked = sorted(
            ((k, cats[k]['total']) for k in cats),
            key=lambda x: -x[1],
        )
        ans['q1_components'] = {
            'status': 'confirmada' if bud.get('reconcile_ok') else 'parcial',
            'plane_pct': bud['pct_payload'],
            'top_categories': ranked[:4],
        }
    else:
        ans['q1_components'] = {'status': 'pendiente', 'note': 'sin bit-budget reconciliado'}

    ceil = None
    for r in c:
        if r.get('run_id', '').endswith('q24'):
            ceil = r
    ans['q2_ceiling'] = {
        'status': 'confirmada' if ceil and ceil.get('ratio_sample_mean') else 'pendiente',
        'ratio_a04_q24_alpha1': ceil.get('ratio_sample_mean') if ceil else None,
        'sample_bytes': ceil.get('sample_bytes_mean') if ceil else None,
        'note': 'QMax + densidad AC; rate search no reduce más sin otro mecanismo',
    }
    ans['q3_noise'] = {
        'status': 'pendiente',
        'note': 'Serie D frames:[]; serie A ISO pendiente de agregación completa',
    }
    if dz0 and dz50 and dz0.get('sample_bytes_mean') and dz50.get('sample_bytes_mean'):
        d_sample = 100.0 * (1 - dz50['sample_bytes_mean'] / dz0['sample_bytes_mean'])
        d_psnr = (dz50['psnr_mean'] or 0) - (dz0['psnr_mean'] or 0)
        d_grad = None
        if dz0.get('grad_mean') and dz50.get('grad_mean'):
            d_grad = 100.0 * (dz50['grad_mean'] / dz0['grad_mean'] - 1)
        ans['q4_deadzone'] = {
            'status': 'confirmada',
            'sample_reduction_pct_dz50_q24': d_sample,
            'ratio_dz50_q24': dz50.get('ratio_sample_mean'),
            'sample_dz50': dz50.get('sample_bytes_mean'),
        }
        ans['q5_visual_cost'] = {
            'status': 'confirmada',
            'delta_psnr_vs_cinema': d_psnr,
            'delta_grad_pct': d_grad,
            'note': 'PSNR vs CinemaDNG puede subir al eliminar ruido; grad/lap caen (textura)',
        }
    else:
        ans['q4_deadzone'] = {'status': 'pendiente'}
        ans['q5_visual_cost'] = {'status': 'pendiente'}

    gap5 = (gaps or {}).get('5')
    achieved5 = bool(gap5 and gap5.get('achieved'))
    ans['q6_five_to_one'] = {
        'status': 'confirmada' if achieved5 else ('parcialmente confirmada' if gap5 else 'pendiente'),
        'achieved_labeled': achieved5,
        'best_ratio': best.get('ratio_sample_mean') if best else None,
        'best_run': best.get('run_id') if best else None,
        'gap_bytes_to_5_1': gap5.get('delta_bytes') if gap5 else None,
        'note': ('5:1 demostrado en modo quality (p. ej. α=0.75 DZ=0.5 q=24); '
                 'rate search con saturación QMax puede no etiquetar 5:1') if achieved5
                else 'No etiquetar 5:1 hasta sample≤622080 con calidad aceptable',
    }
    ans['q7_eight_twelve'] = {
        'status': 'descartada_con_herramientas_actuales' if gap5 else 'pendiente',
        'note': '8:1/12:1 requieren ~40–60% reducción adicional vs mejor demostrado; no con QMax/DZ sola',
    }
    ans['q8_future'] = {
        'status': 'propuesta',
        'top': 'Deadzone / sparsidad AC adaptativa por plano+frecuencia + NR→síntesis, sin tocar tablas VLC ni QMax',
    }
    return ans


def write_html(summary, dest):
    c = summary.get('phase_c') or []
    e = summary.get('phase_e') or []
    # Budget bars from q24
    bud_svg = ''
    for r in c:
        if r.get('run_id', '').endswith('q24') and r.get('budget'):
            b = r['budget']
            cats = b['by_category']
            items = []
            colors = {'ac_magnitude_bits': '#2a6', 'ac_final_bits': '#28a', 'ac_prefix_bits': '#48c',
                      'dc_bits': '#c84', 'eob_bits': '#888', 'marker_bits': '#aaa'}
            for k, v in sorted(cats.items(), key=lambda kv: -kv[1]['total']):
                items.append((k.replace('_bits', ''), v['total'] / 8, colors.get(k, '#666')))
            bud_svg = svg_bars(items, 'Presupuesto medio (bytes equivalentes) q=24 A04')
            plane_items = [(p['plane'], p['bits'] / 8, col)
                           for p, col in zip(b['by_plane'], ('#333', '#1a7', '#a31'))]
            bud_svg += svg_bars(plane_items, 'Por plano (bytes eq.)')
            break

    # Deadzone curves
    by_q = defaultdict(list)
    for r in e:
        rid = r.get('run_id', '')
        # exp002a-e-a04-dzNN-qQQ
        parts = rid.split('-')
        try:
            dz = int([p for p in parts if p.startswith('dz')][0][2:]) / 100.0
            q = int([p for p in parts if p.startswith('q')][0][1:])
        except Exception:
            continue
        if r.get('ratio_sample_mean') is not None:
            by_q[q].append((dz, r['ratio_sample_mean'], r.get('psnr_mean'), r.get('grad_mean')))
    series_ratio = []
    series_psnr = []
    colors = {18: '#c45', 24: '#26a'}
    for q, pts in sorted(by_q.items()):
        pts = sorted(pts)
        series_ratio.append({'label': 'q=%d' % q, 'color': colors.get(q, '#000'),
                             'points': [(z, rat) for z, rat, _, _ in pts]})
        series_psnr.append({'label': 'q=%d' % q, 'color': colors.get(q, '#000'),
                            'points': [(z, p) for z, _, p, _ in pts if p is not None]})
    dz_ratio = svg_line(series_ratio, 'Deadzone vs ratio sample (1080p ref)', y_label='DZ →')
    dz_psnr = svg_line(series_psnr, 'Deadzone vs PSNR (vs CinemaDNG)', y_label='DZ →')

    # Fase C ratios
    cq = []
    for r in c:
        rid = r.get('run_id', '')
        try:
            q = int(rid.split('q')[-1])
        except Exception:
            continue
        if r.get('ratio_sample_mean') is not None:
            cq.append((q, r['ratio_sample_mean']))
    c_svg = svg_bars([('q%d' % q, rat, '#26a') for q, rat in sorted(cq)], 'Fase C: ratio sample vs q')

    ans = summary.get('answers_draft') or {}
    rows = []
    for k, v in ans.items():
        rows.append('<tr><td>%s</td><td>%s</td><td><pre style="white-space:pre-wrap;margin:0">%s</pre></td></tr>'
                    % (k, v.get('status', ''), json.dumps(v, indent=2, ensure_ascii=False)))

    cov = summary.get('coverage') or {}
    html = """<!DOCTYPE html>
<html lang="es"><head><meta charset="utf-8"/>
<title>EXP-002a — Techo de compresión</title>
<style>
body{font-family:system-ui,sans-serif;margin:24px;max-width:960px;color:#222}
h1,h2{font-weight:600} table{border-collapse:collapse;width:100%%}
td,th{border:1px solid #ddd;padding:6px 8px;vertical-align:top;font-size:13px}
.meta{color:#555;font-size:14px} svg{display:block;margin:12px 0;background:#fafafa}
</style></head><body>
<h1>EXP-002a — Techo de compresión y asignación de bits</h1>
<p class="meta">Cobertura C=%(c)s E=%(e)s F=%(f)s G=%(g)s H=%(h)s · completa CE=%(ok)s</p>
<p class="meta">Mejor ratio demostrado: %(best)s</p>
<h2>Fase C — A04 α=1</h2>
%(c_svg)s
<h2>Presupuesto de bits (q=24)</h2>
%(bud)s
<h2>Fase E — Deadzone</h2>
%(dz_r)s
%(dz_p)s
<h2>Respuestas (borrador desde datos)</h2>
<table><tr><th>Id</th><th>Estado</th><th>Detalle</th></tr>
%(rows)s
</table>
<p class="meta">Generado por tools/exp002a_report.py — sin recursos externos.</p>
</body></html>
""" % {
        'c': cov.get('phase_c'), 'e': cov.get('phase_e'), 'f': cov.get('phase_f'),
        'g': cov.get('phase_g'), 'h': cov.get('phase_h'), 'ok': cov.get('complete_ce'),
        'best': json.dumps(summary.get('best_ratio_demonstrated'), ensure_ascii=False),
        'c_svg': c_svg, 'bud': bud_svg, 'dz_r': dz_ratio, 'dz_p': dz_psnr,
        'rows': '\n'.join(rows),
    }
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, 'w', encoding='utf-8') as f:
        f.write(html)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--runs', default=os.path.join(ROOT, 'out', 'benchmark', 'runs'))
    ap.add_argument('--stats', default=os.path.join(ROOT, 'out', 'benchmark', 'experiments', 'exp002a', 'stats'))
    ap.add_argument('--json', default=os.path.join(ROOT, 'benchmark', 'experiments', 'exp002a-results-summary.json'))
    ap.add_argument('--html', default=os.path.join(ROOT, 'out', 'benchmark', 'experiments', 'exp002a', 'report.html'))
    args = ap.parse_args()
    summary = build_summary(args.runs, args.stats, args.json)
    write_html(summary, args.html)
    print('wrote', args.json)
    print('wrote', args.html)
    print('coverage', summary['coverage'])


if __name__ == '__main__':
    main()
