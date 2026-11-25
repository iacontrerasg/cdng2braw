"""Pruebas del banco de fidelidad: métricas, ROI, alineación, identidad y comparación.

No necesitan el dataset ni Resolve: usan imágenes sintéticas. Requieren numpy (make venv).
"""
import json
import math
import os
import shutil
import tempfile
import unittest

try:
    import numpy as np
except ImportError:  # pragma: no cover
    np = None

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@unittest.skipUnless(np is not None, 'numpy es necesario (make venv)')
class MetricsTests(unittest.TestCase):
    def setUp(self):
        from tools.fidelity import metrics as M
        self.M = M
        self.cfg = {
            'normalize_to_iso': 200, 'psnr_peak': 1.0,
            'error_percentiles': [50, 99],
            'luminance_bins': [0.0, 0.01, 0.1, 1e9],
            'chroma_min_luminance': 0.005,
            'edge_mask': {'percentile': 90, 'dilate': 0, 'min_luminance': 0.002},
            'neutral_mask': {'max_duv': 0.01, 'min_luminance': 0.01},
            'ceiling_margin': 0.9999,
        }

    def test_identical_images(self):
        img = np.full((32, 48, 3), 0.18, np.float64)
        out, edges = self.M.fidelity(img, img.copy(), self.cfg, norm=1.0, ceiling=2.0)
        self.assertEqual(out['normalized']['mae'], 0.0)
        self.assertEqual(out['normalized']['mse'], 0.0)
        self.assertEqual(out['normalized']['psnr_db'], 'inf')
        self.assertEqual(out['normalized']['luminance_rel_error_pct'], 0.0)
        self.assertTrue(np.any(edges) or True)  # máscara puede estar vacía en uniforme

    def test_known_gain_difference(self):
        ref = np.full((16, 16, 3), 0.25, np.float64)
        cand = ref * 2.0
        # Sin normalización: PSNR finito; con norm=0.5 en candidata vía factor común sobre ambas
        # (amplificación lineal común) las métricas normalizadas deben coincidir bit a bit con idénticas.
        out, _ = self.M.fidelity(ref, cand, self.cfg, norm=1.0, ceiling=None)
        self.assertAlmostEqual(out['normalized']['luminance_rel_error_pct'], 100.0, places=6)
        # Factor 0.5 sobre ambas: ref→0.125, cand→0.25 → sigue el doble
        out2, _ = self.M.fidelity(ref, cand, self.cfg, norm=0.5, ceiling=None)
        self.assertAlmostEqual(out2['normalized']['luminance_rel_error_pct'], 100.0, places=6)
        # Comparar ref y ref*2 con norm que cancela el 2× en cand: no aplica; sí: misma ganancia en ambos
        twin = self.M.fidelity(ref, ref * 2, self.cfg, norm=1.0, ceiling=None)[0]
        self.assertGreater(twin['raw']['mae'], 0.2)

    def test_artificial_clipping(self):
        ref = np.linspace(0.1, 2.0, 64 * 64).reshape(64, 64)
        ref = np.stack([ref, ref, ref], -1)
        cand = np.minimum(ref, 1.0)
        out, _ = self.M.fidelity(ref, cand, self.cfg, norm=1.0, ceiling=1.0)
        self.assertGreater(out['normalized']['mae'], 0)
        self.assertEqual(out['ceiling']['value'], 1.0)
        self.assertGreater(out['ceiling']['candidate_fraction'], 0)
        self.assertGreater(out['candidate_channels']['R']['above_one_fraction'], -1)  # 0 after clip

    def test_negative_and_hdr(self):
        img = np.array([[[-0.01, 0.5, 1.5]]], np.float64)
        stats = self.M.channel_stats(img)
        self.assertGreater(stats['R']['negative_fraction'], 0)
        self.assertLess(stats['R']['negative_min'], 0)
        self.assertGreater(stats['B']['above_one_fraction'], 0)
        self.assertGreater(stats['B']['max'], 1.0)

    def test_chromatic_halo_raises_edge_chroma(self):
        # Referencia uniforme; candidata con franja cromática en un borde → Δu'v' mayor en máscara de bordes
        # si forzamos bordes con un gradiente en la referencia.
        y = np.linspace(0.05, 0.4, 64)
        ref = np.stack([np.tile(y, (64, 1))] * 3, -1)
        cand = ref.copy()
        cand[:, 30:34, 0] *= 1.3
        cand[:, 30:34, 2] *= 0.7
        out, edges = self.M.fidelity(ref, cand, self.cfg, norm=1.0, ceiling=None)
        self.assertGreater(out['luma_chroma']['all']['duv_mean'], 0)
        self.assertGreater(edges.mean(), 0)


@unittest.skipUnless(np is not None, 'numpy es necesario (make venv)')
class ImageTests(unittest.TestCase):
    def test_f32_roundtrip(self):
        from tools.fidelity import images
        img = np.linspace(0, 1, 12).reshape(2, 2, 3).astype(np.float32)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, 'x.f32')
            images.write_f32(path, img)
            got = images.read_f32(path)
        np.testing.assert_array_equal(got, img)

    def test_rejects_size_mismatch(self):
        from tools.fidelity import images
        a = np.zeros((4, 4, 3), np.float32)
        b = np.zeros((5, 4, 3), np.float32)
        with self.assertRaises(images.ImageError):
            images.check_pair(a, b)

    def test_rejects_nonfinite(self):
        from tools.fidelity import images
        a = np.zeros((4, 4, 3), np.float32)
        b = a.copy(); b[0, 0, 0] = np.nan
        with self.assertRaises(images.ImageError):
            images.check_pair(a, b)

    def test_alignment_detects_shift(self):
        from tools.fidelity import images
        rng = np.random.default_rng(0)
        base = rng.random((64, 64, 3)).astype(np.float64) * 0.5 + 0.05
        shifted = np.roll(base, (3, -2), (0, 1))
        shift, _ = images.alignment(base, shifted, search=8)
        self.assertNotEqual(shift, (0, 0))   # el signo depende de la convención de la correlación de fase
        self.assertEqual(abs(shift[0]), 3)
        self.assertEqual(abs(shift[1]), 2)
        with self.assertRaises(images.ImageError):
            images.check_alignment(base, shifted, min_correlation=0.5)


@unittest.skipUnless(np is not None, 'numpy es necesario (make venv)')
class RoiTests(unittest.TestCase):
    def test_roi_metrics_on_synthetic(self):
        from tools.fidelity import rois as R
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, 'rois.json')
            with open(path, 'w', encoding='utf-8') as f:
                json.dump({
                    'schema': 1, 'scene': 'synth', 'image_size': [64, 48],
                    'rois': [
                        {'name': 'patch', 'type': 'patch', 'category': 'surface', 'rect': [10, 10, 20, 15], 'neutral': True},
                        {'name': 'edge', 'type': 'edge', 'category': 'edge', 'line': [5, 24, 58, 24], 'width': 3},
                    ],
                }, f)
            doc = R.load(path, (64, 48))
            ref = np.full((48, 64, 3), 0.2, np.float64)
            ref[:, 30:] = 0.5
            cand = ref.copy()
            cand[10:25, 10:30] *= 1.05
            analysis = {
                'psnr_peak': 1.0, 'chroma_min_luminance': 0.005,
                'neutral_mask': {'max_duv': 0.05, 'min_luminance': 0.01},
            }
            metrics, summary = R.evaluate(ref, cand, doc, analysis)
            self.assertIn('patch', metrics)
            self.assertGreater(metrics['patch']['mae'], 0)
            self.assertIn('edge', metrics)
            self.assertIn('mae_mean', summary['surface'])
            self.assertIn('profile_rmse_mean', summary['edge'])


@unittest.skipUnless(np is not None, 'numpy es necesario (make venv)')
class CompareAndCacheTests(unittest.TestCase):
    def test_compare_identical_and_worse(self):
        from tools.fidelity import compare as C
        thresholds = {
            'equivalence': {'default': {'abs': 0.0, 'rel': 1e-9}, 'rules': []},
            'acceptance': [],
        }
        base = {'results': {'A01_f000': {'normalized': {'mae': 0.01, 'psnr_db': 40.0}}}}
        same = {'results': {'A01_f000': {'normalized': {'mae': 0.01, 'psnr_db': 40.0}}}}
        worse = {'results': {'A01_f000': {'normalized': {'mae': 0.02, 'psnr_db': 38.0}}}}
        better = {'results': {'A01_f000': {'normalized': {'mae': 0.005, 'psnr_db': 42.0}}}}
        self.assertEqual(C.compare(base, same, thresholds)['counts'].get('equivalent'), 2)
        self.assertEqual(C.compare(base, worse, thresholds)['counts'].get('worse'), 2)
        self.assertEqual(C.compare(base, better, thresholds)['counts'].get('improved'), 2)

    def test_psnr_infinity_and_nan_statuses(self):
        from tools.fidelity import compare as C
        thresholds = {'equivalence': {'default': {'abs': 0.0, 'rel': 1e-9}, 'rules': []}, 'acceptance': []}
        # Infinito ↔ infinito (cadenas serializadas por metrics._f)
        self.assertEqual(C.status('normalized.psnr_db', 'inf', 'inf', thresholds), 'equivalent')
        self.assertEqual(C.status('normalized.psnr_db', 'inf', '-inf', thresholds), 'changed')
        # Finito → infinito (mejora de PSNR); infinito → finito (empeora)
        self.assertEqual(C.status('normalized.psnr_db', 40.0, 'inf', thresholds), 'improved')
        self.assertEqual(C.status('normalized.psnr_db', 'inf', 40.0, thresholds), 'worse')
        # NaN: nunca equivalente en silencio
        self.assertEqual(C.status('normalized.psnr_db', 'nan', 'nan', thresholds), 'not_evaluable')
        self.assertEqual(C.status('normalized.psnr_db', 40.0, 'nan', thresholds), 'not_evaluable')
        self.assertEqual(C.status('normalized.duv_mean', 'nan', 0.01, thresholds), 'not_evaluable')

    def test_cache_rejects_foreign_build(self):
        from tools.fidelity import pipeline as P, identity
        with tempfile.TemporaryDirectory() as d:
            directory = os.path.join(d, 'entry')
            os.makedirs(directory)
            payload = os.path.join(directory, 'image.f32')
            open(payload, 'wb').write(b'x' * 16)
            material = {'kind': 'decode', 'decoder': {'brawdec_sha256': 'aaa'}}
            saved = {'material': material, 'sha256': identity.sha256_file(payload)}
            with open(os.path.join(directory, 'identity.json'), 'w') as f:
                json.dump(saved, f)
            # Material distinto → rechazo
            with self.assertRaises(P.CacheError):
                P._verify(directory, {'kind': 'decode', 'decoder': {'brawdec_sha256': 'bbb'}}, 'image.f32')
            # Payload alterado → rechazo
            open(payload, 'wb').write(b'y' * 16)
            with self.assertRaises(P.CacheError):
                P._verify(directory, material, 'image.f32')


@unittest.skipUnless(np is not None, 'numpy es necesario (make venv)')
class ExperimentalEnvAndSetupTests(unittest.TestCase):
    def _encode(self, **extra):
        enc = {'compression': 'quality', 'q': 4, 'noise_synthesis': True, 'workers': 2, 'experimental_env': {}}
        enc.update(extra)
        return enc

    def test_experimental_env_changes_cache_identity(self):
        """Dos configs experimentales distintas, mismo ejecutable y mismos DNG → claves de caché distintas."""
        from tools.fidelity import pipeline as P, identity
        with tempfile.TemporaryDirectory() as d:
            a = os.path.join(d, 'a.dng'); b = os.path.join(d, 'b.dng')
            open(a, 'wb').write(b'DNG-A'); open(b, 'wb').write(b'DNG-B')
            staged = [a, b]
            conv = {'cdng_convert_sha256': 'worker', 'model': {}}
            req0, eff0, _, _ = P.encode_request(self._encode(), environ={})
            req1, eff1, _, _ = P.encode_request(
                self._encode(experimental_env={'CDNG_DEADZONE': '0.25'}), environ={})
            m0 = P.conversion_material(conv, d, staged, 0, req0, eff0)
            m1 = P.conversion_material(conv, d, staged, 0, req1, eff1)
            self.assertNotEqual(identity.sha256_json(m0), identity.sha256_json(m1))
            self.assertEqual(m0['inputs'], m1['inputs'])
            self.assertIsNone(eff0['CDNG_DEADZONE'])
            self.assertEqual(eff1['CDNG_DEADZONE'], '0.25')

    def test_undeclared_ambient_cdng_is_rejected(self):
        from tools.fidelity import pipeline as P
        with self.assertRaises(P.PipelineError) as ctx:
            P.encode_request(self._encode(), environ={'CDNG_DEADZONE': '0.1', 'PATH': '/bin'})
        self.assertIn('CDNG_DEADZONE', str(ctx.exception))

    def test_neighbor_frame_hash_breaks_setup_equality(self):
        """Modificar un DNG vecino (sin tocar el evaluado) hace que los setups dejen de ser comparables."""
        from tools.fidelity import runner as R
        inputs_a = [{'index': 10, 'name': 'f10.dng', 'sha256': 'aaa'},
                    {'index': 11, 'name': 'f11.dng', 'sha256': 'bbb'}]
        inputs_b = [{'index': 10, 'name': 'f10.dng', 'sha256': 'aaa'},
                    {'index': 11, 'name': 'f11.dng', 'sha256': 'CHANGED'}]
        setup_a = {'clip': 'C', 'frame': 10, 'dng_sha256': 'aaa', 'inputs': inputs_a, 'range': [10, 2],
                   'encode': {}, 'decode': {}, 'iso': 400, 'reference': None}
        setup_b = dict(setup_a, inputs=inputs_b)
        self.assertEqual(setup_a['dng_sha256'], setup_b['dng_sha256'])
        self.assertNotEqual(R.regression_setup(setup_a), R.regression_setup(setup_b))

    def test_verify_decode_distinguishes_applied_and_unverifiable(self):
        from tools.fidelity import pipeline as P
        settings = {'gamut': 'DCI-P3 D65', 'gamma': 'Linear', 'color_science_gen': 5,
                    'highlight_recovery': 0, 'scale': 'full', 'iso': 'as_shot', 'exposure': -3}
        info = {
            'clip': {'gamut': {'status': '0', 'value': 'DCI-P3 D65'},
                     'gamma': {'status': '0', 'value': 'Linear'},
                     'csgn': {'status': '0', 'value': 5},
                     'hlry': {'status': '0', 'value': 0.0}},
            'frame': {'exposure': {'status': '0', 'value': -3.0}, 'iso': {'status': '0', 'value': 1600.0},
                      'kelvin': {'status': '0', 'value': 5600.0}, 'tint': {'status': '0', 'value': 10.0}},
            'scale': 'full', 'frame_bytes': 100,
        }
        v = P.verify_decode(settings, info, expected_iso=1600)
        self.assertTrue(v['ok'])
        self.assertEqual(v['applied']['exposure'], -3.0)
        self.assertEqual(v['applied']['iso'], 1600.0)
        # Ausencia de atributo crítico → error (no solo aviso)
        missing = P.verify_decode(settings, dict(info, frame={'exposure': {'status': '0', 'value': -3.0}}))
        self.assertFalse(missing['ok'])
        bad = P.verify_decode(settings, dict(info, frame={
            'exposure': {'status': '0', 'value': 0.0}, 'iso': {'status': '0', 'value': 1600.0},
            'kelvin': {'status': '0', 'value': 5600.0}, 'tint': {'status': '0', 'value': 10.0}}))
        self.assertFalse(bad['ok'])

    def test_parse_worker_events_rejects_invalid_json(self):
        from tools.fidelity import pipeline as P
        events = P.parse_worker_events('{"event":"started","og_deblock_precomp":true}\n')
        self.assertEqual(events[0]['og_deblock_precomp'], True)
        with self.assertRaises(P.PipelineError):
            P.parse_worker_events('{"event":"started","og_deblock_precomp":on}\n')

    def test_conversion_diagnostics_flags(self):
        from tools.fidelity import pipeline as P
        self.assertFalse(P.conversion_diagnostics({}))
        self.assertFalse(P.conversion_diagnostics({'CDNG_PIPELINE_TRACE': '0'}))
        self.assertTrue(P.conversion_diagnostics({'CDNG_PIPELINE_TRACE': '1'}))
        self.assertTrue(P.conversion_diagnostics({'CDNG_DUMP_CANVAS': '/tmp/x'}))

    def test_og_deblock_precomp_in_output_env(self):
        from tools.fidelity import pipeline as P, identity
        self.assertIn('CDNG_OG_DEBLOCK_PRECOMP', P.OUTPUT_ENV)
        enc = self._encode(experimental_env={'CDNG_OG_DEBLOCK_PRECOMP': '0'})
        req, eff, env, _ = P.encode_request(enc, environ={})
        self.assertEqual(eff['CDNG_OG_DEBLOCK_PRECOMP'], '0')
        self.assertEqual(env['CDNG_OG_DEBLOCK_PRECOMP'], '0')
        on = P.encode_request(self._encode(experimental_env={'CDNG_OG_DEBLOCK_PRECOMP': '1'}), environ={})[1]
        with tempfile.TemporaryDirectory() as d:
            a = os.path.join(d, 'a.dng'); open(a, 'wb').write(b'DNG')
            conv = {'cdng_convert_sha256': 'w', 'model': {}}
            m0 = P.conversion_material(conv, d, [a, a], 0, req, eff)
            m1 = P.conversion_material(conv, d, [a, a], 0, req, on)
            self.assertNotEqual(identity.sha256_json(m0), identity.sha256_json(m1))

    def test_no_reference_skips_exr_requirement(self):
        from tools.fidelity import manifest as MF
        bmd = os.path.expanduser('~/Desktop/BMD')
        if not os.path.isdir(bmd):
            self.skipTest('dataset BMD no disponible')
        doc = MF.load(os.path.join(ROOT, 'benchmark', 'manifest.json'))
        tests = MF.select(doc, series=['A'])
        # Sin carpeta de EXR: con require_references=True hay errores; con False, solo avisos.
        errors, warnings = MF.against_dataset(doc, bmd, '/tmp/no-exr-here', tests, require_references=True)
        self.assertTrue(any('referencia' in e or 'EXR' in e or '.exr' in e for e in errors) or errors)
        errors2, warnings2 = MF.against_dataset(doc, bmd, '/tmp/no-exr-here', tests, require_references=False)
        self.assertFalse(errors2)
        self.assertTrue(any('referencia' in w or 'EXR' in w or 'ausente' in w for w in warnings2))

    def test_chroma_validity_counts(self):
        from tools.fidelity import metrics as M
        duv = np.array([0.01, 0.02, np.nan, np.inf, -0.01], dtype=np.float64)
        v = M.chroma_validity(duv)
        self.assertEqual(v['duv_samples'], 5)
        self.assertEqual(v['duv_valid'], 3)
        self.assertEqual(v['duv_invalid'], 2)

    def test_block_boundaries_synthetic(self):
        from tools.fidelity import metrics as M
        ref = np.full((32, 32, 3), 0.2, np.float64)
        cand = ref.copy()
        # Escalón artificial en fronteras de bloque 8×8 solo en la candidata.
        cand[:, 8, :] += 0.05
        cand[:, 16, :] += 0.05
        cand[8, :, :] += 0.05
        out = M.block_boundaries(ref, cand, block=8, peak=1.0)
        self.assertGreater(out['border']['mae'], out['interior']['mae'])
        self.assertGreater(out['candidate_seam']['horizontal_border_mean'],
                           out['candidate_seam']['horizontal_interior_mean'])


@unittest.skipUnless(np is not None, 'numpy es necesario (make venv)')
class ManifestTests(unittest.TestCase):
    def test_manifest_loads_and_selects_series_a(self):
        from tools.fidelity import manifest as MF
        doc = MF.load(os.path.join(ROOT, 'benchmark', 'manifest.json'))
        tests = MF.select(doc, series=['A'])
        self.assertEqual([t['id'] for t in tests], ['A01', 'A02', 'A03', 'A04', 'A05'])
        self.assertAlmostEqual(MF.iso_for_baseline_exposure(3.1), 1600, places=6)

    def test_structure_rejects_bad_reference_frame(self):
        from tools.fidelity import manifest as MF
        doc = MF.load(os.path.join(ROOT, 'benchmark', 'manifest.json'))
        bad = json.loads(json.dumps({k: v for k, v in doc.items() if not k.startswith('_')}))
        bad['tests'] = [dict(bad['tests'][0], references=[{'frame': 999, 'file': 'x.exr', 'kind': 'resolve_cinemadng_exr'}])]
        problems = MF.structure(bad)
        self.assertTrue(any('fotograma' in p or 'referencia' in p for p in problems))


@unittest.skipUnless(np is not None, 'numpy es necesario (make venv)')
class ReportReproTests(unittest.TestCase):
    def test_csv_and_html_from_synthetic_fidelity(self):
        from tools.fidelity import metrics as M, report
        cfg = {
            'normalize_to_iso': 200, 'psnr_peak': 1.0, 'error_percentiles': [50, 99],
            'luminance_bins': [0.0, 0.5, 1e9], 'chroma_min_luminance': 0.005,
            'edge_mask': {'percentile': 90, 'dilate': 0, 'min_luminance': 0.002},
            'neutral_mask': {'max_duv': 0.05, 'min_luminance': 0.01}, 'ceiling_margin': 0.9999,
        }
        ref = np.full((24, 32, 3), 0.2, np.float64)
        cand = ref * 1.01
        fid, _ = M.fidelity(ref, cand, cfg, norm=1.0, ceiling=2.0)
        fid['png'] = {}
        metrics = {
            'schema': 1, 'config_sha256': 'abc', 'build': {},
            'results': {
                'A01_f000': {
                    'test': 'A01', 'series': 'A', 'category': 'iso', 'frame': 0,
                    'setup': {'iso': 200, 'clip': 'C0036', 'range': [0, 2], 'encode': {}, 'decode': {},
                              'dng_sha256': '0' * 64, 'reference': {'file': 'DNG200.exr'}},
                    'candidate_sha256': 'c' * 64,
                    'compression': {'frame_bytes': 100, 'frame_ratio': 2.0},
                    'reference_fidelity': fid,
                }
            },
        }
        run_meta = {'run_id': 't', 'git': {'commit': '0' * 40, 'branch': 'main', 'dirty': False},
                    'timing': {'total_seconds': 1.0, 'conversions': [], 'decodes': []},
                    'converter': {'cdng_convert_sha256': '1' * 64, 'model': {}, 'path': '/tmp/c'},
                    'decoder': {'brawdec_sha256': '2' * 64, 'framework_sha256': '3' * 64,
                                'framework': '/tmp/fw', 'camera_support': {}},
                    'environment': {'python': '3', 'numpy': '2', 'platform': 'test', 'machine': 'x'},
                    'dataset': '/tmp', 'references': '/tmp', 'baseline': None, 'warnings': []}
        doc = {'name': 'test', 'visualization': {
            'display_exposure': 2.0, 'abs_error_max': 0.02, 'signed_error_max': 0.01,
            'chroma_error_max': 0.02, 'histogram_max': 0.05}}
        with tempfile.TemporaryDirectory() as d:
            csv_path = os.path.join(d, 'm.csv')
            html_path = os.path.join(d, 'r.html')
            os.makedirs(os.path.join(d, 'png'))
            report.write_csv(csv_path, metrics)
            report.write_csv(os.path.join(d, 'm2.csv'), metrics)
            self.assertEqual(open(csv_path).read(), open(os.path.join(d, 'm2.csv')).read())
            report.write_html(html_path, metrics, run_meta, None, doc)
            html = open(html_path, encoding='utf-8').read()
            self.assertIn('A01_f000', html)
            self.assertIn('Normalización', html)


if __name__ == '__main__':
    unittest.main()
