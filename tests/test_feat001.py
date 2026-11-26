"""FEAT-001 A–D: cabecera de la LUT Gen 1 y viewing histórico. Sin encode."""
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CUBE = os.path.join(ROOT, 'model', 'pocket_og_dng_film_from_dwg_intermediate.cube')
CONTAINER = os.path.join(ROOT, 'native', 'engine', 'container.cpp')


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


class LutHeaderTests(unittest.TestCase):
    def test_dwg_intermediate_to_film_gen1_cube(self):
        h = cube_header(CUBE)
        self.assertIn('DaVinci WG/Intermediate', h.get('TITLE', ''))
        self.assertIn('Blackmagic Design Film', h.get('TITLE', ''))
        self.assertEqual(h.get('LUT_3D_SIZE'), '33')
        self.assertEqual(h.get('DOMAIN_MIN'), '0 0 0')
        self.assertEqual(h.get('DOMAIN_MAX'), '1 1 1')


class HistoricalViewingTests(unittest.TestCase):
    def test_og_viewing_p3_linear(self):
        with open(CONTAINER, encoding='utf-8') as f:
            text = f.read()
        self.assertIn('ogViewingGamut = "DCI-P3 D65"', text)
        self.assertIn('ogViewingGamma = "Linear"', text)
        self.assertIn('Film Gen 1', text)


if __name__ == '__main__':
    unittest.main()
