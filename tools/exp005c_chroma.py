"""EXP-005c G: álgebra NativeSensor vs ChannelCodec hasta el bitstream.

No equivale al pipeline propietario de Blackmagic. Solo el código de este repo.
"""
from __future__ import annotations

import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def code_from_ycc():
    path = os.path.join(ROOT, 'model', 'pocket_og_cinemadng_signal.json')
    with open(path, encoding='utf-8') as f:
        return json.load(f)['code_from_ycc']


def ycc_from_codes(r, g, b):
    """Inversa de code_from_ycc: Y=(R+2G+B)/4, Cb=(B−G)/2, Cr=(R−G)/2."""
    return ((r + 2 * g + b) / 4.0, (b - g) / 2.0, (r - g) / 2.0)


def codes_from_ycc(y, cb, cr):
    return (y - 0.5 * cb + 1.5 * cr, y - 0.5 * cb - 0.5 * cr, y + 1.5 * cb - 0.5 * cr)


TRACE = {
    'native_sensor': [
        'RGB lineal (AHD o reconstruct)',
        'opcional rcm_tone',
        'por canal: curve.encode(c·scale) − offset  → códigos absolutos R,G,B',
        'YCC = inv(code_from_ycc)·códigos  (= Y=(R+2G+B)/4, Cb=(B−G)/2, Cr=(R−G)/2)',
        'imagePlanes C0: Y full; Cb/Cr = map(media 2×2 RGB lineal)',
        'finishPlanes: NR opcional; **no** convierte proporciones (channelCodec=null)',
        'canvasPlanes → DCT → VLC → bitstream Y/Cb/Cr en códigos',
        'SDK: inv_sqrt del perfil + CSC (matrices D65, r_gain/b_gain, ISO post_gain)',
    ],
    'channel_codec_va': [
        'muestra lineal por fotosito × gain_ch × full',
        'encode: k0 + s√x (toe SDK bajo la rodilla)',
        'signal(code) = code − vertex  ∝ √(lineal)  (arriba de la rodilla)',
        'Y = blur 3×3 de códigos de fotosito (no RGB completo)',
        'Ĝ en R/B: cúbico direccional en lineal, luego encode verde',
        'proporción: (signal(Ch)−signal(Ĝ)) / max(signal(Ĝ), 1)  filtrada binomial 7 en sitios 4:2:0',
        'sitios: Cr UL (site 0), Cb DR (site 1) del cuarteto — asume fase tipo RGGB',
        'finishPlanes: sharpen Y 1.2; Cb = ρb·signal(Y_DR) / (2(1+(ρb+ρr)/4)); igual Cr con Y_UL',
        'bitstream: mismos códigos Y/Cb/Cr absolutos (ya no hay proporciones)',
        'SDK Sigma: perfil Camera Support fp + analog_gain del clip',
    ],
    'intensity_edge_constant_chromaticity': (
        'Si B_lin = c_b·I y G_lin = c_g·I, signal_VA ∝ √I ⇒ signal_B/signal_G = √(c_b/c_g) '
        'independiente de I. Las proporciones sobreviven a un borde de intensidad. '
        'Cb absoluto (B−G)/2 en dominio de curva **no** es invariante con I (la curva no es lineal). '
        'C0 OG promedia RGB lineal 2×2 y luego aplica la curva: mezcla espacial antes de la no linealidad.'
    ),
    'high_frequency_periodic': (
        'El binomial 7 sobre numerador/denominador de la proporción atenúa el zippering CFA '
        'antes del 4:2:0. NativeSensor C0 no ve el mosaico: el patrón periódico de las reglas '
        'entra como RGB AHD (false color ya interpolado) y luego caja 2×2.'
    ),
    'survives_finishPlanes': (
        'Sí, como **construcción** de códigos: finishPlanes reescribe ρ·signal(Y_local) → Cb/Cr absolutos, '
        'así el bitstream es el mismo álgebra YCC que NativeSensor. No sobreviven como campo de proporciones. '
        'DCT + cuantización Y y C independientes pueden romper ρ∝Y. El SDK no relee proporciones: solo códigos + inv_sqrt.'
    ),
    'not_blackmagic_proprietary': True,
}
