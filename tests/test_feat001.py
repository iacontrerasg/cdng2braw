"""FEAT-001: LUT Gen 1, viewing histórico y opción Match (E1)."""
import json
import os
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CUBE = os.path.join(ROOT, 'model', 'pocket_og_dng_film_from_dwg_intermediate.cube')
CONTAINER_HPP = os.path.join(ROOT, 'native', 'engine', 'container.hpp')
CONTAINER_CPP = os.path.join(ROOT, 'native', 'engine', 'container.cpp')
CONVERT_CPP = os.path.join(ROOT, 'native', 'engine', 'worker', 'convert.cpp')


def cube_header(path):
    out = {}
    with open(path, encoding='utf-8') as f:
        for line in f:
            s = line.strip()
            if not s or s.startswith('#'):
                continue
            if s[0].isdigit() or s.startswith('-'):
                break
            parts = s.split(None, 1)
            key = parts[0]
            val = parts[1].strip().strip('"') if len(parts) > 1 else ''
            out[key] = val
    return out


def cube_samples(path):
    rows = []
    with open(path, encoding='utf-8') as f:
        for line in f:
            s = line.strip()
            if not s or s.startswith('#') or s[0].isalpha():
                continue
            parts = s.split()
            if len(parts) != 3:
                continue
            rows.append(tuple(float(x) for x in parts))
    return rows


class LutHeaderTests(unittest.TestCase):
    def test_dwg_intermediate_to_film_gen1_cube(self):
        h = cube_header(CUBE)
        self.assertIn('DaVinci WG/Intermediate', h.get('TITLE', ''))
        self.assertIn('Blackmagic Design Film', h.get('TITLE', ''))
        self.assertEqual(h.get('LUT_3D_SIZE'), '33')
        self.assertEqual(h.get('DOMAIN_MIN'), '0 0 0')
        self.assertEqual(h.get('DOMAIN_MAX'), '1 1 1')

    def test_intermediate_domain_is_not_classified_as_sdr(self):
        """Dominio 0–1 de DaVinci Intermediate es log, no rec.709 SDR."""
        samples = cube_samples(CUBE)
        self.assertEqual(len(samples), 33 ** 3)
        black = samples[0]
        white = samples[-1]
        mins = [min(c[i] for c in samples) for i in range(3)]
        maxs = [max(c[i] for c in samples) for i in range(3)]
        self.assertTrue(all(v > 0.03 for v in black))
        self.assertTrue(all(v < 0.9 for v in white))
        self.assertTrue(all(v >= 0.0 for v in mins))
        self.assertTrue(all(v <= 1.0 for v in maxs))
        clipped = sum(1 for c in samples if any(abs(x - 0.85771) < 1e-4 for x in c))
        self.assertGreater(clipped, 0)


class TemplateLutAbsenceTests(unittest.TestCase):
    def test_orig_braw_has_no_post_3dlut_keys(self):
        path = os.path.join(ROOT, 'model', 'cam', 'orig.braw')
        with open(path, 'rb') as f:
            data = f.read()
        self.assertNotIn(b'post_3dlut_mode', data)
        self.assertNotIn(b'post_3dlut_embedded_data', data)


class HistoricalViewingTests(unittest.TestCase):
    def test_og_viewing_p3_linear_default(self):
        with open(CONTAINER_HPP, encoding='utf-8') as f:
            header = f.read()
        with open(CONTAINER_CPP, encoding='utf-8') as f:
            impl = f.read()
        self.assertIn('ogViewingGamut = "DCI-P3 D65"', header)
        self.assertIn('ogViewingGamma = "Linear"', header)
        self.assertIn('ogMatchViewingGamut = "DaVinci Wide Gamut"', header)
        self.assertIn('ogMatchViewingGamma = "DaVinci Intermediate"', header)
        self.assertIn('Film Gen 1', impl)
        self.assertIn('matchCinemaDngRendering', header)

    def test_match_flag_is_opt_in_in_request(self):
        with open(CONVERT_CPP, encoding='utf-8') as f:
            text = f.read()
        self.assertIn('matchCinemaDngRendering = false', text)
        self.assertIn('match_cinemadng_rendering', text)
        self.assertIn('only available for the original Pocket Cinema Camera', text)


class EncodeRequestMatchTests(unittest.TestCase):
    def test_pipeline_default_and_cache_identity(self):
        from tools.fidelity import pipeline as P, identity
        req0, _, _, _ = P.encode_request(
            {'compression': 'quality', 'q': 4, 'noise_synthesis': False, 'workers': 1}, environ={})
        self.assertFalse(req0['match_cinemadng_rendering'])
        req1, _, _, _ = P.encode_request(
            {'compression': 'quality', 'q': 4, 'noise_synthesis': False, 'workers': 1,
             'match_cinemadng_rendering': True}, environ={})
        self.assertTrue(req1['match_cinemadng_rendering'])
        with tempfile.TemporaryDirectory() as d:
            a = os.path.join(d, 'a.dng')
            with open(a, 'wb') as f:
                f.write(b'DNG-A')
            conv = {'cdng_convert_sha256': 'worker', 'model': {}}
            m0 = P.conversion_material(conv, d, [a], 0, req0, {})
            m1 = P.conversion_material(conv, d, [a], 0, req1, {})
            self.assertNotEqual(identity.sha256_json(m0), identity.sha256_json(m1))


if __name__ == '__main__':
    unittest.main()
