"""EXP-004: métricas de halo, fixtures, env de instrumentación."""
import os
import tempfile
import unittest

import numpy as np

from tools.exp004_fixtures import build_set, mosaic_rggb, write_fixture
from tools.exp004_halo import (chroma_esf_mtf, esf_mtf, extract_profile,
                               freq_response_binomial_box, halo_extent_px, profile_metrics)
from tools.fidelity import images, pipeline as P, stage_io


class HaloMetricTests(unittest.TestCase):
    def test_neutral_edge_profile_extent(self):
        fx = build_set(32, 32)['edge_neutral_v']  # also constructs diagonal fixture
        y = fx.mean(axis=-1)
        cb = np.zeros_like(y)
        line = [0, 16, 31, 16]
        prof = extract_profile(y, line, 1)
        self.assertEqual(len(prof), 32)
        mag = np.abs(np.diff(prof, prepend=prof[0]))
        self.assertGreater(halo_extent_px(mag, mag.max() * 0.2), 0)

    def test_esf_mtf_ideal_step_dc_is_one(self):
        step = np.concatenate([np.zeros(32), np.ones(32)])
        r = esf_mtf(step)
        self.assertNotIn('absent', r)
        self.assertAlmostEqual(r['mtf'][0], 1.0, places=9)
        # LSF impulso: MTF plana ~1 (no se resta la media: eso anularía DC).
        self.assertGreater(min(r['mtf'][:8]), 0.99)

    def test_esf_mtf_does_not_zero_dc_by_mean_subtraction(self):
        step = np.concatenate([np.zeros(64), np.ones(64)])
        lsf = np.diff(step, prepend=step[0])
        spec_ok = np.abs(np.fft.rfft(lsf))
        spec_bad = np.abs(np.fft.rfft(lsf - lsf.mean()))
        self.assertGreater(spec_ok[0], 0.5)
        self.assertLess(spec_bad[0], spec_ok[0] * 0.1)

    def test_chroma_mtf_ignores_luma_step(self):
        img = np.zeros((32, 64, 3), np.float32)
        img[:, :] = 0.2
        img[:, 32:] = 0.6  # solo luma, RGB iguales
        r = chroma_esf_mtf(img, [0, 16, 63, 16], 1)
        self.assertEqual(r.get('absent'), 'no_chroma_step')
        img2 = img.copy()
        img2[:, 32:, 0] = 0.7
        img2[:, 32:, 1] = 0.1
        img2[:, 32:, 2] = 0.1
        r2 = chroma_esf_mtf(img2, [0, 16, 63, 16], 1)
        self.assertNotIn('absent', r2)
        self.assertAlmostEqual(r2['mtf'][0], 1.0, places=6)

    def test_binomial_box_dc_unity(self):
        for k in ('b3', 'b5', 'b7'):
            r = freq_response_binomial_box(k)
            self.assertAlmostEqual(r['dc_combined'], 1.0, places=9)

    def test_mosaic_rggb_keeps_sites(self):
        rgb = np.zeros((4, 4, 3), np.float32)
        rgb[..., 0] = 1
        m = mosaic_rggb(rgb)
        self.assertEqual(m[0, 0], 1.0)
        self.assertEqual(m[0, 1], 0.0)
        self.assertEqual(m[1, 1], 0.0)


class StageIoTests(unittest.TestCase):
    def test_roundtrip_sidecar(self):
        with tempfile.TemporaryDirectory() as d:
            rgb = np.linspace(0, 1, 12).reshape(2, 2, 3).astype(np.float32)
            path = os.path.join(d, 'x.f32')
            images.write_f32(path, rgb)
            raw = rgb.tobytes()
            binp = os.path.join(d, 'S2_rgb.bin')
            open(binp, 'wb').write(raw)
            js = os.path.join(d, 'S2_rgb.json')
            import json
            json.dump({
                'stage': 'S2', 'plane': 'rgb', 'path': 'S2_rgb.bin', 'dtype': 'float32',
                'width': 2, 'height': 2, 'channels': 3, 'layout': 'HxWx3_interleaved',
                'nbytes': len(raw), 'domain': 'linear', 'units': 'x', 'note': '',
            }, open(js, 'w'))
            arr, meta = stage_io.read_buffer(js)
            np.testing.assert_array_equal(arr, rgb)
            self.assertEqual(len(meta['sha256']), 64)

    def test_ycbcr_roundtrip_codes(self):
        rgb = np.array([[[1000., 2000., 1500.]]], np.float64)
        y, cb, cr = stage_io.codes_to_ycbcr(rgb)
        back = stage_io.ycbcr_to_codes(y, cb, cr)
        np.testing.assert_allclose(back, rgb, atol=1e-9)


class InjectEnvTests(unittest.TestCase):
    def _encode(self, experimental_env=None, diagnostic_env=None):
        enc = {'compression': 'quality', 'q': 4, 'ratio': None, 'rate_search': False,
               'noise_synthesis': False, 'nr_luma': 0, 'nr_chroma': 0, 'workers': 1,
               'experimental_env': experimental_env or {}}
        if diagnostic_env:
            enc['diagnostic_env'] = diagnostic_env
        return enc

    def test_inject_and_dump_env(self):
        self.assertIn('CDNG_INJECT_RGB', P.OUTPUT_ENV)
        self.assertIn('CDNG_INJECT_AT', P.OUTPUT_ENV)
        self.assertIn('CDNG_DUMP_STAGES', P.DIAGNOSTIC_ENV)
        self.assertTrue(P.conversion_diagnostics({'CDNG_DUMP_STAGES': '/tmp/x'}))
        with self.assertRaises(P.PipelineError):
            P.encode_request(self._encode(experimental_env={'CDNG_INJECT_AT': 'nope'}), environ={})
        with self.assertRaises(P.PipelineError):
            P.encode_request(self._encode(experimental_env={'CDNG_INJECT_AT': 'pre_map'}), environ={})
        with tempfile.TemporaryDirectory() as d:
            rgb = np.zeros((2, 2, 3), np.float32)
            inj = os.path.join(d, 'a.f32')
            images.write_f32(inj, rgb)
            _, effective, env, diag = P.encode_request(self._encode(
                experimental_env={'CDNG_INJECT_RGB': inj, 'CDNG_INJECT_AT': 'pre_map'},
                diagnostic_env={'CDNG_DUMP_STAGES': '/tmp/st'}), environ={})
            self.assertEqual(env['CDNG_INJECT_AT'], 'pre_map')
            self.assertEqual(env['CDNG_INJECT_RGB'], os.path.abspath(inj))
            self.assertIn('#', effective['CDNG_INJECT_RGB'])
            from tools.fidelity import identity as I
            self.assertEqual(effective['CDNG_INJECT_RGB'].split('#')[-1], I.sha256_file(inj))
            self.assertEqual(diag['CDNG_DUMP_STAGES'], '/tmp/st')
            self.assertNotIn('CDNG_DUMP_STAGES', env)
        with self.assertRaises(P.PipelineError):
            P.encode_request(self._encode(experimental_env={'CDNG_OG_CHROMA_MODE': 'c2'}), environ={})
        _, _, env2, _ = P.encode_request(self._encode(
            experimental_env={'CDNG_OG_CHROMA_MODE': 'c1'}), environ={})
        self.assertEqual(env2['CDNG_OG_CHROMA_MODE'], 'c1')
        self.assertIn('CDNG_OG_CHROMA_MODE', P.OUTPUT_ENV)


if __name__ == '__main__':
    unittest.main()
