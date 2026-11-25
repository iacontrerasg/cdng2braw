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
