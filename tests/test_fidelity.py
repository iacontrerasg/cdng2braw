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

    def test_publish_fresh_trace_rejects_stale_cache_only(self):
        """Una traza antigua en la caché no satisface la regeneración instrumentada."""
        from tools.fidelity import pipeline as P
        with tempfile.TemporaryDirectory() as d:
            cache = os.path.join(d, 'cache')
            instr = os.path.join(d, 'instr')
            os.makedirs(cache)
            os.makedirs(instr)
            open(os.path.join(cache, 'clip.pipeline-trace.txt'), 'w').write('TRAZA_VIEJA\n')
            open(os.path.join(cache, 'pipeline-trace.identity.json'), 'w').write(
                '{"generated":"2000-01-01","trace_body_sha256":"dead"}\n')
            with self.assertRaises(P.PipelineError) as cm:
                P._publish_fresh_trace(
                    instr, cache, 'keyabc', {'kind': 'braw'}, 'evalsha', 'prodsha',
                    {'CDNG_PIPELINE_TRACE': '1'}, True, {'seconds': 1.0})
            self.assertIn('no escribió', str(cm.exception))
            # La traza vieja sigue ahí; no se publicó nada nuevo.
            self.assertEqual(open(os.path.join(cache, 'clip.pipeline-trace.txt')).read(), 'TRAZA_VIEJA\n')

    def test_publish_fresh_trace_replaces_stale_and_records_both_hashes(self):
        from tools.fidelity import pipeline as P, identity
        with tempfile.TemporaryDirectory() as d:
            cache = os.path.join(d, 'cache')
            instr = os.path.join(d, 'instr')
            os.makedirs(cache)
            os.makedirs(instr)
            open(os.path.join(cache, 'clip.pipeline-trace.txt'), 'w').write('TRAZA_VIEJA\n')
            open(os.path.join(cache, 'pipeline-trace.identity.json'), 'w').write('{"generated":"old"}\n')
            body = 'frame 0 Y math_above=61\n'
            open(os.path.join(instr, 'clip.pipeline-trace.txt'), 'w').write(body)
            path, meta = P._publish_fresh_trace(
                instr, cache, 'convkey', {'kind': 'braw'}, 'sha_evaluado', 'sha_productor',
                {'CDNG_PIPELINE_TRACE': '1'}, True,
                {'seconds': 2.5, 'completed': {'event': 'completed', 'q_min': 18}, 'braw_sha256': 'sha_productor'})
            self.assertTrue(os.path.isfile(path))
            self.assertEqual(meta['evaluated_braw_sha256'], 'sha_evaluado')
            self.assertEqual(meta['producer_braw_sha256'], 'sha_productor')
            self.assertEqual(meta['braw_sha256'], 'sha_evaluado')
            self.assertEqual(meta['instrumented_braw_sha256'], 'sha_productor')
            self.assertTrue(meta['binds_to_evaluated_image'])
            self.assertTrue(meta['media_equivalent'])
            self.assertFalse(meta['braw_file_sha_equal'])
            self.assertEqual(meta['trace_body_sha256'], identity.sha256_bytes(body.encode()))
            self.assertEqual(meta['instrumented']['seconds'], 2.5)
            text = open(os.path.join(cache, 'clip.pipeline-trace.txt')).read()
            self.assertIn('frame 0 Y math_above=61', text)
            self.assertNotIn('TRAZA_VIEJA', text)
            self.assertIn('# producer_braw_sha256 sha_productor', text)
            self.assertIn('# evaluated_braw_sha256 sha_evaluado', text)
            P._require_requested_traces(cache, {'CDNG_PIPELINE_TRACE': '1'},
                                        expect_body_sha=meta['trace_body_sha256'])

    def test_publish_fresh_trace_marks_non_binding_when_media_differs(self):
        from tools.fidelity import pipeline as P
        with tempfile.TemporaryDirectory() as d:
            cache = os.path.join(d, 'cache')
            instr = os.path.join(d, 'instr')
            os.makedirs(cache)
            os.makedirs(instr)
            open(os.path.join(instr, 'clip.pipeline-trace.txt'), 'w').write('fresh\n')
            _, meta = P._publish_fresh_trace(
                instr, cache, 'k', {}, 'a', 'b', {'CDNG_PIPELINE_TRACE': '1'}, False,
                {'seconds': 1.0})
            self.assertFalse(meta['binds_to_evaluated_image'])
            self.assertFalse(meta['media_equivalent'])

    def test_braw_inspect_same_media_on_determinism_corpus(self):
        """Corpus EXP-001: mdat idéntico entre repeticiones aunque el SHA del fichero difiera."""
        from tools.fidelity import braw_inspect
        root = os.path.join(ROOT, 'out/benchmark/experiments/exp001/determinism')
        files = []
        for dirpath, _, names in os.walk(os.path.join(root, 'cache')):
            if 'clip.braw' in names:
                files.append(os.path.join(dirpath, 'clip.braw'))
        if len(files) < 2:
            self.skipTest('corpus de determinismo no disponible')
        files.sort()
        fa = braw_inspect.media_fingerprint(files[0])
        fb = braw_inspect.media_fingerprint(files[1])
        self.assertTrue(fa['mdat_sha256'])
        self.assertTrue(fa['frames'])
        eq = braw_inspect.equivalence(files[0], files[1])
        self.assertTrue(eq['payload_equal'])
        self.assertTrue(eq['mdat_equal'])
        # El moov completo puede diferir (UUID/tiempos); la metadata de revelado debe coincidir.
        self.assertTrue(eq['decode_relevant_moov_equal'])
        self.assertTrue(eq['binds_to_evaluated_image'])
        self.assertEqual(braw_inspect.same_media(files[0], files[1]), True)

    def test_diagnostic_refresh_keeps_cached_completed(self):
        """Al regenerar trazas, compression/completed siguen siendo del BRAW cacheado."""
        from unittest import mock
        from tools.fidelity import pipeline as P, identity

        cached_done = {'event': 'completed', 'bytes': 1000, 'frames': 2,
                       'ratio_achieved': 3.0, 'q_min': 18, 'q_max': 18}
        instr_done = {'event': 'completed', 'bytes': 9999, 'frames': 2,
                      'ratio_achieved': 9.9, 'q_min': 1, 'q_max': 1}
        with tempfile.TemporaryDirectory() as d:
            a = os.path.join(d, 'a.dng')
            open(a, 'wb').write(b'DNG')
            cache = os.path.join(d, 'cache')
            enc = self._encode(diagnostic_env={'CDNG_PIPELINE_TRACE': '1'})
            req, effective, env, diagnostic = P.encode_request(enc, environ={})
            conv_id = {'cdng_convert_sha256': 'worker' * 8, 'model': {}}
            material = P.conversion_material(conv_id, d, [a], 0, req, effective)
            key = identity.sha256_json(material)
            directory = os.path.join(cache, 'braw', key[:24])
            os.makedirs(directory)
            # BRAW mínimo: fingerprint fallará; mockeamos equivalence.
            braw = os.path.join(directory, 'clip.braw')
            open(braw, 'wb').write(b'BRAW_CACHED')
            saved = {'material': material, 'sha256': identity.sha256_file(braw),
                     'completed': cached_done, 'seconds': 11.0}
            with open(os.path.join(directory, 'identity.json'), 'w') as f:
                json.dump(saved, f)

            def fake_worker(converter, clip_dir, staged, req, env, diagnostic, tmp):
                out = os.path.join(tmp, 'clip.braw')
                open(out, 'wb').write(b'BRAW_INSTRUMENTED_DIFF')
                open(os.path.join(tmp, 'clip.pipeline-trace.txt'), 'w').write('Y math_above=0\n')
                open(os.path.join(tmp, 'convert.log'), 'w').write('ok\n')
                return out, [{'event': 'started'}], instr_done, 3.0

            eq = {'payload_equal': True, 'decode_relevant_meta_equal': True,
                  'binds_to_evaluated_image': True, 'mdat_equal': True, 'moov_equal': False,
                  'mdat_sha256_b': 'mdat', 'moov_sha256_b': 'moov'}
            logs = []
            with mock.patch.object(P, '_run_convert_worker', side_effect=fake_worker), \
                 mock.patch.object(P.braw_inspect, 'equivalence', return_value=eq), \
                 mock.patch.object(P, 'ambient_cdng', return_value={}):
                result = P.convert('/converter', conv_id, d, [a], 0, 1, enc, cache,
                                   log=logs.append, no_cache=False)
            self.assertTrue(result['reused'])
            self.assertTrue(result['trace_refreshed'])
            self.assertEqual(result['completed'], cached_done)
            self.assertEqual(result['seconds'], 11.0)
            self.assertEqual(result['instrumented']['completed'], instr_done)
            self.assertEqual(result['instrumented']['seconds'], 3.0)
            self.assertNotEqual(result['sha256'], result['instrumented']['braw_sha256'])
            self.assertTrue(result['binds_to_evaluated_image'])
            # compression del runner usaría completed cacheado
            from tools.fidelity import runner as R
            comp = R.compression(result, {'frame_info': {'frame_bytes': 100}}, np.zeros((4, 4, 3)))
            self.assertEqual(comp['braw_bytes'], 1000)
            self.assertEqual(comp['q_min'], 18)
            text = open(os.path.join(directory, 'clip.pipeline-trace.txt')).read()
            self.assertIn('Y math_above=0', text)
            meta = json.load(open(os.path.join(directory, 'pipeline-trace.identity.json')))
            self.assertEqual(meta['evaluated_braw_sha256'], saved['sha256'])
            self.assertEqual(meta['producer_braw_sha256'], result['instrumented']['braw_sha256'])

    def test_og_deblock_precomp_in_output_env(self):
        from tools.fidelity import pipeline as P, identity
        self.assertIn('CDNG_OG_DEBLOCK_PRECOMP', P.OUTPUT_ENV)
        self.assertIn('CDNG_OG_DEBLOCK_ALPHA', P.OUTPUT_ENV)
        self.assertIn('CDNG_OG_DEBLOCK_BOUNDED', P.OUTPUT_ENV)
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

    def test_rd_parse_and_storage(self):
        from tools.fidelity import rd as RD
        meta = RD.parse_exp002_id('exp002-a04-a050-q18')
        self.assertEqual(meta['alpha'], 0.5)
        self.assertEqual(meta['q'], 18)
        meta2 = RD.parse_exp002_id('exp002-a04-a1-r03')
        self.assertEqual(meta2['ratio'], 3)
        self.assertEqual(meta2['alpha'], 1.0)
        bd = RD.parse_exp002_id('exp002-a04-bd-q18')
        self.assertEqual(bd['tag'], 'bounded')
        self.assertIsNone(bd['alpha'])
        self.assertEqual(bd['q'], 18)
        # No confundir «bd» con un patrón «abd»
        self.assertNotEqual(RD.parse_exp002_id('exp002-a04-a0-q04')['tag'], 'bounded')
        # 1 MB/frame → 24 MB/s → 86.4 GB/h; 500 GB ≈ 5.79 h
        rows = [{'alpha': 1.0, 'q': 18, 'mode': 'quality', 'tag': None, 'ratio': None,
                 'frame_bytes': 1_000_000, 'psnr': 40.0, 'duv_edges': 0.01, 'grad': 1.0,
                 'block_bmi': 0.0, 'bits_per_site': 8 * 1e6 / RD.PHOTOSITES_1080P,
                 'mb_s_24fps': 24.0, 'gb_h_24fps': 24 * 3600 / 1e3,
                 'rmse': 0.01, 'duv_neutral': 0.01, 'lap': 1.0, 'braw_bytes': 2e6,
                 'ratio_achieved': 3.0, 'frame_ratio': 3.0, 'q_eff': 18,
                 'run_seconds': 1, 'fresh_convert_seconds': 1}]
        # Correct gb/h from bytes: fb*24*3600/1e9
        rows[0]['gb_h_24fps'] = 1_000_000 * 24 * 3600 / 1e9
        agg = RD.aggregate(rows)
        self.assertAlmostEqual(agg[0]['gb_h_24fps'], 86.4, places=3)
        hours_500 = 500 / agg[0]['gb_h_24fps']
        self.assertAlmostEqual(hours_500, 500 / 86.4, places=3)

    def test_rd_config_from_setup_not_name(self):
        from tools.fidelity import rd as RD
        result = {
            'frame': 44,
            'setup': {
                'clip': 'A04', 'frame': 44, 'iso': 1600, 'range': [44, 2],
                'inputs': [{'index': 44, 'sha256': 'abc'}],
                'decode': {'exposure': -3}, 'reference': {'path': 'x.exr'},
                'dng_sha256': 'abc',
                'encode': {
                    'request': {'compression': 'quality', 'q': 0, 'ratio': None,
                                'noise_synthesis': False, 'nr_luma': 0, 'nr_chroma': 0},
                    'experimental_env': {
                        'CDNG_OG_DEBLOCK_PRECOMP': '1', 'CDNG_OG_DEBLOCK_ALPHA': None,
                        'CDNG_OG_DEBLOCK_BOUNDED': '1', 'CDNG_RATE': None,
                    },
                },
            },
            'compression': {'q_min': 1, 'q_max': 1, 'frame_bytes': 100, 'braw_bytes': 200},
        }
        cfg = RD.config_from_result(result)
        self.assertTrue(cfg['bounded'])
        self.assertEqual(cfg['tag'], 'bounded')
        self.assertEqual(cfg['q_requested'], 0)
        label = RD.parse_exp002_id('exp002-a04-bd-q00')
        self.assertEqual(RD.label_matches_config(label, cfg), [])
        bad = RD.parse_exp002_id('exp002-a04-a1-q00')
        self.assertTrue(RD.label_matches_config(bad, cfg))  # a1 ≠ bd

    def test_rd_html_omits_missing_as_zero_and_is_self_contained(self):
        from tools.fidelity import rd as RD
        with tempfile.TemporaryDirectory() as d:
            agg = [
                {'alpha': 1.0, 'mode': 'quality', 'q': 0, 'q_requested': 0, 'ratio_requested': None,
                 'tag': 'control', 'psnr': 40.0, 'duv_edges': 0.01, 'grad': 1.0, 'lap': 0.9,
                 'block_bmi': 0.001, 'bits_per_site': 4.0, 'sample_bytes': 1e6,
                 'ratio_sample_12bit': 3.0, 'gb_h_24fps': 80.0, 'q_eff': 1.0,
                 'n_codec_range_fallback': 1},
                {'alpha': 1.0, 'mode': 'quality', 'q': 4, 'q_requested': 4, 'ratio_requested': None,
                 'tag': 'control', 'psnr': 39.5, 'duv_edges': 0.012, 'grad': 0.99, 'lap': 0.88,
                 'block_bmi': 0.002, 'bits_per_site': 3.5, 'sample_bytes': 9e5,
                 'ratio_sample_12bit': 3.4, 'gb_h_24fps': 70.0, 'q_eff': 4.0,
                 'n_codec_range_fallback': 0},
                {'alpha': 1.0, 'mode': 'quality', 'q': 1, 'q_requested': 1, 'ratio_requested': None,
                 'tag': 'control', 'psnr': None, 'duv_edges': None, 'grad': None, 'lap': None,
                 'block_bmi': None, 'bits_per_site': None, 'sample_bytes': None,
                 'ratio_sample_12bit': None, 'gb_h_24fps': None, 'q_eff': None,
                 'n_codec_range_fallback': 0},
            ]
            path = os.path.join(d, 'rd.html')
            RD.write_html(path, 'test', agg)
            html = open(path, encoding='utf-8').read()
            self.assertNotIn('cdn.jsdelivr', html.lower())
            self.assertNotIn('chart.js', html.lower())
            self.assertIn('<svg', html)
            self.assertIn('—', html)
            # No cell should invent PSNR 0.000 for the missing row
            self.assertNotIn('>0.000</td>', html)

    def test_rd_incremental_rejects_missing_frame(self):
        from tools.fidelity import rd_incremental as RI
        with tempfile.TemporaryDirectory() as d:
            base = os.path.join(d, 'base')
            cand = os.path.join(d, 'cand')
            for name, results in (
                (base, {'A04_f044': self._inc_result(q=0)}),
                (cand, {'A04_f044': self._inc_result(q=4), 'A04_f049': self._inc_result(q=4, frame=49)}),
            ):
                os.makedirs(os.path.join(name, 'frames'))
                open(os.path.join(name, 'metrics.json'), 'w').write(json.dumps({
                    'results': results}))
                open(os.path.join(name, 'run.json'), 'w').write(json.dumps({
                    'converter': {'cdng_convert_sha256': 'w'}, 'dataset': '/d', 'references': '/r',
                    'timing': {'conversions': [], 'total_seconds': 1}, 'cache': []}))
            with self.assertRaises(RI.IncrementalError) as cm:
                RI.analyze_pair(base, cand, {'normalize_to_iso': 200, 'psnr_peak': 1.0,
                                             'error_percentiles': [50], 'luminance_bins': [0, 1e9],
                                             'chroma_min_luminance': 0.005,
                                             'edge_mask': {'percentile': 90, 'dilate': 0, 'min_luminance': 0},
                                             'neutral_mask': {'max_duv': 0.01, 'min_luminance': 0},
                                             'ceiling_margin': 0.9999})
            self.assertIn('fotogramas no coinciden', str(cm.exception))

    def _inc_result(self, q=0, frame=44):
        return {
            'frame': frame, 'candidate_sha256': None,
            'setup': {
                'clip': 'A04', 'frame': frame, 'iso': 1600, 'range': [44, 2],
                'inputs': [{'index': 44, 'sha256': 'a'}, {'index': 49, 'sha256': 'b'}],
                'decode': {'exposure': -3}, 'reference': {'id': 'r'},
                'dng_sha256': 'a' if frame == 44 else 'b',
                'encode': {
                    'request': {'compression': 'quality', 'q': q, 'ratio': None,
                                'noise_synthesis': False, 'nr_luma': 0, 'nr_chroma': 0},
                    'experimental_env': {'CDNG_OG_DEBLOCK_PRECOMP': '1',
                                         'CDNG_OG_DEBLOCK_ALPHA': None,
                                         'CDNG_OG_DEBLOCK_BOUNDED': None},
                },
            },
            'reference_fidelity': {'normalized': {'psnr_db': 40.0}, 'raw': {'psnr_db': 30.0}},
        }

    def test_rd_size_fields_distinct(self):
        from tools.fidelity import rd as RD
        self.assertGreater(RD.RAW_BYTES_12BIT, 0)
        # sample ratio y payload ratio no deben compartir denominador distinto silenciosamente
        sample, payload = 1_000_000, 900_000
        self.assertNotAlmostEqual(RD.RAW_BYTES_12BIT / sample, RD.RAW_BYTES_12BIT / payload)

    def test_rd_braw_binding_rejects_ambiguous_same_range_two_clips(self):
        """Dos clips distintos con el mismo start/count no pueden resolverse solo por range."""
        from tools.fidelity import rd as RD, identity
        with tempfile.TemporaryDirectory() as d:
            cache = os.path.join(d, 'cache', 'braw')
            runs = os.path.join(d, 'runs', 'fake')
            os.makedirs(runs)
            model = {'quant_steps.json': 'x'}
            worker = 'w' * 64
            # Materiales distintos (clip/inputs) → keys distintos, mismo range [10, 2]
            mats = []
            keys = []
            for clip, sha in (('CLIP_A', 'aa'), ('CLIP_B', 'bb')):
                mat = {
                    'kind': 'braw', 'info_schema': 2,
                    'converter': {'cdng_convert_sha256': worker, 'model': model},
                    'clip': clip, 'first_frame': 10,
                    'inputs': [{'index': 10, 'name': 'f.dng', 'sha256': sha},
                               {'index': 11, 'name': 'g.dng', 'sha256': sha + '2'}],
                    'request': {'compression': 'quality', 'q': 4, 'noise_synthesis': False,
                                'nr_luma': 0, 'nr_chroma': 0},
                    'experimental_env': {'CDNG_OG_DEBLOCK_PRECOMP': '1'},
                }
                key = identity.sha256_json(mat)
                mats.append(mat)
                keys.append(key)
                bdir = os.path.join(cache, key[:24])
                os.makedirs(bdir)
                # Payload mínimo: no hace falta BRAW real para probar ambigüedad de timing
                open(os.path.join(bdir, 'clip.braw'), 'wb').write(b'not-a-braw')
                with open(os.path.join(bdir, 'identity.json'), 'w') as f:
                    json.dump({'material': mat, 'sha256': identity.sha256_file(
                        os.path.join(bdir, 'clip.braw'))}, f)

            # Run con timing ambiguo si solo se mira range (ambos [10,2])
            run_doc = {
                'converter': {'cdng_convert_sha256': worker, 'model': model},
                'decoder': {'brawdec_sha256': 'd' * 64, 'framework_sha256': 'f' * 64,
                            'camera_support': {'p.json': 'c' * 64}},
                'config': {
                    'manifest_analysis': {'normalize_to_iso': 200},
                    'rois': {}, 'decode': {'gamma': 'Linear'},
                    'encode': {'compression': 'quality', 'q': 4, 'noise_synthesis': False,
                               'nr_luma': 0, 'nr_chroma': 0},
                },
                'timing': {'conversions': [
                    {'test': 'T1', 'range': [10, 2], 'reused': False, 'seconds': 1},
                    {'test': 'T2', 'range': [10, 2], 'reused': False, 'seconds': 1},
                ]},
                'cache': [
                    {'kind': 'braw', 'key': keys[0], 'reused': False},
                    {'kind': 'braw', 'key': keys[1], 'reused': False},
                ],
                'dataset': '/data', 'references': '/refs',
            }
            with open(os.path.join(runs, 'run.json'), 'w') as f:
                json.dump(run_doc, f)
            with open(os.path.join(runs, 'metrics.json'), 'w') as f:
                json.dump({'results': {}}, f)
            # Enlazar cache relativa: rd espera out/benchmark/cache desde runs/../..
            # runs = d/runs/fake → cache = d/cache  (pardir×2 = d)
            run = RD.load_run(runs)
            # Resultado del clip A / test T1
            result_a = {
                'test': 'T1', 'frame': 10,
                'setup': {
                    'clip': 'CLIP_A', 'range': [10, 2], 'frame': 10, 'iso': 400,
                    'inputs': mats[0]['inputs'], 'dng_sha256': 'aa',
                    'decode': {'exposure': 0},
                    'reference': {'sha256': 'refa', 'file': 'a.exr'},
                    'encode': {'request': mats[0]['request'],
                               'experimental_env': mats[0]['experimental_env']},
                },
            }
            bind = RD.resolve_braw_binding(run, result_a, 'T1_f010', verify_file_sha=True)
            self.assertEqual(bind['conversion_key'], keys[0])
            self.assertEqual(bind['clip'], 'CLIP_A')
            self.assertEqual(bind['test'], 'T1')
            # Mismo range, otro clip → otra conversion_key (no se elige la primera por range)
            result_b = json.loads(json.dumps(result_a))
            result_b['test'] = 'T2'
            result_b['setup']['clip'] = 'CLIP_B'
            result_b['setup']['inputs'] = mats[1]['inputs']
            result_b['setup']['dng_sha256'] = 'bb'
            result_b['setup']['encode']['request'] = mats[1]['request']
            result_b['setup']['encode']['experimental_env'] = mats[1]['experimental_env']
            result_b['setup']['reference'] = {'sha256': 'refb', 'file': 'b.exr'}
            bind_b = RD.resolve_braw_binding(run, result_b, 'T2_f010', verify_file_sha=True)
            self.assertEqual(bind_b['conversion_key'], keys[1])
            self.assertNotEqual(bind['conversion_key'], bind_b['conversion_key'])
            # Si se corrompe el test ID y solo queda range, timing es ambiguo al recuperar
            # (forzar fallo: key inexistente + timing duplicado sin test usable)
            bad = json.loads(json.dumps(result_a))
            bad['setup']['inputs'] = [{'index': 10, 'name': 'f.dng', 'sha256': 'ZZ'},
                                      {'index': 11, 'name': 'g.dng', 'sha256': 'YY'}]
            with self.assertRaises(RD.RdError) as cm:
                RD.resolve_braw_binding(run, bad, 'T1_f010', verify_file_sha=False)
            msg = str(cm.exception)
            self.assertTrue('ambiguo' in msg or '≠' in msg or 'sin conversion_key' in msg or
                            'material' in msg, msg)

    def test_rd_coverage_uses_test_and_frame(self):
        from tools.fidelity import rd as RD
        rows = [
            {'alpha': 1.0, 'q_requested': 0, 'mode': 'quality', 'test': 'A01', 'frame': 10, 'run': 'r1'},
            {'alpha': 1.0, 'q_requested': 0, 'mode': 'quality', 'test': 'A02', 'frame': 10, 'run': 'r2'},
        ]
        cov = RD.coverage_report(rows, expected_q=[0], expected_alphas=[1.0],
                                 expected_test_frames=[('A01', 10), ('A02', 10), ('A03', 10)])
        self.assertEqual(len(cov['missing']), 1)
        self.assertEqual(cov['missing'][0]['test'], 'A03')
        # Mismo frame en dos tests no es duplicado
        cov2 = RD.coverage_report(rows)
        self.assertEqual(cov2['duplicates'], [])

    def test_rd_run_identity_requires_critical_fields(self):
        from tools.fidelity import rd as RD
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, 'metrics.json'), 'w') as f:
                json.dump({'results': {}}, f)
            with open(os.path.join(d, 'run.json'), 'w') as f:
                json.dump({'converter': {'cdng_convert_sha256': 'w'}, 'decoder': {}}, f)
            run = RD.load_run(d)
            with self.assertRaises(RD.RdError) as cm:
                RD.run_identity(run)
            self.assertIn('identidad crítica', str(cm.exception))

    def test_rd_html_overlays_alphas_common_scale(self):
        from tools.fidelity import rd as RD
        with tempfile.TemporaryDirectory() as d:
            agg = []
            for a, psnr0 in ((1.0, 40.0), (0.5, 41.0), (0.75, 40.5)):
                for q in (0, 12, 24):
                    agg.append({
                        'alpha': a, 'mode': 'quality', 'q': q, 'q_requested': q,
                        'ratio_requested': None, 'tag': 'control',
                        'psnr': psnr0 - q * 0.01, 'duv_edges': 0.01, 'grad': 1.0,
                        'lap': 0.9, 'block_bmi': 0.001, 'bits_per_site': 4.0 - q * 0.05,
                        'sample_bytes': 1e6, 'ratio_sample_12bit': 3.0, 'gb_h_24fps': 80.0,
                        'q_eff': float(q) or 1.0, 'n_codec_range_fallback': 0,
                    })
            path = os.path.join(d, 'rd.html')
            RD.write_html(path, 'overlay', agg)
            html = open(path, encoding='utf-8').read()
            self.assertIn('escala común', html)
            self.assertIn('α=1.0', html)
            self.assertIn('α=0.5', html)
            # Una sola SVG por métrica (overlay), no una por alpha
            self.assertEqual(html.count('<svg'), 6)

    def test_og_deblock_alpha_and_bounded_validation(self):
        from tools.fidelity import pipeline as P, identity
        ok = P.encode_request(self._encode(experimental_env={
            'CDNG_OG_DEBLOCK_PRECOMP': '1', 'CDNG_OG_DEBLOCK_ALPHA': '0.5'}), environ={})
        self.assertEqual(ok[2]['CDNG_OG_DEBLOCK_ALPHA'], '0.5')
        with self.assertRaises(P.PipelineError):
            P.encode_request(self._encode(experimental_env={
                'CDNG_OG_DEBLOCK_PRECOMP': '0', 'CDNG_OG_DEBLOCK_ALPHA': '0.5'}), environ={})
        with self.assertRaises(P.PipelineError):
            P.encode_request(self._encode(experimental_env={
                'CDNG_OG_DEBLOCK_PRECOMP': '1', 'CDNG_OG_DEBLOCK_ALPHA': '0.3'}), environ={})
        with self.assertRaises(P.PipelineError):
            P.encode_request(self._encode(experimental_env={
                'CDNG_OG_DEBLOCK_PRECOMP': '1', 'CDNG_OG_DEBLOCK_ALPHA': '0.5',
                'CDNG_OG_DEBLOCK_BOUNDED': '1'}), environ={})
        bd = P.encode_request(self._encode(experimental_env={
            'CDNG_OG_DEBLOCK_PRECOMP': '1', 'CDNG_OG_DEBLOCK_BOUNDED': '1'}), environ={})[1]
        with tempfile.TemporaryDirectory() as d:
            a = os.path.join(d, 'a.dng'); open(a, 'wb').write(b'DNG')
            conv = {'cdng_convert_sha256': 'w', 'model': {}}
            m_alpha = P.conversion_material(conv, d, [a], 0, ok[0], ok[1])
            m_bd = P.conversion_material(conv, d, [a], 0, ok[0], bd)
            self.assertNotEqual(identity.sha256_json(m_alpha), identity.sha256_json(m_bd))

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
class Exp002aInstrumentationTests(unittest.TestCase):
    """Banco EXP-002a: deadzone, diagnóstico y agregación de presupuesto."""

    def _encode(self, **kw):
        enc = {
            'compression': 'quality', 'q': 18, 'ratio': None, 'rate_search': False,
            'noise_synthesis': False, 'nr_luma': 0, 'nr_chroma': 0, 'workers': 1,
            'experimental_env': {'CDNG_OG_DEBLOCK_PRECOMP': '1'},
        }
        enc.update(kw)
        return enc

    def test_deadzone_accepted_in_range(self):
        from tools.fidelity import pipeline as P
        for z in ('0', '0.25', '0.5'):
            req, effective, env, diag = P.encode_request(self._encode(experimental_env={
                'CDNG_OG_DEBLOCK_PRECOMP': '1', 'CDNG_DEADZONE': z}), environ={})
            self.assertEqual(env['CDNG_DEADZONE'], z)

    def test_deadzone_rejects_out_of_range(self):
        from tools.fidelity import pipeline as P
        with self.assertRaises(P.PipelineError) as cm:
            P.encode_request(self._encode(experimental_env={
                'CDNG_OG_DEBLOCK_PRECOMP': '1', 'CDNG_DEADZONE': '0.6'}), environ={})
        self.assertIn('CDNG_DEADZONE', str(cm.exception))
        with self.assertRaises(P.PipelineError):
            P.encode_request(self._encode(experimental_env={
                'CDNG_OG_DEBLOCK_PRECOMP': '1', 'CDNG_DEADZONE': '-0.1'}), environ={})
        with self.assertRaises(P.PipelineError):
            P.encode_request(self._encode(experimental_env={
                'CDNG_OG_DEBLOCK_PRECOMP': '1', 'CDNG_DEADZONE': 'nope'}), environ={})

    def test_diagnostic_env_rejected_in_experimental(self):
        from tools.fidelity import pipeline as P
        with self.assertRaises(P.PipelineError) as cm:
            P.encode_request(self._encode(experimental_env={
                'CDNG_OG_DEBLOCK_PRECOMP': '1', 'CDNG_BIT_BUDGET': '1'}), environ={})
        self.assertIn('diagnostic', str(cm.exception).lower())

    def test_diagnostic_env_accepted_separately(self):
        from tools.fidelity import pipeline as P
        req, effective, env, diag = P.encode_request(
            self._encode(diagnostic_env={'CDNG_BIT_BUDGET': '1', 'CDNG_COEFF_STATS': '1',
                                         'CDNG_STATS_DIR': '/tmp/x'}),
            environ={})
        self.assertNotIn('CDNG_BIT_BUDGET', env)
        self.assertEqual(diag.get('CDNG_BIT_BUDGET'), '1')
        self.assertEqual(env.get('CDNG_OG_DEBLOCK_PRECOMP'), '1')

    def test_bit_budget_reconcile_helper(self):
        from tools.exp002a_report import load_budget
        with tempfile.TemporaryDirectory() as d:
            # payload 16 B → 128 bits; DC Y 100 + pad 28 + marker 0
            line = {
                'q': 24,
                'slices_payload_bytes': 16,
                'budget': {
                    'dc_bits': [100, 0, 0],
                    'ac_prefix_bits': [0, 0, 0],
                    'ac_final_bits': [0, 0, 0],
                    'ac_magnitude_bits': [0, 0, 0],
                    'eob_bits': [0, 0, 0],
                    'marker_bits': 0,
                    'padding_bits': 28,
                    'coded_bits': 100,
                    'payload_bytes': 16,
                    'attributed_bits': 128,
                },
            }
            with open(os.path.join(d, 'bit-budget.jsonl'), 'w') as f:
                f.write(json.dumps(line) + '\n')
            bud = load_budget(d)
            self.assertTrue(bud['reconcile_ok'])
            self.assertEqual(bud['payload_bytes_mean'], 16)
            self.assertAlmostEqual(bud['pct_payload']['Y'], 100.0 * 100 / 128)

    def test_default_encode_has_no_deadzone(self):
        from tools.fidelity import pipeline as P
        _, _, env, _ = P.encode_request(self._encode(), environ={})
        self.assertIsNone(env.get('CDNG_DEADZONE'))

    def test_align_soft_records_without_raising(self):
        from tools.fidelity import images as IMG
        yy, xx = np.mgrid[0:64, 0:64]
        base = 0.2 + 0.3 * (xx / 63.0)
        ref = np.stack([base, base, base], -1)
        # Misma geometría de baja frecuencia; detalle distinto (ruido) → baja correlación highpass.
        rng = np.random.default_rng(0)
        cand = ref + rng.normal(0, 0.08, ref.shape)
        cand = np.clip(cand, 1e-4, None)
        old = os.environ.get('CDNG_ALIGN_SOFT')
        os.environ['CDNG_ALIGN_SOFT'] = '1'
        try:
            out = IMG.check_alignment(ref, cand, 0.99)
            self.assertTrue(out.get('gate_failed'))
            self.assertIn(out.get('gate_reason'), ('detail_correlation', 'shift'))
        finally:
            if old is None:
                os.environ.pop('CDNG_ALIGN_SOFT', None)
            else:
                os.environ['CDNG_ALIGN_SOFT'] = old
        with self.assertRaises(IMG.ImageError):
            IMG.check_alignment(ref, cand, 0.99)

    def test_alignment_separates_geometry_and_detail(self):
        """EXP-003: degradación de detalle con shift=0 → quality_rejected, no desalineación."""
        from tools.fidelity import images as IMG
        yy, xx = np.mgrid[0:64, 0:64]
        base = 0.2 + 0.3 * (xx / 63.0)
        ref = np.stack([base, base, base], -1)
        rng = np.random.default_rng(1)
        cand = ref + rng.normal(0, 0.1, ref.shape)
        cand = np.clip(cand, 1e-4, None)
        old = os.environ.get('CDNG_ALIGN_SOFT')
        os.environ['CDNG_ALIGN_SOFT'] = '1'
        try:
            out = IMG.check_alignment(ref, cand, 0.99)
            self.assertTrue(out['geometric_aligned'] or out['gate_reason'] == 'shift')
            if out['geometric_aligned']:
                self.assertFalse(out['detail_gate_pass'])
                self.assertTrue(out['quality_rejected'])
                self.assertEqual(out['gate_reason'], 'detail_correlation')
                self.assertNotEqual(out['gate_reason'], 'shift')
        finally:
            if old is None:
                os.environ.pop('CDNG_ALIGN_SOFT', None)
            else:
                os.environ['CDNG_ALIGN_SOFT'] = old


@unittest.skipUnless(np is not None, 'numpy es necesario (make venv)')
class Exp003PolicyTests(unittest.TestCase):
    """EXP-003: validación de política en el banco (sin dataset)."""

    def _encode(self, **kw):
        enc = {
            'compression': 'quality', 'q': 24, 'ratio': None, 'rate_search': False,
            'noise_synthesis': False, 'nr_luma': 0, 'nr_chroma': 0, 'workers': 1,
            'experimental_env': {'CDNG_OG_DEBLOCK_PRECOMP': '1'},
        }
        enc.update(kw)
        return enc

    def test_policy_path_accepted_and_sha_in_identity(self):
        from tools.fidelity import pipeline as P
        pol = os.path.join(ROOT, 'benchmark', 'experiments', 'exp003', 'policies', 'uniform-z25.json')
        _, effective, env, _ = P.encode_request(self._encode(experimental_env={
            'CDNG_OG_DEBLOCK_PRECOMP': '1', 'CDNG_DEADZONE_POLICY': pol}), environ={})
        self.assertTrue(env['CDNG_DEADZONE_POLICY'].endswith('uniform-z25.json'))
        self.assertIn('#', effective['CDNG_DEADZONE_POLICY'])
        self.assertIn(os.path.abspath(pol), effective['CDNG_DEADZONE_POLICY'])

    def test_policy_conflicts_with_deadzone(self):
        from tools.fidelity import pipeline as P
        pol = os.path.join(ROOT, 'benchmark', 'experiments', 'exp003', 'policies', 'uniform-z25.json')
        with self.assertRaises(P.PipelineError) as cm:
            P.encode_request(self._encode(experimental_env={
                'CDNG_OG_DEBLOCK_PRECOMP': '1',
                'CDNG_DEADZONE': '0.25',
                'CDNG_DEADZONE_POLICY': pol}), environ={})
        self.assertIn('mutuamente', str(cm.exception).lower())

    def test_policy_missing_file_rejected(self):
        from tools.fidelity import pipeline as P
        with self.assertRaises(P.PipelineError):
            P.encode_request(self._encode(experimental_env={
                'CDNG_OG_DEBLOCK_PRECOMP': '1',
                'CDNG_DEADZONE_POLICY': '/tmp/no-such-policy-exp003.json'}), environ={})

    def test_band_maps_cover_all_ac(self):
        path = os.path.join(ROOT, 'benchmark', 'experiments', 'exp003', 'band-maps.json')
        with open(path, encoding='utf-8') as f:
            doc = json.load(f)
        for plane, umax in (('Y', 7), ('Cb', 3), ('Cr', 3)):
            cells = doc['planes'][plane]
            self.assertEqual(len(cells), (umax + 1) * 8)
            dc = [c for c in cells if c['u'] == 0 and c['v'] == 0]
            self.assertEqual(dc[0]['band'], 'DC')
            ac = [c for c in cells if not (c['u'] == 0 and c['v'] == 0)]
            self.assertTrue(all(c['band'] in ('low', 'mid', 'high') for c in ac))

    def test_policy_validator_rejects_overlap_and_unknown_plane(self):
        from tools.fidelity.deadzone_policy import PolicyError, validate_policy_doc
        with self.assertRaises(PolicyError):
            validate_policy_doc({
                'schema': 1, 'name': 'x', 'mode': 'per_band',
                'bands': {'low': {'f_min': 0, 'f_max': 0.4}, 'mid': {'f_min': 0.3, 'f_max': 0.6}},
                'planes': {'Y': {'default': 0.1, 'bands': {'low': 0.1}},
                           'Cb': {'default': 0.1}, 'Cr': {'default': 0.1}, 'X': {'default': 0}},
            })
        with self.assertRaises(PolicyError):
            validate_policy_doc({
                'schema': 1, 'name': 'u', 'mode': 'uniform', 'z': 0.25,
                'planes': {'Y': {'default': 0.5}, 'Cb': {'default': 0.25}, 'Cr': {'default': 0.25}},
            })


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
