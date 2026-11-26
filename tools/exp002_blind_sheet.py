#!/usr/bin/env python3
"""Hoja de contacto visual a ciegas EXP-002: copia PNG con IDs neutrales + mapa secreto."""
import argparse
import hashlib
import json
import os
import random
import shutil


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--runs', nargs='+', required=True, help='directorios de run')
    ap.add_argument('--dest', required=True)
    ap.add_argument('--png-key', default='zoom_card_black',
                    help='clave dentro de reference_fidelity.png')
    ap.add_argument('--seed', type=int, default=202)
    args = ap.parse_args()
    os.makedirs(args.dest, exist_ok=True)
    items = []
    for run in args.runs:
        metrics = json.load(open(os.path.join(run, 'metrics.json'), encoding='utf-8'))
        for key, res in metrics.get('results', {}).items():
            png = ((res.get('reference_fidelity') or {}).get('png') or {}).get(args.png_key)
            if not png:
                continue
            src = png if os.path.isabs(png) else os.path.join(run, png)
            if not os.path.isfile(src):
                # rutas relativas típicas del informe
                alt = os.path.join(run, 'png', os.path.basename(png))
                src = alt if os.path.isfile(alt) else src
            if not os.path.isfile(src):
                continue
            items.append({'run': os.path.basename(run), 'result': key, 'src': src})
    rng = random.Random(args.seed)
    rng.shuffle(items)
    mapping = []
    for i, it in enumerate(items):
        code = 'V%03d' % (i + 1)
        dest = os.path.join(args.dest, code + '.png')
        shutil.copy2(it['src'], dest)
        mapping.append({'id': code, 'run': it['run'], 'result': it['result'],
                        'sha256': hashlib.sha256(open(it['src'], 'rb').read()).hexdigest()})
    with open(os.path.join(args.dest, 'key.json'), 'w', encoding='utf-8') as f:
        json.dump({'seed': args.seed, 'png_key': args.png_key, 'items': mapping}, f, indent=2)
    # Hoja HTML sin revelar la clave
    cards = ''.join('<figure><img src="%s.png" width="240"><figcaption>%s</figcaption></figure>' % (m['id'], m['id'])
                    for m in mapping)
    html = """<!DOCTYPE html><html lang="es"><meta charset="utf-8"><title>EXP-002 blind sheet</title>
<style>body{font-family:system-ui;background:#111;color:#eee;display:flex;flex-wrap:wrap;gap:12px;padding:16px}
figure{margin:0;text-align:center} img{image-rendering:pixelated;background:#000}</style>
<body>%s<p>Clave en key.json (no abrir durante evaluación a ciegas).</p></body></html>""" % cards
    open(os.path.join(args.dest, 'sheet.html'), 'w', encoding='utf-8').write(html)
    print(args.dest, len(mapping), 'tiles')


if __name__ == '__main__':
    main()
