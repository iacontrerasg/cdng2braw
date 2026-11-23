"""Close and audit Mach-O dependencies after macdeployqt, including @rpath dylibs.

Some Qt 5 deployment tools miss transitive @rpath dependencies (e.g. Brotli).
Source discovery is restricted to the original binaries' dependency graph and
the selected Qt installation. No downloads or system-library modifications.
"""
import os
from pathlib import Path
import re
import shutil
import subprocess


def command(args):
    return subprocess.run(list(map(str, args)), check=True, capture_output=True, text=True).stdout


def macho(path):
    if not path.is_file() or path.is_symlink(): return False
    with path.open('rb') as stream: magic = stream.read(4)
    return magic in (b'\xcf\xfa\xed\xfe', b'\xfe\xed\xfa\xcf', b'\xca\xfe\xba\xbe',
                     b'\xbe\xba\xfe\xca', b'\xca\xfe\xba\xbf', b'\xbf\xba\xfe\xca')


def dependencies(path):
    lines = command(['/usr/bin/otool', '-L', path]).splitlines()[1:]
    ids = {line.strip() for line in command(['/usr/bin/otool', '-D', path]).splitlines()[1:]}
    return [v for line in lines if line[:1].isspace() and (v := line.strip().split(' (')[0]) not in ids]


def system(dependency):
    return dependency.startswith(('/usr/lib/', '/System/Library/'))


def expand(path, binary, executable):
    path = path.replace('@loader_path', str(binary.parent)).replace('@executable_path', str(executable.parent))
    return Path(path)


def resolve(dependency, binary, executable, extra=()):
    if dependency.startswith('@rpath/'):
        info = command(['/usr/bin/otool', '-l', binary])
        paths = re.findall(r'cmd LC_RPATH\s+cmdsize \d+\s+path (.*?) \(offset', info)
        roots = [expand(p, binary, executable) for p in paths]
        roots.extend(extra)
        candidates = [p / dependency[len('@rpath/'):] for p in roots]
    else:
        candidates = [expand(dependency, binary, executable)]
    return next((p.resolve() for p in candidates if p.is_file()), None)


def audit(bundle):
    bundle = bundle.resolve(); executable = bundle / 'Contents/MacOS/cdng2braw'
    frameworks = bundle / 'Contents/Frameworks'
    graph = {}; problems = []
    for path in bundle.rglob('*'):
        if not macho(path): continue
        values = dependencies(path); graph[str(path.relative_to(bundle))] = values
        for value in values:
            if system(value): continue
            found = resolve(value, path, executable, [frameworks])
            if found is None or not found.is_relative_to(bundle):
                problems.append(dict(binary=str(path.relative_to(bundle)), dependency=value))
    return graph, problems


def minimum_system_version(bundle):
    versions = []
    for path in bundle.rglob('*'):
        if not macho(path): continue
        info = command(['/usr/bin/otool', '-l', path])
        versions.extend(re.findall(r'\bminos ([\d.]+)', info))
        versions.extend(re.findall(r'cmd LC_VERSION_MIN_MACOSX\s+cmdsize \d+\s+version ([\d.]+)', info))
    if not versions: raise RuntimeError('Cannot determine minimum macOS version')
    return max(versions, key=lambda value: tuple(map(int, value.split('.'))))


def complete(bundle, original_bundle, qt_libs, qt_plugins):
    bundle = bundle.resolve(); original_bundle = original_bundle.resolve()
    executable = original_bundle / 'Contents/MacOS/cdng2braw'
    frameworks = bundle / 'Contents/Frameworks'
    originals = {}; by_name = {}; pending = [executable, executable.with_name('cdng-profile-helper')]
    # macdeployqt chooses plugins beyond those linked by the executable.
    for binary in bundle.rglob('*'):
        if not macho(binary): continue
        relative = binary.relative_to(bundle / 'Contents')
        if relative.parts[0] == 'PlugIns': source = qt_plugins / Path(*relative.parts[1:])
        elif relative.parts[0] == 'Frameworks': source = qt_libs / Path(*relative.parts[1:])
        else: continue
        if source.is_file(): pending.append(source)
    while pending:
        binary = pending.pop().resolve()
        if str(binary) in originals: continue
        originals[str(binary)] = binary; by_name.setdefault(binary.name, binary)
        for dependency in dependencies(binary):
            if system(dependency): continue
            found = resolve(dependency, binary, executable, [binary.parent, qt_libs])
            if found is not None:
                # Preserve the ABI filename as well as its versioned symlink target.
                by_name.setdefault(Path(dependency).name, found)
                if str(found) not in originals: pending.append(found)
    copied = []
    for _ in range(32):
        _, problems = audit(bundle)
        if not problems: return copied
        for problem in problems:
            dependency = problem['dependency']; name = Path(dependency).name
            source = by_name.get(name)
            if source is None or not name.endswith('.dylib'):
                raise RuntimeError(f'Cannot resolve bundled dependency: {problem}')
            target = frameworks / name
            if not target.exists():
                shutil.copy2(source, target)
                command(['/usr/bin/install_name_tool', '-id', '@rpath/' + name, target])
                copied.append(dict(file=name, source=str(source)))
            parent = bundle / problem['binary']
            relative = os.path.relpath(target, parent.parent)
            command(['/usr/bin/install_name_tool', '-change', dependency, '@loader_path/' + relative, parent])
    raise RuntimeError('Dependency closure did not converge')
