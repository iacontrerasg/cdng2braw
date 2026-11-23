#!/usr/bin/env python3
"""Empaqueta la app Qt para Windows x64: carpeta portable con todo lo que necesita para arrancar sin Qt
instalado, sin checkout y sin Python, y su ZIP.

Equivalente de package_profile_app.py (macOS). Diferencias del sistema:

- No hay bundle: el ejecutable, las DLL de Qt y los avisos de terceros van juntos en la raíz de la carpeta.
- windeployqt copia las DLL de Qt y sus plugins (plataforma, estilos, imágenes) y genera el qt.conf.
- El proceso de trabajo va en `converter/`, que es donde lo busca la app (app/conversion_job.cpp).
- No se firma: Windows mostrará el aviso de SmartScreen hasta que haya un certificado.

El gestor del perfil Camera Support queda como «no verificado» en Windows a propósito: no se ha comprobado
dónde lo busca Resolve en este sistema (app/profile_store.cpp, Paths::system). La app convierte por la ruta
Video Assist, que no necesita perfil instalado.
"""
import argparse
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(command, **kwargs):
    # encoding utf-8 explícito: el motor y la app escriben UTF-8, y en un Windows en español el códec por
    # omisión de Python es cp1252, que convierte los acentos en mojibake al leer su salida.
    if kwargs.get('capture_output'):
        kwargs.setdefault('encoding', 'utf-8')
    return subprocess.run(list(map(str, command)), check=True, **kwargs)


def qt_query(qmake, key):
    return run([qmake, '-query', key], capture_output=True).stdout.strip()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--build-dir', type=Path, default=Path('build/qt-app'))
    ap.add_argument('--out', type=Path, required=True, help='carpeta de salida (se crea; no debe existir ya)')
    ap.add_argument('--windeployqt', default=shutil.which('windeployqt'))
    ap.add_argument('--converter', type=Path, help='carpeta del proceso de trabajo de stage_converter.py')
    ap.add_argument('--vc-redist-dir', type=Path,
                    help='carpeta Microsoft.VC*.CRT del redistribuible de MSVC (runtime de C++)')
    ap.add_argument('--zip', action='store_true', help='además, comprime la carpeta')
    args = ap.parse_args()
    if sys.platform != 'win32':
        ap.error('empaquetador de Windows; para macOS usa package_profile_app.py')
    if not args.windeployqt:
        ap.error('no se encontró windeployqt (añade la carpeta bin de Qt al PATH o usa --windeployqt)')
    windeployqt = Path(args.windeployqt).resolve()
    qmake = windeployqt.with_name('qmake.exe')
    if not qmake.is_file():
        ap.error('no hay un qmake junto a windeployqt: no se puede comprobar la versión de Qt')

    # El generador de Visual Studio es multiconfiguración y deja el ejecutable en app/Release/; Ninja y los
    # generadores de una sola configuración, directamente en app/.
    build = args.build_dir.resolve()
    candidates = [build / 'app/Release/cdng2braw.exe', build / 'app/cdng2braw.exe']
    source = next((path for path in candidates if path.is_file()), None)
    if source is None:
        ap.error('compila primero la app Qt; no existe ninguno de: ' + ', '.join(map(str, candidates)))
    target = args.out.resolve()
    if target.exists():
        ap.error('la carpeta de salida ya existe; elige otra')
    target.mkdir(parents=True)
    shutil.copy2(source, target / source.name)

    qt_version = qt_query(qmake, 'QT_VERSION')
    run([windeployqt, '--release', '--dir', target, target / source.name])

    # Proceso de trabajo donde lo busca la app: <carpeta del exe>/converter/cdng-convert.exe
    if args.converter:
        worker = args.converter.resolve()
        if not (worker / 'cdng-convert.exe').is_file():
            ap.error(f'no existe {worker / "cdng-convert.exe"}; ejecuta stage_converter.py antes')
        shutil.copytree(worker, target / 'converter')

    # Runtime de MSVC, para no depender de que el equipo tenga el redistribuible de Visual C++ instalado
    # (Microsoft permite redistribuir estas DLL). Se copian a mano en lugar de con --compiler-runtime de
    # windeployqt: con Build Tools (sin Visual Studio completo) no encuentra el redistribuible ni con
    # VCINSTALLDIR puesto, y avisa pero deja el paquete sin runtime.
    #
    # Va en las dos carpetas: cdng-convert.exe es un proceso aparte y Windows busca sus DLL en la carpeta del
    # propio ejecutable (converter\), no en la del padre, así que con el runtime solo arriba no arrancaría.
    if args.vc_redist_dir:
        redist = args.vc_redist_dir.resolve()
        if not redist.is_dir():
            ap.error(f'no existe la carpeta del redistribuible: {redist}')
        dlls = sorted(redist.glob('*.dll'))
        if not dlls:
            ap.error(f'no hay ninguna DLL en {redist}')
        destinations = [target] + ([target / 'converter'] if args.converter else [])
        for destination in destinations:
            for dll in dlls:
                shutil.copy2(dll, destination / dll.name)
        print(f'Runtime de MSVC en {len(destinations)} carpeta(s): {", ".join(d.name for d in dlls)}')
    else:
        print('AVISO: sin --vc-redist-dir; el equipo de destino necesitará el redistribuible de '
              'Visual C++ 2015-2022 instalado.')

    # Las licencias de LibRaw y Qt piden que los avisos estén accesibles: la app los lee de su propia carpeta.
    shutil.copy2(ROOT / 'app/THIRD_PARTY_NOTICES.md', target / 'THIRD_PARTY_NOTICES.md')
    qt_licenses = Path(qt_query(qmake, 'QT_INSTALL_PREFIX')) / 'LICENSES'
    if qt_licenses.is_dir():
        shutil.copytree(qt_licenses, target / 'QtLicenses', dirs_exist_ok=True)

    # Comprobación real de que el paquete arranca: la CLI de solo lectura no necesita pantalla.
    status = json.loads(run([target / source.name, '--profile-status'], capture_output=True).stdout)

    archive = None
    if args.zip:
        archive = target.with_name(f'{target.name}.zip')
        if archive.exists():
            archive.unlink()
        with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as zf:
            for path in sorted(target.rglob('*')):
                if path.is_file():
                    zf.write(path, Path(target.name) / path.relative_to(target))

    result = dict(package=str(target), zip=str(archive) if archive else None, qt_version=qt_version,
                  status=status, conversion_worker_included=bool(args.converter),
                  signed=False, scope='Qt app for Windows x64; unsigned, profile install needs administrator (UAC)')
    (target.parent / 'package-check-windows.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
