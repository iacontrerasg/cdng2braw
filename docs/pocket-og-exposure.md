# Original Pocket Cinema Camera: exposure and ISO

How the original Blackmagic Pocket Cinema Camera (2013) carries its exposure into
Blackmagic RAW, and what Resolve does with it. The colour (matrices, white
balance tables) and the calibrated signal curve are those described in
[format.md](format.md); none of that changes here.

## The model

The camera records CinemaDNG at one sensor gain and expresses the ISO it was set
to as `BaselineExposure` (BE): 1.1 EV more than the calibration exposure for
every stop. The converter encodes the sensor data at **unity gain** and writes
the ISO as metadata, which the decoder turns into a digital gain when the clip
is opened:

| ISO | BaselineExposure | `pre_gain` | `post_gain` |
|---:|---:|---:|---:|
| 200 | 0.1 | 1.0 | 0.5 |
| 400 | 1.1 | 1.0 | 1.0 |
| 800 | 2.1 | 1.0 | 2.0 |
| 1600 | 3.1 | 1.0 | 4.0 |

- `post_gain` = 2^BE / 2^1.1. It is applied by the SDK at decode time and is
  identical, bit for bit, to Exposure = log2(`post_gain`) on the unity-gain
  data; `pre_gain` (analog gain) stays at 1.
- `base_iso` and `native_iso` are 400. That is the **calibration reference**
  (BE 1.1 is the exposure the colour and signal were fitted at), not a statement
  about the physical native sensitivity of the sensor.
- Encoding at unity gain keeps the highlights: multiplying the data by 2^BE /
  2^1.1 before encoding, as earlier builds did, clipped everything above
  1/`post_gain` of the curve's ceiling (code 4095).

## Inferring the ISO

`ogExposure` (`native/engine/signal_source.cpp`) looks up the BE of each frame in
the table with a tolerance of ±0.01 EV:

- a BE inside the tolerance of one stop gives that ISO and its `post_gain`;
- **any other BE is rejected** with "BaselineExposure is not represented by the
  Pocket Cinema Camera ISO table (ISO 200, 400, 800, 1600)". The nearest ISO is
  never picked silently: an ISO the profile does not list would make the SDK
  fail, and a guessed one would misstate the exposure;
- a non-finite BE is rejected as "Invalid source BaselineExposure";
- when the DNG carries an EXIF ISO (tag 34855) it must equal the inferred one,
  or the conversion stops with "source ISO does not match its BaselineExposure".
  The camera's own DNGs carry none, so BE is the source.

The table lives in two places that are checked against each other when the
calibration is loaded (`loadOgModel`): the BE of each stop, the tolerance and the
reference ISO in `model/pocket_og_cinemadng_signal.json`, and the gains in the
Camera Support profile. Loading fails with "Camera Support ISO table does not
match this signal calibration" if the stops differ, if a `pre_gain` is not 1, if
a `post_gain` is not 2^BE / `baseline_exposure_gain`, if the reference ISO is not
the one with `post_gain` 1, or if the tolerance reaches a neighbouring stop.

## What the BRAW carries

Per frame (`bmdf`): `isoe` = the inferred ISO, `expo` = 0 (no exposure offset:
the ISO alone compensates), `agpf` = 1.0 (unity analog gain), plus white balance
and frame rate. Per clip (`moov/meta`): `camera_type` "Blackmagic Pocket Cinema
Camera", `analog_gain` = 1.0 and `analog_gain_is_constant` = 1, as the SIGMA fp
route writes them, and the profile revision in `ctrn`.

Earlier builds wrote `analog_gain` = 0, `agpf` = 0 and no
`analog_gain_is_constant`; decoding is the same either way (checked bit for bit,
see below), the new values only describe the clip correctly.

## Profile revisions

The SDK pairs a clip with an external profile by `camera_type` and the revision
in `ctrn`, so a revision is a fixed contract with every BRAW written with it.

| Revision | File in Camera Support | Used by | ISO |
|---|---|---|---|
| 1128549959 (`CDNG`) | `pocket-og-cinemadng-experimental.json` | BRAW written by earlier builds | 1250 only |
| 1128878927 (`CISO`) | `pocket-og-cinemadng-iso.json` | BRAW written from now on | 200, 400, 800, 1600 |

- The calibrated revision is kept byte for byte (SHA-256
  `63a0358740bfc98a459769a68d5490345785d1cbba42aa7086c6fe192d62fb5c`).
- The ISO revision is generated from it by `tools/make_pocket_og_iso_profile.py`,
  which changes only the revision, the name, `iso_params`, `native_iso` and
  `iso_modification_supported`, verifies that everything else (colour matrices,
  `inv_sqrt` curve, levels, white balance tables) is identical, and updates the
  signal JSON. `--check` fails if the committed files are not what it produces.
- The signal JSON lists the earlier revision in `legacy_revisions`.
- The installer (`app/profile_store.cpp`) installs both: the ISO revision as the
  profile and the calibrated one as its companion. "Ready" means each revision
  is present exactly once and matches its pinned SHA-256. Upgrading from a
  machine that has only the calibrated revision adds the new file and keeps the
  old one; duplicates or altered copies are backed up and consolidated;
  uninstalling removes both. If writing the second file fails, the first one is
  removed again and the earlier state restored.

## Resolve's ISO selector

Resolve 21 opens these clips at their nominal exposure with Exposure 0.00, but
with Decode Using = Clip its Camera RAW panel shows **no ISO selector**. Resolve
builds that control from `IBlackmagicRawConstants::GetISOListForAnalogGain(cameraType, …)`,
a lookup by camera type in the products built into the SDK; for an external
Camera Support profile it returns `0x80000008`, whatever the profile or the
clip says. The per-clip API does expose the ISO list (200–1600, editable, as
`iso_modification_supported` is true) and applications built on it can change
the ISO; Resolve cannot.

This is accepted as a limitation of the SDK. The profile does not impersonate
any built-in camera and the SDK is not modified; the selector would need
Blackmagic to expose external profiles' capabilities through
`IBlackmagicRawConstants`. The full investigation is in
[experiment-pocket-og-unity-gain.md](experiment-pocket-og-unity-gain.md).

## Diagnostics

`CDNG_PIPELINE_TRACE=1` in the converter's environment writes
`<output>.pipeline-trace.txt` next to each BRAW: the build, the SHA-256 of the
executable, the signal JSON and the profile, and per frame the route, BE, the
inferred ISO, `post_gain`, the encode gain and channel statistics at each stage.
It is off by default (unset, empty or `0`), and it does not change the BRAW.

## Verification

Automated (`make test`):

- `engine_signal`: inference for each stop, inside and just outside the
  tolerance, float and rational encodings of BE, rejection of values between
  stops or outside the table and of non-finite values, EXIF consistency, the
  cross-check of the two tables, and that the ISO revision differs from the
  calibrated one only in the ISO fields.
- `engine_dng`: synthetic Pocket DNGs through `ogSourceInfo`, which must give
  encode gain 1, the ISO and its `post_gain`, and reject BE outside the table.
- `engine_container`: `isoe`, `expo` = 0 and `agpf` = 1.0 in the frames,
  `analog_gain` = 1.0 and `analog_gain_is_constant` = 1 in the clip, and the
  revision in `ctrn`.
- `profile_store`: both revisions pinned, installed, upgraded from the
  calibrated one alone, consolidated, rolled back and removed.

With the SDK of Resolve 21 (`tools/brawdec`, both revisions installed), on takes
C0036 (ISO 200), C0037 (400), C0038 and C0040 (800) and C0039 (1600):

- the decoder reports `fiso` = the inferred ISO, `agpf` 1, `expo` 0 and the ISO
  list 200, 400, 800, 1600;
- each take decoded at its ISO equals, bit for bit, the same take decoded at
  ISO 400 times its `post_gain`;
- each take decoded at its ISO equals, bit for bit, the BRAW of the calibrated
  revision decoded with Exposure = log2(`post_gain`), and at ISO 400 the same
  BRAW at Exposure 0: no regression from `agpf`, `analog_gain` or the new
  revision;
- BRAW files of the calibrated revision still open (ISO 1250, as before);
- a take with BE 1.6 is refused before anything is written.

## Out of scope

Debayering, deblocking, chroma and compression are unchanged; changes there
belong to BUG-002.
