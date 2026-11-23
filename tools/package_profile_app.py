#!/usr/bin/env python3
"""Package the Qt app for macOS; no Python installation needed to run the .app.

Build first with CDNG_BUILD_APP=ON. Writes a NEW destination and runs macdeployqt
on the copy, including the privileged helper's Qt dependencies. Does not install
profiles, open Resolve, or request administrator privileges.
"""
import argparse
import json
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import sys
from macos_bundle_deps import complete, audit, minimum_system_version


def run(command, **kwargs):
    return subprocess.run(list(map(str, command)), check=True, **kwargs)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--build-dir', type=Path, default=Path('build/qt-app'))
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--macdeployqt', default=shutil.which('macdeployqt'))
    ap.add_argument('--converter', type=Path, help='self-contained worker directory from stage_converter.py')
    args = ap.parse_args()
    if sys.platform != 'darwin': ap.error('macOS packager; Windows/Linux packaging is pending')
    if not args.macdeployqt: ap.error('macdeployqt was not found')
    source = args.build_dir.resolve() / 'app/cdng2braw.app'
    target = args.out.resolve() / 'cdng2braw.app'
    if not source.is_dir(): ap.error('build the Qt application first')
    qmake = Path(args.macdeployqt).resolve().with_name('qmake')
    if not qmake.is_file(): ap.error('matching qmake not found next to macdeployqt')
    def qt_value(key):
        return run([qmake, '-query', key], capture_output=True, text=True).stdout.strip()
    qt_version = qt_value('QT_VERSION')
    links = run(['/usr/bin/otool', '-L', source / 'Contents/MacOS/cdng2braw'], capture_output=True, text=True).stdout
    linked_major = re.search(r'QtCore\.framework/Versions/[^/]+/QtCore[^\n]*current version (\d+)\.', links)
    if not linked_major or linked_major[1] != qt_version.split('.')[0]:
        ap.error('macdeployqt version does not match the Qt application')
    if target.exists(): ap.error('destination already exists; choose a new output directory')
    args.out.resolve().mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target, symlinks=True)
    helper = target / 'Contents/MacOS/cdng-profile-helper'
    run([args.macdeployqt, target, f'-executable={helper}', '-always-overwrite'])
    transitive = complete(target, source, Path(qt_value('QT_INSTALL_LIBS')), Path(qt_value('QT_INSTALL_PLUGINS')))
    _, missing = audit(target)
    if missing: raise RuntimeError(f'incomplete bundle dependencies: {missing}')
    # Bundle-specific configuration avoids looking for Qt plugins in Homebrew.
    resources = target / 'Contents/Resources'; resources.mkdir(exist_ok=True)
    if args.converter:
        if not (args.converter / 'cdng-convert').is_file(): ap.error('converter worker not found')
        shutil.copytree(args.converter, resources / 'converter', symlinks=True)
        _, external = audit(target)
        if external: raise RuntimeError(f'converter has external dependencies: {external}')
    (resources / 'qt.conf').write_text('[Paths]\nPlugins = PlugIns\n')
    project = Path(__file__).resolve().parents[1]
    notice = project / 'app/THIRD_PARTY_NOTICES.md'
    shutil.copy2(notice, resources / notice.name)
    # Keep the framework licence files shipped with this local Qt installation.
    if qmake.is_file():
        prefix = Path(run([qmake, '-query', 'QT_INSTALL_PREFIX'], capture_output=True, text=True).stdout.strip())
        qt_licenses = prefix / 'share/qt/LICENSES'
        if qt_licenses.is_dir(): shutil.copytree(qt_licenses, resources / 'QtLicenses', dirs_exist_ok=True)
        else:
            licenses = list(prefix.glob('LICENSE*'))
            if licenses:
                destination = resources / 'QtLicenses'; destination.mkdir()
                for path in licenses:
                    if path.is_file(): shutil.copy2(path, destination / path.name)
    plist_path = target / 'Contents/Info.plist'
    plist = plistlib.loads(plist_path.read_bytes())
    minimum = minimum_system_version(target)
    plist['LSMinimumSystemVersion'] = minimum
    plist['CFBundleDevelopmentRegion'] = 'en'
    plist['CFBundleLocalizations'] = ['en', 'es']
    plist_path.write_bytes(plistlib.dumps(plist))
    run(['/usr/bin/codesign', '--force', '--deep', '--sign', '-', target])
    run(['/usr/bin/codesign', '--verify', '--deep', '--strict', target])
    executable = target / 'Contents/MacOS/cdng2braw'
    status = json.loads(run([executable, '--profile-status'], capture_output=True, text=True).stdout)
    result = dict(bundle=str(target), status=status, bundle_id=plist.get('CFBundleIdentifier'),
                  qt_version=qt_version, minimum_macos=minimum, extra_transitive_dependencies=transitive,
                  conversion_worker_included=bool(args.converter),
                  scope='Qt conversion app with profile manager; locally signed, not notarized' if args.converter else
                        'Qt app without conversion worker; profile management only')
    (args.out.resolve() / 'package-check.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__': main()
