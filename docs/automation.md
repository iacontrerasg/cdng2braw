# Driving the app from outside

Started with `--automation`, the app listens for commands on a local socket and
can be driven end to end: load a folder, change any setting, convert, take
screenshots. It is how the app is tested as a whole, on the real build rather
than on separate test binaries. Server: [`app/automation_server.cpp`](../app/automation_server.cpp);
client: [`tools/app_control.py`](../tools/app_control.py); tests:
[`tests/test_app_automation.py`](../tests/test_app_automation.py).

## Starting it

```sh
"cdng2braw.app/Contents/MacOS/cdng2braw" --automation [NAME] \
    [--profile-root FOLDER] [--backend PATH/cdng-convert] [--language es|en]
```

- **Only with `--automation`.** Started from the Finder, or without that
  argument, the app listens to nothing.
- The socket is local (Unix on macOS and Linux, a named pipe on Windows) and
  only the current user can open it.
- Every request carries a random **token**. The app prints
  `{automation, token, pid, connection_file}` as the first line of its standard
  output and writes the same to `$TMPDIR/cdng2braw-automation.json` (mode 0600).
- `--profile-root`: a folder of test camera profiles instead of the system one,
  which asks for an administrator password.
- `--backend`: another `cdng-convert` engine (the app in the build tree does not
  carry one inside).
- With `QT_QPA_PLATFORM=offscreen` it runs without a screen, but only the build
  tree app has that platform plugin; the packaged app carries only the macOS
  one.

## Protocol

One JSON line per request, `{"id", "token", "cmd", …}`, and one per answer,
`{"id", "ok": true, "result"}` or `{"id", "ok": false, "error"}`. After
`subscribe`, the app also sends events, `{"event", …}`.

Widgets are named with a **selector**: an object name such as
`startConversion`, `inputFolder` or `nrLuma`, searched first in the active modal
window and then in the visible ones; the path that `tree` returns, for widgets
without a name; or a filter, `{"name", "text", "type", "index"}`.

| Command | Parameters | What it does |
|---|---|---|
| `hello` | | Version, pid and language. |
| `tree` | `target`, `all` | Widgets (visible ones, or all) with path, name, type, text, value, state and geometry. |
| `describe` | `target` | The same for one widget. |
| `dialogs` | | Open windows, the modal one first; for message boxes, their text and buttons. |
| `get` / `set` | `target`, `property`, `value` | Reads or writes a Qt property or a `describe` field. |
| `click` | `target`, `at` | A real mouse click, in the centre or at `[x, y]`. |
| `drag` | `target`, `from`, `to` | A mouse drag. |
| `type` | `target`, `text`, `clear`, `enter` | Types with key events; with `enter`, presses Return at the end. |
| `key` | `key`, `target` | One key: Return, Tab, Escape, Up, Down… |
| `select` | `target`, `index` / `text` / `data` | Picks an item of a combo box. |
| `drop` | `paths`, `target` | Drops files or folders as from the Finder. |
| `invoke` | `method`, `target` | Calls a public action of the window. |
| `wait` | `target`, `timeout` (ms), `property`, `equals` / `not` / `contains` / `exists` | Waits for a condition. |
| `screenshot` | `target`, `path` | A PNG, in base64 or saved to `path`. |
| `state` | | The whole state of the window. |
| `profile` | `action` (install, remove, status), `profile` (`pocket-og`, `sigma-fp`) | Manages the camera profile in `--profile-root`. |
| `subscribe` | | Events: `preview_ready`, `noise_ready`, `preview_error`, `conversion_message`, `conversion_progress`, `conversion_finished`. |
| `quit` | | Closes the app. |

**`type` behaves like a user; `set` does not.** `type` emits the same signals as
typing by hand (`type inputFolder … enter=True` scans the folder); `set` only
changes the value. To type into a spin box, target its text field, the
`qt_spinbox_lineedit` child that `tree` lists under it.

**Batches.** `inputFolder` is the batch folder, `clipList` the list of takes
and `outputFolder` the destination (one BRAW per take; existing ones are
skipped). `conversion_finished` arrives once per take; the end of the batch is
`wait conversionState contains "Batch finished"`.

**Out of reach:** the system's native file dialogs and the administrator
password for installing the profile system-wide. Type the path into the field
instead, and use `--profile-root` with the `profile` command.

## Example

```python
from app_control import App
with App.launch(backend='…/cdng-convert', profile_root='/tmp/profiles', offscreen=True) as app:
    app.subscribe()
    app.type('inputFolder', '/path/to/the/card', enter=True)   # every take inside
    print(app.wait_event('preview_ready'))
    app.click('nrPreset2')                                       # Medium noise reduction
    app.type('outputFolder', '/tmp/braw')
    app.click('startConversion')
    app.wait('conversionState', timeout=600, contains='Batch finished')
    app.screenshot('/tmp/final.png')
```

The tests: `CDNG_TEST_INPUT=folder_with_one_short_take python -m unittest tests.test_app_automation -v`
(without `CDNG_TEST_INPUT` they are skipped). Against the packaged app with the
window on screen, add
`CDNG_TEST_APP=".../cdng2braw.app/Contents/MacOS/cdng2braw" CDNG_TEST_ONSCREEN=1`.
