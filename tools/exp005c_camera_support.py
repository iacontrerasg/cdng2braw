"""EXP-005c H: inventario de perfiles Camera Support (solo lectura)."""
from __future__ import annotations

import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OG_ISO = os.path.join(ROOT, 'model', 'pocket_og_cinemadng_iso_camera_support.json')
OG_CAL = os.path.join(ROOT, 'model', 'pocket_og_cinemadng_camera_support.json')
SIGMA = os.path.join(ROOT, 'model', 'sigma_fp_cinemadng_camera_support.json')
OG_SIGNAL = os.path.join(ROOT, 'model', 'pocket_og_cinemadng_signal.json')


def _load(path):
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def _mode(doc):
    return doc['cameras'][0]['modes'][0]


def inventory(path):
    doc = _load(path)
    cam = doc['cameras'][0]
    mode = _mode(doc)
    table = mode['csc_params']['table']
    iso = mode.get('iso_params') or []
    gains = iso[0]['iso_gain_table'] if iso else []
    return {
        'file': os.path.basename(path),
        'name': doc.get('name'),
        'schema_version': doc.get('schema_version'),
        'manufacturer': cam.get('manufacturer'),
        'camera_model': cam.get('camera_model'),
        'revision': cam.get('revision'),
        'mode_name': mode.get('name'),
        'chroma_alignment': mode.get('chroma_alignment'),
        'native_iso': mode.get('native_iso'),
        'iso_modification_supported': mode.get('iso_modification_supported'),
        'iso_table': [{'iso': g['iso'], 'pre_gain': g['pre_gain'], 'post_gain': g['post_gain']} for g in gains],
        'csc_keys': sorted(table.keys()),
        'has_r_gain': 'r_gain' in table,
        'has_b_gain': 'b_gain' in table,
        'has_d65_forward_matrix': 'd65_forward_matrix_by_temperature_and_tint' in table,
        'inv_sqrt_keys': sorted((mode.get('inv_sqrt') or {}).keys()),
        'blk_lvl_lin_16b': mode.get('blk_lvl_lin_16b'),
        'sat_lvl_lin_16b': mode.get('sat_lvl_lin_16b'),
        'full_well_capacity_e': mode.get('full_well_capacity_e'),
        'stops_above_mid_grey_at_native_iso': mode.get('stops_above_mid_grey_at_native_iso'),
        'channel_codec_field': None,
        'ycc_mix_field': None,
        'notes': (
            'El JSON de Camera Support no declara ChannelCodec ni el modelo de proporciones. '
            'El SDK reconstruye con inv_sqrt + CSC. ChannelCodec es interfaz de encode en signal.hpp.'
        ),
    }


def what_determines():
    return {
        'signal_model': 'model/*_cinemadng_signal.json (sensor_curve, code_from_ycc) + inv_sqrt del perfil (decode SDK)',
        'channel_codec': 'No está en el perfil. Solo SignalMap::channelCodec() en el encoder (VideoAssist).',
        'response_curve': 'perfil.modes[].inv_sqrt {A,B,X,Y,Z,y_knee,clampMax}; encode OG usa sensor_curve del JSON de señal',
        'channel_gains': 'VA: gain_rgb en video_assist_input_transform.json. OG: input_channel_scale + post_gain ISO',
        'chroma_reconstruction': 'SDK: code_from_ycc implícito + upsample opaco. Encoder VA: finishPlanes. Encoder OG: imagePlanes C0',
        'matrices': 'csc_params.table.d65_forward_matrix_by_temperature_and_tint',
        'white_balance': 'r_gain, b_gain × ejes temperature/tint; clip kelvin/tint en bmdf',
        'iso_exposure': 'iso_params.iso_gain_table pre_gain/post_gain; clip isoe/expo/agpf; analog_gain en moov',
        'identity_revision': 'cameras[].manufacturer + camera_model → camera_type; cameras[].revision → ctrn',
    }


def og_channelcodec_profile_feasibility():
    """¿Perfil OG legítimo con representación equivalente a ChannelCodec?"""
    return {
        'reuse_sigma_identity': False,
        'new_profile_required_for_ratios': False,
        'reason_no_new_profile_for_bitstream': (
            'finishPlanes ya escribe Cb/Cr como códigos absolutos. El perfil OG existente espera '
            'esos códigos + inv_sqrt. No hay flag de proporciones en Camera Support.'
        ),
        'encoder_codec_required': (
            'Hace falta un ChannelCodec *de curva OG* (sensor_curve.encode / signal), no el VideoAssist k0+s√x. '
            'NativeSensor no implementa channelCodec() (nullptr).'
        ),
        'cannot_reuse_sigma_fp': (
            'camera_type SIGMA fp + revisión fp haría que OpenClip cargue el perfil Sigma. '
            'Prohibido. La OG ya tiene identidad Blackmagic Pocket Cinema Camera / rev CISO.'
        ),
        'gaps': [
            'Implementar ChannelCodec sobre SensorCurve OG (encode/signal) — no hecho; no en esta fase.',
            'Fase CFA Pocket OG es GBRG (CFA [G,B,R,G]); bayerFields sitúa Cb en DR y Cr en UL (fase RGGB). Desalineación no medida.',
            'chroma_alignment del perfil OG es RGGB; relación con el mosaico GBRG no demostrada para la ruta Bayer.',
            'Y 3×3 de fotositos ≠ Y NativeSensor de RGB demosaicado: colorimetría SDK vs DNG sin medir.',
            'noiseResolution usa crop/2 si channelCodec()!=nullptr: cambiaría grano del decoder si se activa NR/síntesis.',
            'Ventaja de proporciones tras DCT/cuantización y upsample SDK: no medida (BUG-002).',
        ],
        'classification': 'potencialmente_viable_requiere_informacion',
        'installed_profiles_untouched': True,
        'defaults_untouched': True,
        'no_experimental_path_this_phase': True,
    }
