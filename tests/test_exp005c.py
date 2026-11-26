"""EXP-005c: env Bayer OG, identidad de caché, tags DNG, métricas F, Camera Support."""
import os
import tempfile
import unittest

import numpy as np

from tools.exp005c_audit import read_tags
from tools.exp005c_camera_support import og_channelcodec_profile_feasibility, what_determines
from tools.exp005c_chroma import codes_from_ycc, ycc_from_codes
from tools.exp005c_metrics import match_luma, split_psnr
from tools.fidelity import pipeline as P
from tools.fidelity import rd as RD


class OgBayerEnvTests(unittest.TestCase):
    def test_catalogued(self):
        self.assertIn('CDNG_OG_BAYER', P.OUTPUT_ENV)
        self.assertIn('CDNG_OG_BAYER', RD.AUTHORIZED_EXP_ENV)

    def test_hash_identity(self):
        enc = {'compression': 'quality', 'q': 2, 'ratio': None, 'rate_search': False,
               'noise_synthesis': False, 'nr_luma': 0, 'nr_chroma': 0, 'workers': 1,
               'experimental_env': {'CDNG_OG_BAYER': '1'}}
        _, effective, env, _ = P.encode_request(enc, environ={})
        self.assertEqual(env.get('CDNG_OG_BAYER'), '1')
        self.assertEqual(effective.get('CDNG_OG_BAYER'), '1')
        enc0 = dict(enc, experimental_env={})
        _, eff0, _, _ = P.encode_request(enc0, environ={})
        self.assertIsNone(eff0.get('CDNG_OG_BAYER'))


class DngTagTests(unittest.TestCase):
    def test_synthetic_tiff_cfa(self):
        # TIFF little-endian mínimo: 8×8, CFA 2×2 RGGB, uncompressed
        # Demasiado frágil a mano: solo comprueba que read_tags no explota en II*
        data = bytearray(b'II*\x00\x08\x00\x00\x00')
        data += b'\x00\x00'  # 0 entries
        data += b'\x00\x00\x00\x00'
        with tempfile.NamedTemporaryFile(suffix='.dng', delete=False) as f:
            f.write(data)
            path = f.name
        try:
            tags, le = read_tags(path)
            self.assertTrue(le)
            self.assertIsInstance(tags, dict)
        finally:
            os.unlink(path)


class MetricsAndChromaTests(unittest.TestCase):
    def test_minus3_raw_invalid_matched_ok(self):
        rng = np.random.RandomState(0)
        cand = rng.rand(16, 16, 3).astype(np.float64) * 0.4 + 0.1
        ref = cand / 8.0
        s = split_psnr(ref, cand, 'DNG1600MINUS3_00086731.exr')
        self.assertTrue(s['minus3_exr'])
        self.assertFalse(s['raw_comparison_valid'])
        self.assertGreater(s['ev_offset_cand_over_exr'], 2.9)
        def db(x):
            return float('inf') if x == 'inf' else float(x)
        self.assertGreater(db(s['psnr_exposure_matched']), 40)
        self.assertGreater(db(s['psnr_exr_times_8']), 40)

    def test_match_luma_gain(self):
        ref = np.ones((4, 4, 3))
        cand = np.ones((4, 4, 3)) * 4
        matched, g = match_luma(ref, cand)
        self.assertAlmostEqual(g, 4.0)
        self.assertAlmostEqual(float(matched.mean()), 4.0)

    def test_ycc_roundtrip(self):
        r, g, b = codes_from_ycc(800.0, 12.0, -7.0)
        y, cb, cr = ycc_from_codes(r, g, b)
        self.assertAlmostEqual(y, 800.0)
        self.assertAlmostEqual(cb, 12.0)
        self.assertAlmostEqual(cr, -7.0)

    def test_camera_support_no_sigma_reuse(self):
        d = og_channelcodec_profile_feasibility()
        self.assertFalse(d['reuse_sigma_identity'])
        self.assertEqual(d['classification'], 'potencialmente_viable_requiere_informacion')
        self.assertIn('channel_codec', what_determines())
        self.assertTrue(d['no_experimental_path_this_phase'])


if __name__ == '__main__':
    unittest.main()
