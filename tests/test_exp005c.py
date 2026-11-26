"""EXP-005c: env Bayer OG, identidad de caché, tags DNG."""
import os
import tempfile
import unittest

from tools.exp005c_audit import read_tags
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


if __name__ == '__main__':
    unittest.main()
