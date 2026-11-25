"""Identidad reproducible de una ejecución: compilación, decodificador, perfiles instalados y entradas."""
import hashlib
import json
import os
import platform
import subprocess
import sys

_SHA_CACHE = {}


def sha256_file(path):
    path = os.path.realpath(path)
    st = os.stat(path)
    key = (path, st.st_size, st.st_mtime_ns)
    if key not in _SHA_CACHE:
        h = hashlib.sha256()
        with open(path, 'rb') as f:
            for block in iter(lambda: f.read(1 << 20), b''):
                h.update(block)
        _SHA_CACHE[key] = h.hexdigest()
    return _SHA_CACHE[key]


def sha256_json(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def tree(path, suffixes=None):
    """{ruta relativa: sha256} de los ficheros de un directorio (recursivo, orden estable)."""
    out = {}
    for root, dirs, files in os.walk(path):
        dirs.sort()
        for name in sorted(files):
            if name.startswith('.') or (suffixes and not name.endswith(suffixes)):
                continue
            full = os.path.join(root, name)
            out[os.path.relpath(full, path)] = sha256_file(full)
    return out


def git(repo):
    def run(*args):
        r = subprocess.run(['git', '-C', repo] + list(args), capture_output=True, text=True)
        return r.stdout.strip() if r.returncode == 0 else None
    status = run('status', '--porcelain', '--untracked-files=no')
    diff = run('diff', 'HEAD')
    return {'commit': run('rev-parse', 'HEAD'), 'branch': run('rev-parse', '--abbrev-ref', 'HEAD'),
            'dirty': bool(status), 'dirty_files': status.splitlines() if status else [],
            'diff_sha256': hashlib.sha256(diff.encode()).hexdigest() if diff else None}


def converter(directory):
    """Worker empaquetado (cdng-convert + model/): su hash es la identidad de la compilación candidata."""
    binary = os.path.join(directory, 'cdng-convert')
    if not os.path.isfile(binary):
        raise FileNotFoundError('no hay cdng-convert en %s (make worker)' % directory)
    model = os.path.join(directory, 'model')
    return {'path': os.path.abspath(directory), 'cdng_convert_sha256': sha256_file(binary),
            'model': tree(model) if os.path.isdir(model) else {}}


def decoder(brawdec, framework, camera_support):
    """brawdec, el BlackmagicRawAPI cargado y los perfiles de Camera Support con que el SDK revela."""
    real = os.path.realpath(framework)
    return {'brawdec_sha256': sha256_file(brawdec), 'framework': framework, 'framework_sha256': sha256_file(real),
            'camera_support_dir': camera_support,
            'camera_support': tree(camera_support, ('.json',)) if os.path.isdir(camera_support) else {}}


def environment():
    import numpy
    return {'python': sys.version.split()[0], 'numpy': numpy.__version__, 'platform': platform.platform(),
            'machine': platform.machine()}
