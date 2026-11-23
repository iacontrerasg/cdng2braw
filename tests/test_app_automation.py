"""Pruebas de la app real manejada desde fuera con su modo de automatización (--automation).

Se saltan salvo que existan:
  CDNG_TEST_APP      ejecutable de la app (por defecto, el del árbol de build)
  CDNG_TEST_BACKEND  motor cdng-convert (por defecto, el de la app empaquetada)
  CDNG_TEST_INPUT    carpeta con una toma corta de la Sigma fp (2–8 DNG, con o sin WAV)
  CDNG_TEST_ONSCREEN=1  con la ventana visible (necesario con la app empaquetada, que solo lleva la plataforma
                     cocoa; la del árbol de build usa el Qt de Homebrew y corre sin pantalla)
La app usa una carpeta de perfiles temporal.
"""
import json
import os
import socket
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from app_control import App, AutomationError

APP = Path(os.environ.get('CDNG_TEST_APP', ROOT / 'build/qt-app/app/cdng2braw.app/Contents/MacOS/cdng2braw'))
BACKEND = Path(os.environ.get('CDNG_TEST_BACKEND', ROOT / 'out/cdng2braw/cdng2braw.app/Contents/Resources/converter/cdng-convert'))
INPUT = os.environ.get('CDNG_TEST_INPUT')
OFFSCREEN = os.environ.get('CDNG_TEST_ONSCREEN') != '1'


@unittest.skipUnless(INPUT and APP.is_file() and BACKEND.is_file(), 'CDNG_TEST_INPUT, la app y el motor son necesarios')
class AppAutomationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = App.launch(APP, backend=BACKEND, profile_root=Path(self.temp.name) / 'perfiles', offscreen=OFFSCREEN)
        self.app.subscribe()

    def tearDown(self):
        self.app.close()
        self.temp.cleanup()

    def test_rejects_wrong_token_and_unknown_commands(self):
        raw = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        raw.connect(self.app.socket_path)
        raw.sendall(json.dumps({'id': 1, 'token': 'no', 'cmd': 'hello'}).encode() + b'\n')
        with raw, raw.makefile() as stream:
            reply = json.loads(stream.readline())
        self.assertFalse(reply['ok']); self.assertEqual(reply['error'], 'Invalid token')
        with self.assertRaises(AutomationError): self.app.call('nonexistent')
        with self.assertRaises(AutomationError): self.app.click('noSuchWidget')

    def test_modal_message_box_is_reported_and_dismissed(self):
        # Sin toma cargada, el comparador avisa con un QMessageBox modal.
        self.app.click('compareNoise')
        self.app.wait({'type': 'QMessageBox'}, timeout=10, exists=True)
        box = next(d for d in self.app.dialogs() if d['type'] == 'QMessageBox')
        self.assertTrue(box['buttons'])
        self.app.click(box['buttons'][0]['path'])
        self.app.wait({'type': 'QMessageBox'}, timeout=10, exists=False)

    def test_full_conversion_through_the_interface(self):
        app = self.app
        app.type('inputFolder', INPUT, enter=True)
        preview = app.wait_event('preview_ready', 60)
        self.assertEqual(preview['route'], 'va')
        app.click('nrPreset2')
        self.assertEqual((app.get('nrLuma', 'value'), app.get('nrChroma', 'value')), (35, 60))
        app.click('compareNoise')
        app.wait({'type': 'NoiseDialog'}, timeout=10, exists=True)
        app.wait_event('noise_ready', 60)
        app.set('compareLuma', 'value', 45)
        app.wait_event('noise_ready', 60)
        shot = app.screenshot(target={'type': 'NoiseDialog'})
        self.assertGreater(len(shot['png']), 1000)
        app.click({'type': 'QPushButton', 'text': 'Aplicar'})
        app.wait('nrLuma', timeout=10, property='value', equals=45)
        self.assertTrue(app.get('nrPreset4', 'checked'))
        destination = Path(self.temp.name) / 'braw'
        app.type('outputFolder', str(destination))
        app.click('startConversion')
        finished = app.wait_event('conversion_finished', 600)
        self.assertTrue(finished['success'], finished['detail'])
        app.wait('conversionState', timeout=30, contains='Lote terminado: 1 convertidos')
        outputs = list(destination.glob('*.braw'))
        self.assertEqual(len(outputs), 1); self.assertGreater(outputs[0].stat().st_size, 0)
        # Repetir el lote salta la toma ya convertida: nunca se sobrescribe.
        app.click('startConversion')
        app.wait('conversionState', timeout=30, contains='1 saltados')
        self.assertEqual(list(destination.glob('*.braw')), outputs)

    def test_control_survives_language_switch(self):
        self.app.select('uiLanguage', data='en')
        self.app.wait('startConversion', timeout=10, property='text', contains='Convert ')
        self.assertEqual(self.app.hello()['language'], 'en')


if __name__ == '__main__':
    unittest.main()
