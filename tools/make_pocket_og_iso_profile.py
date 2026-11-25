"""Deriva la revisión con tabla ISO del perfil de la Pocket Cinema Camera (2013) a partir de la calibrada.

La revisión calibrada (model/pocket_og_cinemadng_camera_support.json, 1128549959) no se toca: la siguen necesitando
los BRAW ya escritos con ella. La nueva solo cambia los campos de ISO; matrices, tablas de balance, curva del sensor
(inv_sqrt) y niveles se copian tal cual, y el script falla si cualquier otra cosa difiere.

  python3 tools/make_pocket_og_iso_profile.py           # escribe el perfil nuevo y actualiza el JSON de señal
  python3 tools/make_pocket_og_iso_profile.py --check   # solo comprueba que los ficheros del repositorio coinciden
"""
import argparse
import copy
import hashlib
import json
import math
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
MODEL = ROOT / 'model'
LEGACY = MODEL / 'pocket_og_cinemadng_camera_support.json'
LEGACY_SHA = '63a0358740bfc98a459769a68d5490345785d1cbba42aa7086c6fe192d62fb5c'
LEGACY_REVISION = 1128549959
PROFILE = MODEL / 'pocket_og_cinemadng_iso_camera_support.json'
SIGNAL = MODEL / 'pocket_og_cinemadng_signal.json'
REVISION = 0x4349534F   # «CISO»; distinta de la calibrada y de las revisiones de prueba de out/iso-exp
REFERENCE_ISO = 400      # BaselineExposure 1.1 = la exposición con la que se calibró baseline_exposure_gain
# Los DNG de la Pocket original no llevan ISO en EXIF; la cámara la expresa con BaselineExposure.
ISO_TABLE = [(200, 0.1), (400, 1.1), (800, 2.1), (1600, 3.1)]
TOLERANCE_EV = 0.01
ISO_FIELDS = {'iso_params', 'native_iso', 'iso_modification_supported'}


def dumps(doc):
    return json.dumps(doc, indent=2) + '\n'


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def derive(legacy, baseline_gain):
    profile = copy.deepcopy(legacy)
    profile['name'] = 'Blackmagic Pocket Cinema Camera (2013) CinemaDNG calibration, ISO table'
    profile['description'] = ('CDNG revision with ISO 200/400/800/1600 as decode-time digital gain over unity-gain '
                              'data. Coexists with the calibrated revision 1128549959; both share colour and signal.')
    camera = profile['cameras'][0]
    camera['revision'] = REVISION
    mode = camera['modes'][0]
    reference = 2.0 ** ISO_TABLE[[iso for iso, _ in ISO_TABLE].index(REFERENCE_ISO)][1]
    if abs(reference - baseline_gain) > 1e-6:
        raise SystemExit('baseline_exposure_gain no corresponde a la ISO de referencia')
    mode['iso_params'] = [{'base_iso': REFERENCE_ISO, 'iso_gain_table': [
        {'iso': iso, 'pre_gain': 1.0, 'post_gain': round(2.0 ** ev / reference, 9)} for iso, ev in ISO_TABLE]}]
    mode['native_iso'] = REFERENCE_ISO
    mode['iso_modification_supported'] = True
    return profile


def check_only_iso_changed(legacy, profile):
    a, b = copy.deepcopy(legacy), copy.deepcopy(profile)
    for doc in (a, b):
        doc.pop('name'); doc.pop('description')
        doc['cameras'][0].pop('revision')
        for field in ISO_FIELDS: doc['cameras'][0]['modes'][0].pop(field)
    if a != b:
        raise SystemExit('el perfil derivado difiere del calibrado fuera de los campos ISO')
    for entry in profile['cameras'][0]['modes'][0]['iso_params'][0]['iso_gain_table']:
        ev = dict(ISO_TABLE)[entry['iso']]
        if entry['pre_gain'] != 1.0 or not math.isclose(entry['post_gain'], 2.0 ** (ev - ISO_TABLE[1][1]), rel_tol=1e-6):
            raise SystemExit('tabla ISO incoherente')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    legacy_text = LEGACY.read_text()
    if sha(legacy_text) != LEGACY_SHA:
        raise SystemExit('la revisión calibrada cambió: debe conservarse byte a byte')
    legacy = json.loads(legacy_text)
    if legacy['cameras'][0]['revision'] != LEGACY_REVISION:
        raise SystemExit('revisión calibrada inesperada')
    signal_text = SIGNAL.read_text()
    signal = json.loads(signal_text)
    profile = derive(legacy, signal['baseline_exposure_gain'])
    check_only_iso_changed(legacy, profile)
    profile_text = dumps(profile)
    signal.pop('source_iso', None)
    signal['revision'] = REVISION
    signal['legacy_revisions'] = [LEGACY_REVISION]
    signal['reference_iso'] = REFERENCE_ISO
    signal['iso_table'] = [{'iso': iso, 'baseline_exposure': ev} for iso, ev in ISO_TABLE]
    signal['baseline_exposure_tolerance'] = TOLERANCE_EV
    signal['profile_filename'] = PROFILE.name
    signal['profile_sha256'] = sha(profile_text)
    new_signal_text = dumps(signal)
    if args.check:
        stale = [p.name for p, text in ((PROFILE, profile_text), (SIGNAL, new_signal_text))
                 if not p.exists() or p.read_text() != text]
        if stale:
            raise SystemExit('desactualizado: ' + ', '.join(stale))
        print('ok')
        return
    PROFILE.write_text(profile_text)
    SIGNAL.write_text(new_signal_text)
    print(f'{PROFILE.name} {sha(profile_text)}\n{SIGNAL.name} {sha(new_signal_text)}')


if __name__ == '__main__':
    sys.exit(main())
