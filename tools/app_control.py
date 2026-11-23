#!/usr/bin/env python3
"""Cliente del modo de automatización de la app (cdng2braw --automation). Ver docs/automation.md.

Como biblioteca:
    from app_control import App
    with App.launch(backend=..., profile_root=..., offscreen=True) as app:
        app.type('inputFolder', '/ruta/a/la/toma', enter=True)
        app.wait_event('preview_ready')
        app.click('startConversion')
        app.wait_event('conversion_finished', timeout=300)

Desde la consola (con la app ya arrancada con --automation; lee el fichero de conexión del directorio temporal):
    tools/app_control.py hello
    tools/app_control.py tree
    tools/app_control.py click startConversion
    tools/app_control.py get outputFile text
    tools/app_control.py call '{"cmd": "wait", "target": "conversionState", "contains": "completada", "timeout": 60000}'
    tools/app_control.py launch --backend RUTA --profile-root CARPETA [--offscreen]
"""
import argparse
import itertools
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_APP = ROOT / 'out/cdng2braw/cdng2braw.app/Contents/MacOS/cdng2braw'
CONNECTION_FILE = Path(tempfile.gettempdir()) / 'cdng2braw-automation.json'


class AutomationError(RuntimeError):
    pass


class App:
    def __init__(self, socket_path, token, process=None):
        self.process = process
        self.token = token
        self.socket_path = socket_path
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(socket_path)
        self.buffer = b''
        self.ids = itertools.count(1)
        self.events = []

    # ---- arranque y conexión --------------------------------------------------------------------------------------
    @classmethod
    def launch(cls, app=DEFAULT_APP, backend=None, profile_root=None, offscreen=False, language='es', extra=()):
        command = [str(app), '--automation', '--language', language]
        if backend: command += ['--backend', str(backend)]
        if profile_root: command += ['--profile-root', str(profile_root)]
        command += list(extra)
        env = dict(os.environ, QT_QPA_PLATFORM='offscreen') if offscreen else None
        process = subprocess.Popen(command, stdout=subprocess.PIPE, env=env, text=True)
        line = process.stdout.readline()
        if not line:
            raise AutomationError(f'la app no arrancó en modo de automatización (código {process.poll()})')
        info = json.loads(line)
        return cls(info['automation'], info['token'], process)

    @classmethod
    def connect(cls, connection_file=CONNECTION_FILE):
        info = json.loads(Path(connection_file).read_text())
        return cls(info['socket'], info['token'])

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self, quit_app=True):
        if self.process and quit_app and self.process.poll() is None:
            try: self.call('quit')
            except (OSError, AutomationError): pass
            try: self.process.wait(10)
            except subprocess.TimeoutExpired: self.process.kill()
        if self.process and self.process.stdout: self.process.stdout.close()
        self.sock.close()

    # ---- protocolo ------------------------------------------------------------------------------------------------
    def _read(self, timeout):
        self.sock.settimeout(timeout)
        while b'\n' not in self.buffer:
            data = self.sock.recv(1 << 20)
            if not data:
                raise AutomationError('la app cerró la conexión')
            self.buffer += data
        line, self.buffer = self.buffer.split(b'\n', 1)
        return json.loads(line)

    def call(self, cmd, deadline=60, **args):
        """Envía una orden y devuelve su resultado; `deadline`: segundos que espera la respuesta."""
        request = dict(args, cmd=cmd, id=next(self.ids), token=self.token)
        self.sock.sendall(json.dumps(request).encode() + b'\n')
        deadline = time.monotonic() + deadline
        while True:
            message = self._read(max(.1, deadline - time.monotonic()))
            if 'event' in message:
                self.events.append(message); continue
            if message.get('id') != request['id']:
                continue
            if not message['ok']:
                raise AutomationError(f"{cmd}: {message.get('error')}" + (f" (actual: {message['current']!r})" if 'current' in message else ''))
            return message.get('result')

    def wait_event(self, name, timeout=60, where=None):
        """Espera un evento (preview_ready, noise_ready, conversion_progress, conversion_finished…). Requiere subscribe()."""
        deadline = time.monotonic() + timeout
        while True:
            for i, event in enumerate(self.events):
                if event['event'] == name and (where is None or where(event)):
                    return self.events.pop(i)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AutomationError(f'no llegó el evento {name} en {timeout} s')
            try:
                message = self._read(remaining)
            except socket.timeout:
                continue
            if 'event' in message: self.events.append(message)

    # ---- atajos ---------------------------------------------------------------------------------------------------
    def hello(self): return self.call('hello')
    def tree(self, target=None, all=False): return self.call('tree', **({'target': target} if target else {}), all=all)
    def describe(self, target): return self.call('describe', target=target)
    def dialogs(self): return self.call('dialogs')
    def get(self, target, prop): return self.call('get', target=target, property=prop)
    def set(self, target, prop, value): return self.call('set', target=target, property=prop, value=value)
    def click(self, target, at=None): return self.call('click', target=target, **({'at': at} if at else {}))
    def drag(self, target, start, end): return self.call('drag', target=target, **{'from': start, 'to': end})
    def type(self, target, text, clear=True, enter=False): return self.call('type', target=target, text=text, clear=clear, enter=enter)
    def key(self, name, target=None): return self.call('key', key=name, **({'target': target} if target else {}))
    def select(self, target, **which): return self.call('select', target=target, **which)
    def drop(self, paths, target=None): return self.call('drop', paths=[str(p) for p in paths], **({'target': target} if target else {}))
    def invoke(self, method, target=None): return self.call('invoke', method=method, **({'target': target} if target else {}))
    def wait(self, target, timeout=10, **condition):
        """Espera a que el widget cumpla la condición: property=…, equals= / not= / contains= / exists=."""
        return self.call('wait', deadline=timeout + 5, target=target, timeout=int(timeout * 1000), **condition)
    def screenshot(self, path=None, target=None): return self.call('screenshot', **({'path': str(path)} if path else {}), **({'target': target} if target else {}))
    def state(self): return self.call('state')
    def profile(self, action, profile='pocket-og'): return self.call('profile', action=action, profile=profile)
    def subscribe(self): return self.call('subscribe')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('command', help='orden (hello, tree, click, get, set, type, key, select, dialogs, state, screenshot, call, launch…)')
    ap.add_argument('args', nargs='*')
    ap.add_argument('--backend'); ap.add_argument('--profile-root'); ap.add_argument('--app', default=str(DEFAULT_APP))
    ap.add_argument('--offscreen', action='store_true')
    a = ap.parse_args()
    if a.command == 'launch':
        app = App.launch(a.app, a.backend, a.profile_root, a.offscreen)
        print(json.dumps(app.hello(), ensure_ascii=False))
        app.close(quit_app=False)
        return
    app = App.connect()
    if a.command == 'call':
        request = json.loads(a.args[0]); cmd = request.pop('cmd')
        result = app.call(cmd, **request)
    else:
        names = {'click': ['target'], 'get': ['target', 'property'], 'describe': ['target'], 'type': ['target', 'text'],
                 'key': ['key'], 'screenshot': ['path'], 'invoke': ['method'], 'tree': ['target'],
                 'set': ['target', 'property', 'value']}.get(a.command, [])
        values = {}
        for name, value in zip(names, a.args):
            try: values[name] = json.loads(value) if name == 'value' else value
            except json.JSONDecodeError: values[name] = value
        result = app.call(a.command, **values)
    print(json.dumps(result, ensure_ascii=False, indent=1))
    app.close(quit_app=False)


if __name__ == '__main__':
    main()
