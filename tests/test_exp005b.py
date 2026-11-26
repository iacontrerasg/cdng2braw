"""EXP-005b: inyección de planos, hipótesis A, oráculo offline."""
import os
import tempfile
import unittest

import numpy as np

from tools.exp005b_io import read_brwp, write_brwp
from tools.exp005b_oracle import characterize_upsamplers, run_oracle_suite, solve_o1
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


if __name__ == '__main__':
    unittest.main()
