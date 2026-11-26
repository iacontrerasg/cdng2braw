"""EXP-005b: inyección de planos, hipótesis A, oráculo offline."""
import os
import tempfile
import unittest

import numpy as np

from tools.exp005b_io import read_brwp, write_brwp
from tools.exp005b_loop import solve_gn, svd_report
from tools.exp005b_oracle import characterize_upsamplers, run_oracle_suite, solve_o1
from tools.exp005b_report import classify
from tools.exp005b_sdk import (
    W, H, build_impulse_planes, impulse_sites, match_codec_y, signed_channel_stats, stripe_layout,
)
from tools.exp005b_signal import NativeSensor, analyze_hypothesis_a, nn_chroma
from tools.fidelity import pipeline as P
from tools.fidelity import images


class InjectPlanesEnvTests(unittest.TestCase):
    def _encode(self, experimental_env=None):
        return {'compression': 'quality', 'q': 2, 'ratio': None, 'rate_search': False,
                'noise_synthesis': False, 'nr_luma': 0, 'nr_chroma': 0, 'workers': 1,
                'experimental_env': experimental_env or {}}

    def test_catalogued(self):
        self.assertIn('CDNG_INJECT_PLANES', P.OUTPUT_ENV)

    def test_hash_identity_and_mutex_rgb(self):
        with tempfile.TemporaryDirectory() as d:
            y = np.zeros((4, 4), np.float32)
            cb = np.zeros((2, 2), np.float32)
            cr = np.zeros((2, 2), np.float32)
            path = os.path.join(d, 'p.brwp')
            write_brwp(path, y, cb, cr)
            _, effective, env, _ = P.encode_request(
                self._encode({'CDNG_INJECT_PLANES': path}), environ={})
            self.assertEqual(env['CDNG_INJECT_PLANES'], os.path.abspath(path))
            self.assertIn('#', effective['CDNG_INJECT_PLANES'])
            from tools.fidelity import identity as I
            self.assertEqual(effective['CDNG_INJECT_PLANES'].split('#')[-1], I.sha256_file(path))
            rgb = os.path.join(d, 'a.f32')
            images.write_f32(rgb, np.zeros((2, 2, 3), np.float32))
            with self.assertRaises(P.PipelineError):
                P.encode_request(self._encode({
                    'CDNG_INJECT_PLANES': path,
                    'CDNG_INJECT_RGB': rgb,
                }), environ={})


class BrwpTests(unittest.TestCase):
    def test_roundtrip(self):
        y = np.arange(16, dtype=np.float32).reshape(4, 4)
        cb = np.array([[-1.5, 2], [3.25, -4]], np.float32)
        cr = np.array([[9, 0], [-8, 1.125]], np.float32)
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 't.brwp')
            write_brwp(p, y, cb, cr)
            y2, cb2, cr2 = read_brwp(p)
            np.testing.assert_array_equal(y, y2)
            np.testing.assert_array_equal(cb, cb2)
            np.testing.assert_array_equal(cr, cr2)


class HypothesisATests(unittest.TestCase):
    def test_equal_rgb_is_codec_achromatic(self):
        h = analyze_hypothesis_a()
        self.assertLess(h['camera_equal_code_chroma_max'], 1e-4)
        self.assertTrue(h['hypothesis']['camera_equal_is_codec_achromatic'])

    def test_p3_grey_matches_asn_linear(self):
        h = analyze_hypothesis_a()
        self.assertLess(h['asn_matches_p3_inverse_chromaticity_max'], 2e-3)
        self.assertTrue(h['hypothesis']['p3_achromatic_approx_asn'])
        self.assertFalse(h['hypothesis']['p3_achromatic_is_camera_asn_up_to_scale'])

    def test_p3_grey_chroma_varies_with_intensity(self):
        h = analyze_hypothesis_a()
        self.assertGreater(h['p3_grey_code_chroma_range'], 1.0)
        self.assertTrue(h['hypothesis']['p3_grey_has_intensity_dependent_codec_chroma'])

    def test_native_map_roundtrip_codes(self):
        s = NativeSensor()
        rgb = np.array([0.12, 0.18, 0.09])
        ycc = s.map_rgb(rgb)
        back = s.rgb_from_ycc(ycc)
        self.assertLess(np.max(np.abs(back - rgb)), 1e-9)


class OracleTests(unittest.TestCase):
    def test_nn_is_separable_block(self):
        c = characterize_upsamplers()
        self.assertTrue(c['s7_nn']['linear_separable'])
        self.assertEqual(c['s7_nn']['cross_channel_max'], 0.0)
        self.assertTrue(c['bilinear_420']['linear_separable'])
        self.assertLess(c['bilinear_420']['amplitude_rel_err'], 1e-9)

    def test_o1_beats_c0_on_p3_grey_edge(self):
        o = run_oracle_suite(size=24)
        self.assertGreater(o['reduction_vs_c0']['o1_p99'], 0.2)
        self.assertTrue(o['o1_continuous']['chroma_legal'])
        self.assertTrue(o['o3']['chroma_legal'])
        self.assertTrue(o['not_sdk_optimum'])
        self.assertGreaterEqual(o['o3']['luma_corr'], 0.999)

    def test_o1_recovers_nn_chroma(self):
        cb = np.zeros((8, 8))
        cr = np.zeros((8, 8))
        cb[2:6, 2:6] = 10
        hat_cb, hat_cr = solve_o1(np.repeat(np.repeat(cb, 2, 0), 2, 1),
                                  np.repeat(np.repeat(cr, 2, 0), 2, 1), nn_chroma)
        self.assertLess(np.max(np.abs(hat_cb - cb)), 1e-6)
        self.assertLess(np.max(np.abs(hat_cr)), 1e-6)


class PackedFixturesTests(unittest.TestCase):
    def test_impulse_sites_separated_and_interior(self):
        sites, y_imp = impulse_sites()
        self.assertGreaterEqual(len(sites), 8)
        coords = [(s['cx'], s['cy']) for s in sites]
        self.assertEqual(len(coords), len(set(coords)))
        for s in sites:
            self.assertGreaterEqual(s['cx'], 16)
            self.assertLess(s['cx'], 960 - 16)
            self.assertGreaterEqual(s['cy'], 16)
            self.assertLess(s['cy'], 540 - 16)
            if s['phase'] == 'even':
                self.assertEqual(s['cx'] % 8, 0)
                self.assertEqual(s['cy'] % 4, 0)
            else:
                self.assertEqual(s['cx'] % 8, 4)
                self.assertEqual(s['cy'] % 4, 2)
        for i, a in enumerate(sites):
            for b in sites[i + 1:]:
                d = abs(a['cx'] - b['cx']) + abs(a['cy'] - b['cy'])
                self.assertGreaterEqual(d, 32, (a, b, d))
        y, cb, cr, _, _ = build_impulse_planes()
        self.assertEqual(y.shape, (H, W))
        self.assertEqual(cb.shape, (H // 2, W // 2))
        self.assertGreater(float(np.max(np.abs(cb)) + np.max(np.abs(cr))), 0)

    def test_stripes_fill_1080p(self):
        last = stripe_layout()[-1]
        self.assertEqual(last[1] + last[2], W)


class MethodH1Tests(unittest.TestCase):
    def test_signed_lobes_not_hypot(self):
        k = np.zeros((9, 9))
        k[4, 4] = 1.0
        k[4, 6] = -0.4
        s = signed_channel_stats(k)
        self.assertTrue(s['has_negative_lobes'])
        mag = np.hypot(k, np.zeros_like(k))
        self.assertGreaterEqual(mag.min(), 0.0)

    def test_match_codec_y_beats_one_shot_scale(self):
        s = NativeSensor()
        rgb = np.array([0.40, 0.10, 0.06], dtype=np.float64)
        y_tgt = 1200.0
        one = rgb * (y_tgt / float(s.map_rgb(rgb)[0]))
        y_one = float(s.map_rgb(one)[0])
        matched, info = match_codec_y(s, rgb, y_tgt)
        self.assertLess(abs(info['y_final'] - y_tgt), abs(y_one - y_tgt) + 1e-6)
        self.assertLess(abs(info['y_final'] - y_tgt), 1.0)

    def test_classify_does_not_mix_pairs(self):
        sdk = {
            'status': 'measured',
            'oracle_s8': {'o1_p99_reduction': 0.7, 'o1_integrated_reduction': 0.05},
            'closed_loop': {'best': {'kind': 'cbcr_gn',
                                     'red_plateau': {'p99': 0.1, 'integrated': 0.8}}},
        }
        d = classify({'reduction_vs_c0': {'o3_p99': 0}}, sdk)
        self.assertNotEqual(d['class'], 'A')
        self.assertNotEqual(d['class'], 'B')

    def test_gn_recovers_linear(self):
        rng = np.random.default_rng(0)
        J = rng.normal(size=(40, 6))
        x = rng.normal(size=6)
        r = J @ (-x)
        dx = solve_gn(J, r, 1e-8, (-10 * np.ones(6), 10 * np.ones(6)))
        self.assertLess(np.max(np.abs(dx - x)), 1e-4)
        sv = svd_report(J)
        self.assertGreater(sv['smax'], sv['smin'])


if __name__ == '__main__':
    unittest.main()
