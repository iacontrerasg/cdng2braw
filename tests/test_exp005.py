"""EXP-005: transformación S2↔P3, selector de debayer, métricas."""
import os
import tempfile
import unittest

import numpy as np

from tools.exp004_fixtures import mosaic_rggb
from tools.exp005_color import (apply_rgb, camera_to_p3, known_color_checks, p3_to_camera,
                                roundtrip_error)
from tools.exp005_metrics import aliasing_score, false_color, zippering
from tools.fidelity import images, pipeline as P


class ColorTransformTests(unittest.TestCase):
    def test_known_neutral_is_achromatic_p3(self):
        k = known_color_checks()
        self.assertTrue(k['neutral_achromatic'])
        self.assertLess(k['ramp_roundtrip']['max_abs'], 1e-9)
        self.assertLess(k['primaries_roundtrip_max'], 1e-9)

    def test_roundtrip_random_camera_rgb(self):
        cm1 = [1.2, -0.3, -0.1, -0.2, 1.1, 0.05, 0.02, -0.1, 0.9]
        cm2 = [0.9, -0.2, -0.08, -0.25, 1.15, 0.08, -0.04, 0.12, 0.7]
        n = [0.63, 1.0, 0.77]
        fwd = camera_to_p3(cm1, cm2, 2856, 6504, n)
        inv = p3_to_camera(cm1, cm2, 2856, 6504, n)
        rng = np.random.default_rng(0)
        rgb = rng.random((8, 8, 3)) * 0.8 + 0.05
        rt = roundtrip_error(rgb, fwd, inv)
        self.assertLess(rt['max_abs'], 1e-9)

    def test_srational_i32_wrap(self):
        u = 4294917617
        wrapped = (u & 0xffffffff)
        wrapped = wrapped - 0x100000000 if wrapped >= 0x80000000 else wrapped
        self.assertLess(wrapped, 0)

    def test_no_least_squares_fit_in_api(self):
        import inspect
        import tools.exp005_color as C
        src = inspect.getsource(C)
        self.assertNotIn('least_squares', src)
        self.assertNotIn('lstsq', src)


class DebayerEnvTests(unittest.TestCase):
    def _encode(self, experimental_env=None):
        return {'compression': 'quality', 'q': 2, 'ratio': None, 'rate_search': False,
                'noise_synthesis': False, 'nr_luma': 0, 'nr_chroma': 0, 'workers': 1,
                'experimental_env': experimental_env or {}}

    def test_catalogued(self):
        self.assertIn('CDNG_OG_DEBAYER', P.OUTPUT_ENV)

    def test_accepts_known(self):
        _, _, env, _ = P.encode_request(self._encode({'CDNG_OG_DEBAYER': 'vng'}), environ={})
        self.assertEqual(env['CDNG_OG_DEBAYER'], 'vng')

    def test_rejects_unknown_and_later_candidates(self):
        with self.assertRaises(P.PipelineError):
            P.encode_request(self._encode({'CDNG_OG_DEBAYER': 'nope'}), environ={})
        with self.assertRaises(P.PipelineError):
            P.encode_request(self._encode({'CDNG_OG_DEBAYER': 'amaze'}), environ={})


class MetricTests(unittest.TestCase):
    def test_zippering_flat(self):
        img = np.full((16, 16, 3), 0.2, np.float64)
        z = zippering(img)
        self.assertAlmostEqual(z['row_rg'], 0.0)
        self.assertAlmostEqual(z['col_rg'], 0.0)

    def test_false_color_none_on_identity(self):
        img = np.full((16, 16, 3), 0.2, np.float64)
        f = false_color(img, img)
        self.assertEqual(f['false_color_fraction'], 0.0)

    def test_mosaic_ground_truth_sites(self):
        rgb = np.zeros((4, 4, 3), np.float32)
        rgb[..., 0] = 0.5
        rgb[..., 1] = 0.2
        rgb[..., 2] = 0.1
        m = mosaic_rggb(rgb)
        self.assertAlmostEqual(m[0, 0], 0.5)
        self.assertAlmostEqual(m[0, 1], 0.2)
        self.assertAlmostEqual(m[1, 1], 0.1)

    def test_aliasing_defined(self):
        img = np.zeros((32, 32, 3))
        img[:, 0::2, 0] = 1
        r = aliasing_score(img)
        self.assertIn('nyquist_over_dc', r)


class InjectFixtureTests(unittest.TestCase):
    def test_write_f32_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            rgb = np.linspace(0, 1, 12).reshape(2, 2, 3).astype(np.float32)
            p = os.path.join(d, 'x.f32')
            images.write_f32(p, rgb)
            back = images.read_f32(p)
            np.testing.assert_allclose(back, rgb)


if __name__ == '__main__':
    unittest.main()
