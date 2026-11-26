"""Validación de políticas CDNG_DEADZONE_POLICY (paridad con C++ deadzone_policy.cpp)."""
from __future__ import annotations

import json
import math
import os


class PolicyError(ValueError):
    pass


def _finite01(v, what):
    if not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v) or v < 0 or v > 0.5:
        raise PolicyError('%s out of range [0,0.5]: %r' % (what, v))
    return float(v)


def validate_policy_doc(doc):
    if not isinstance(doc, dict):
        raise PolicyError('root must be object')
    if doc.get('schema') != 1:
        raise PolicyError('schema must be 1')
    if not doc.get('name') or not doc.get('mode'):
        raise PolicyError('name and mode required')
    mode = doc['mode']
    allowed_root = {'schema', 'name', 'mode', 'planes'}
    if mode == 'uniform':
        allowed_root.add('z')
    if mode == 'per_band':
        allowed_root.add('bands')
    unknown = set(doc) - allowed_root
    if unknown:
        raise PolicyError('unexpected root keys: %s' % sorted(unknown))
    planes = doc.get('planes')
    if not isinstance(planes, dict):
        raise PolicyError('planes required')
    for k in planes:
        if k not in ('Y', 'Cb', 'Cr'):
            raise PolicyError('unknown plane %r' % k)
    for pl in ('Y', 'Cb', 'Cr'):
        if pl not in planes:
            raise PolicyError('missing plane %s' % pl)
        po = planes[pl]
        if not isinstance(po, dict):
            raise PolicyError('plane %s must be object' % pl)
        if 'default' not in po and mode != 'uniform':
            raise PolicyError('missing default for %s' % pl)
        if 'default' in po:
            _finite01(po['default'], 'default')
        allowed = {'default'}
        if mode == 'per_band':
            allowed.add('bands')
        if mode == 'per_coefficient':
            allowed.add('coeffs')
        bad = set(po) - allowed
        if mode in ('per_plane', 'uniform', 'legacy') and bad:
            raise PolicyError('unexpected keys in %s: %s' % (pl, sorted(bad)))
        if mode == 'per_band' and bad - {'bands'}:
            raise PolicyError('unexpected keys in %s: %s' % (pl, sorted(bad)))
        if mode == 'per_coefficient' and bad - {'coeffs'}:
            raise PolicyError('unexpected keys in %s: %s' % (pl, sorted(bad)))
        if mode == 'per_coefficient' and 'coeffs' in po:
            seen = set()
            umax = 7 if pl == 'Y' else 3
            for c in po['coeffs']:
                u, v, z = c.get('u'), c.get('v'), c.get('z')
                _finite01(z, 'coeff z')
                if u == 0 and v == 0:
                    raise PolicyError('DC cannot be set')
                if not (0 <= u <= umax and 0 <= v <= 7):
                    raise PolicyError('invalid coeff coordinate')
                idx = u * 8 + v
                if idx in seen:
                    raise PolicyError('duplicate coeff coordinate')
                seen.add(idx)
    if mode == 'uniform':
        if 'z' in doc:
            z = _finite01(doc['z'], 'z')
            for pl, po in planes.items():
                if 'default' in po and abs(float(po['default']) - z) > 1e-15:
                    raise PolicyError('uniform z conflicts with plane default')
        else:
            zs = [_finite01(planes[p]['default'], 'default') for p in ('Y', 'Cb', 'Cr')]
            if len(set(zs)) != 1:
                raise PolicyError('uniform requires equal plane defaults')
    if mode == 'per_band':
        bands = doc.get('bands')
        if not isinstance(bands, dict):
            raise PolicyError('bands required for per_band')
        edges = []
        for name, spec in bands.items():
            lo, hi = float(spec['f_min']), float(spec['f_max'])
            if not (lo < hi) or lo < 0 or hi > 1.01 + 1e-9:
                raise PolicyError('invalid band range %s' % name)
            edges.append((name, lo, hi))
        for i, (n1, lo1, hi1) in enumerate(edges):
            for n2, lo2, hi2 in edges[i + 1:]:
                if lo1 < hi2 and lo2 < hi1:
                    raise PolicyError('overlapping bands %s/%s' % (n1, n2))
    return True


def validate_policy_file(path):
    path = os.path.abspath(path)
    with open(path, encoding='utf-8') as f:
        doc = json.load(f)
    validate_policy_doc(doc)
    return doc
