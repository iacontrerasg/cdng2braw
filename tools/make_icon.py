#!/usr/bin/env python3
"""Iconos de la app (propuestas). Dibuja a 4096 px y reduce a 1024 para suavizar los bordes.

  .venv/bin/python tools/make_icon.py --out app/icon          # propuestas 1, 2 y 3
  .venv/bin/python tools/make_icon.py --out app/icon --icns 2 # AppIcon.icns de la propuesta elegida
  python tools/make_icon.py --out app/icon --ico             # AppIcon.ico (Windows) desde AppIcon.png
"""
import argparse
import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

S = 4096
ORANGE, ORANGE_LIGHT = (184, 106, 18), (223, 160, 73)
GRAPHITE_TOP, GRAPHITE_BOTTOM = (52, 55, 62), (24, 26, 30)
BAYER = {'R': (214, 72, 64), 'G': (86, 176, 96), 'B': (70, 120, 214)}


def base(top, bottom):
    """Cuadrado redondeado de macOS: 824/1024 del lienzo, radio 185/1024, degradado vertical y sombra suave."""
    img = Image.new('RGBA', (S, S), (0, 0, 0, 0))
    m, r = S * 100 // 1024, S * 185 // 1024
    shadow = Image.new('RGBA', (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle([m, m + S // 80, S - m, S - m + S // 80], r, fill=(0, 0, 0, 110))
    img.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(S // 60)))
    gradient = Image.new('RGBA', (S, S))
    for y in range(S):
        f = y / S
        gradient.paste(tuple(int(top[i] * (1 - f) + bottom[i] * f) for i in range(3)) + (255,), (0, y, S, y + 1))
    mask = Image.new('L', (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([m, m, S - m, S - m], r, fill=255)
    img.paste(gradient, (0, 0), mask)
    return img


def bayer(draw, x, y, size, gap, radius):
    cell = (size - gap) / 2
    for (i, j), c in zip(((0, 0), (1, 0), (0, 1), (1, 1)), 'RGGB'):
        x0, y0 = x + i * (cell + gap), y + j * (cell + gap)
        draw.rounded_rectangle([x0, y0, x0 + cell, y0 + cell], radius, fill=BAYER[c])


def sensor():
    img = base(GRAPHITE_TOP, GRAPHITE_BOTTOM); d = ImageDraw.Draw(img)
    c, ring = S // 2, S * 300 // 1024
    d.ellipse([c - ring, c - ring, c + ring, c + ring], outline=ORANGE_LIGHT, width=S * 34 // 1024)
    size = S * 330 // 1024
    bayer(d, c - size // 2, c - size // 2, size, S * 26 // 1024, S * 40 // 1024)
    return img


def develop():
    img = base(GRAPHITE_TOP, GRAPHITE_BOTTOM); d = ImageDraw.Draw(img)
    size, y = S * 250 // 1024, S * 387 // 1024
    bayer(d, S * 205 // 1024, y, size, S * 18 // 1024, S * 26 // 1024)
    # flecha
    ax, ay = S * 490 // 1024, S // 2
    d.line([ax, ay, ax + S * 70 // 1024, ay], fill=ORANGE_LIGHT, width=S * 30 // 1024)
    d.polygon([(ax + S * 60 // 1024, ay - S * 45 // 1024), (ax + S * 110 // 1024, ay), (ax + S * 60 // 1024, ay + S * 45 // 1024)], fill=ORANGE_LIGHT)
    # fotograma revelado: cielo, montaña y sol
    fx0, fy0, fx1, fy1 = S * 625 // 1024, y, S * 875 // 1024, y + size
    frame = Image.new('RGBA', (fx1 - fx0, fy1 - fy0)); fd = ImageDraw.Draw(frame)
    for yy in range(frame.height):
        f = yy / frame.height
        fd.line([0, yy, frame.width, yy], fill=(int(235 - 60 * f), int(170 - 40 * f), int(90 - 20 * f), 255))
    fd.ellipse([frame.width * .55, frame.height * .18, frame.width * .82, frame.height * .45], fill=(255, 236, 200))
    fd.polygon([(0, frame.height), (frame.width * .35, frame.height * .45), (frame.width * .62, frame.height * .78),
                (frame.width * .8, frame.height * .6), (frame.width, frame.height)], fill=(70, 46, 30))
    mask = Image.new('L', frame.size, 0); ImageDraw.Draw(mask).rounded_rectangle([0, 0, *frame.size], S * 26 // 1024, fill=255)
    img.paste(frame, (fx0, fy0), mask)
    return img


def film():
    img = base(ORANGE_LIGHT, ORANGE); d = ImageDraw.Draw(img)
    x0, x1, y0, y1 = S * 215 // 1024, S * 809 // 1024, S * 300 // 1024, S * 724 // 1024
    d.rounded_rectangle([x0, y0, x1, y1], S * 40 // 1024, fill=(250, 246, 240))
    hole, step = S * 36 // 1024, S * 74 // 1024
    for x in range(x0 + S * 40 // 1024, x1 - hole, step):
        for yy in (y0 + S * 26 // 1024, y1 - S * 26 // 1024 - hole):
            d.rounded_rectangle([x, yy, x + hole, yy + hole], S * 8 // 1024, fill=ORANGE)
    size = S * 230 // 1024
    bayer(d, S // 2 - size // 2, S // 2 - size // 2, size, S * 16 // 1024, S * 22 // 1024)
    return img


DESIGNS = {1: sensor, 2: develop, 3: film}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--icns', type=int, choices=DESIGNS)
    ap.add_argument('--ico', action='store_true', help='AppIcon.ico para Windows, a partir de AppIcon.png')
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    if a.ico:
        # Windows: el .icns no sirve. Los siete tamaños son los que pide el Explorador (lista, iconos, barra).
        source = Image.open(a.out / 'AppIcon.png').convert('RGBA')
        sizes = [(s, s) for s in (256, 128, 64, 48, 32, 24, 16)]
        source.save(a.out / 'AppIcon.ico', format='ICO', sizes=sizes)
        print(a.out / 'AppIcon.ico')
        return
    if a.icns:
        icon = DESIGNS[a.icns]().resize((1024, 1024), Image.LANCZOS)
        with tempfile.TemporaryDirectory() as temp:
            iconset = Path(temp) / 'AppIcon.iconset'; iconset.mkdir()
            for size in (16, 32, 128, 256, 512):
                icon.resize((size, size), Image.LANCZOS).save(iconset / f'icon_{size}x{size}.png')
                icon.resize((size * 2, size * 2), Image.LANCZOS).save(iconset / f'icon_{size}x{size}@2x.png')
            subprocess.run(['iconutil', '-c', 'icns', iconset, '-o', a.out / 'AppIcon.icns'], check=True)
        icon.save(a.out / 'AppIcon.png')
        print(a.out / 'AppIcon.icns')
        return
    sheet = Image.new('RGB', (3 * 760, 700), (236, 238, 241))
    draw = ImageDraw.Draw(sheet)
    for k, design in DESIGNS.items():
        icon = design().resize((1024, 1024), Image.LANCZOS)
        icon.save(a.out / f'propuesta-{k}.png')
        x = (k - 1) * 760 + 20
        sheet.paste(icon.resize((512, 512), Image.LANCZOS), (x, 20), icon.resize((512, 512), Image.LANCZOS))
        for i, size in enumerate((128, 64, 32, 16)):
            small = icon.resize((size, size), Image.LANCZOS)
            sheet.paste(small, (x + 540, 20 + sum((128, 64, 32, 16)[:i]) + 20 * i), small)
        draw.text((x, 560), f'Propuesta {k}', fill=(28, 31, 36))
    sheet.save(a.out / 'propuestas.png')
    print(a.out / 'propuestas.png')


if __name__ == '__main__':
    main()
