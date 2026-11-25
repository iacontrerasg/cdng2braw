# Image fidelity benchmark

Automated bank for measuring how close the CinemaDNG → BRAW pipeline stays to
DaVinci Resolve's CinemaDNG develop, and for catching regressions between
builds — without opening Resolve on every iteration.

This is **infrastructure only**. Debayer, deblocking, chroma, compression,
colour matrices, the ISO model and the calibrated signal curve are not changed
here.

## Baseline

| Field | Value |
|---|---|
| Commit | `352cfab4a5b9217abbfb06b15913fb6b044b5b87` |
| Description | Merge of PR #1 (Pocket OG ISO exposure model) |
| Recorded in | `benchmark/BASELINE` |

That commit is the research-stable build this bank characterises. Frozen
decoded frames of a run against it live under `out/benchmark/baselines/`
(not in Git).

## Layout

| Path | Role |
|---|---|
| `benchmark/manifest.json` | Dataset: takes, frames, expected BE/ISO, Decode settings, EXR names |
| `benchmark/rois/*.json` | Regions of interest (patches, edges); editable without Python changes |
| `benchmark/thresholds.json` | Equivalence tolerances and optional acceptance rules (documented basis) |
| `tools/fidelity/` | Runner: convert → decode → metrics → maps → HTML |
| `out/benchmark/runs/` | One directory per run (ignored by Git) |
| `out/benchmark/cache/` | Content-addressed BRAW and decoded `.f32` (identity verified) |
| `out/benchmark/baselines/` | Frozen runs for regression |

The CinemaDNG dataset, Resolve EXRs and BRAW files are **not** in Git. Default
paths: `~/Desktop/BMD` and `~/Desktop/EXR` (relative to the dataset as
`../EXR` in the manifest).

## Two modes

### Reference fidelity

Decode the BRAW with the Blackmagic RAW SDK (`tools/brawdec`) to RGB float
linear and compare against a CinemaDNG OpenEXR exported from Resolve of the
**same source frame**, developed as:

- Color Science **Gen 4** (CinemaDNG path in Resolve)
- Gamut **DCI-P3 D65**, Gamma **Linear**
- White balance **As Shot**
- Sharpness **0**, Highlight Recovery **OFF**
- No LUT, CST or extra processing

LibRaw is **not** used as the reference: the point is to measure our pipeline
against Resolve's CinemaDNG develop.

On the BRAW side, the SDK only offers colour science generation **5** for these
clips. Decode settings used by the bank (and verified bit-identical to
`brawdec` defaults): P3 D65, Linear, `csgen` 5, `hlry` 0, As Shot ISO/WB,
Exposure from the reference entry when needed (e.g. −3 EV for A04 frame 44).

### Regression

Compare a candidate build's decoded `.f32` to a **frozen** baseline run with
the same inputs, encode/decode settings and frames. No new Resolve export is
required. A mismatch in regression setup (clip, ordered DNG hashes of the
converted range, encode request, experimental env, decode, ISO) aborts the
comparison; the EXR reference block is **not** required for that check
(`--no-reference`). Per-frame decode overrides from the manifest (e.g. A04
frame 44 at −3 EV) still apply. A BRAW or decode cache entry from another
build is rejected (identity re-checked, never reused silently).

## Commands

```bash
make venv                 # numpy, scipy, OpenEXR, …
make worker               # staged converter (build/worker/converter)
make brawdec

# Validate manifest + DNG metadata + EXR presence (series A complete)
make benchmark-validate DATASET=~/Desktop/BMD

# Full series A (reference fidelity)
make benchmark DATASET=~/Desktop/BMD SERIES=A

# One take / custom run id
make benchmark DATASET=~/Desktop/BMD TEST=A02 RUN=smoke-a02

# Frames / compression overrides (CLI)
python -m tools.fidelity run --dataset ~/Desktop/BMD --test A04 --frames 44,49 --q 4
python -m tools.fidelity run --dataset ~/Desktop/BMD --series A --ratio 3

# Freeze a run as the regression baseline
make benchmark-freeze RUN=<run-id> NAME=main-352cfab

# Candidate vs frozen baseline
make benchmark DATASET=~/Desktop/BMD SERIES=A BASELINE=main-352cfab RUN=candidate-1

# Regression only (no EXR required; decode overrides from the manifest still apply)
python -m tools.fidelity run --dataset ~/Desktop/BMD --series A --no-reference \
  --baseline main-352cfab --run regression-no-exr

# Compare two existing runs (add --allow-setup-differences for e.g. q4 vs q8)
python -m tools.fidelity compare out/benchmark/baselines/main-352cfab out/benchmark/runs/candidate-1

# brawdec vs Resolve's own BRAW EXR (decoder equivalence, no conversion)
make benchmark-equivalence DATASET=~/Desktop/BMD

# Unit tests (no dataset)
make fidelity-test
```

Environment: `PYTHON` defaults to `.venv`, then `out/.venv-exr`, then
`python3`. Override with `PYTHON=…`.

## Manifest (series A)

| ID | Clip | ISO | BE | Frames | Resolve EXR |
|---|---|---:|---:|---|---|
| A01 | C0036 | 200 | 0.1 | 52 | `DNG200_00086452.exr` |
| A02 | C0037 | 400 | 1.1 | 29 | `DNG400_00086516.exr` |
| A03 | C0038 | 800 | 2.1 | 46 | `DNG800_00086635.exr` |
| A04 | C0039 | 1600 | 3.1 | 44 (−3 EV), 49 | `DNG1600MINUS3_…`, `DNG1600_…` |
| A05 | C0040 | 800 | 2.1 | 53 | `DNG800CONTROL00086843.exr` |

Series **B** (aperture), **C** (white balance) and **D** (dark frames) are in
the manifest as `pending`: clip paths and observed BE/AsShotNeutral are filled
from the DNGs where known; aperture, frames and EXRs are left null until they
can be determined. The bank does not invent those values.

Aperture is not present in these DNGs (no EXIF IFD); that field stays `null`
with a note.

## Resolve EXR references

Place exports under `~/Desktop/EXR` (or pass `--references`). Required names
match the table above. Each EXR must:

1. Come from the listed DNG frame of that take (same timeline timecode as in
   the manifest; the bank checks the EXR `timeCode` attribute).
2. Use Gen 4, P3 D65, Linear, As Shot, Sharpness 0, Highlight Recovery OFF.
3. Be RGB float (half or float), no LUT/CST.

If an EXR is missing, `validate` and `run` report which file is expected and
from which frame. Do not invent references or retarget metrics to match older
hand measurements.

Optional **BRAW** EXRs (`BRAW400_…`, `BRAW1600MINUS3_…`, …) are only for the
`equivalence` command (SDK decode vs Resolve's BRAW render of the same file).

## Metrics

Computed on the original float RGB (no clip to 0–1, no gamma, no 8-bit):

- MAE, MSE, RMSE, PSNR with peak **1.0**, RGB correlation
- Per-channel signed mean error; P3-D65 luminance relative error (%)
- Absolute-error percentiles; channel min/max; negative and >1.0 fractions
- Fraction of pixels at the signal ceiling
- Error by luminance bins
- Luma vs chroma (Δu′v′) on all / edge / flat masks; edge sharpness ratios
- Near-neutral Δu′v′ to D65
- Per-ROI MAE/PSNR/Δu′v′; edge profiles (overshoot, undershoot, fringe)
- BRAW size, effective compression, convert/decode timing

Exposure-normalised metrics multiply **both** images by
`(normalize_to_iso / ISO) · 2^(−exposure)` so a linear ISO gain is not scored
as a fidelity loss. Default `normalize_to_iso` is 200.

Hand measurements from Resolve (research, not golden tests for other frames):

| ISO | PSNR normalised to ISO 200 |
|---:|---:|
| 200 | 42.89 dB |
| 400 | 42.55 dB |
| 800 | 42.49 dB |
| 1600 | 42.56 dB |
| 800 control | 42.53 dB |

ISO 1600 at −3 EV (both formats): PSNR 42.5454 dB, MAE 0.00308508, RMSE
0.00745984, correlation 0.9980229, luminance rel. error +0.0704 %.

## Schema (metrics / run)

`tools.fidelity.SCHEMA` is currently **2**. Relative to schema 1:

| Field | Change |
|---|---|
| `setup.inputs` | Ordered `{index, name, sha256}` for **every** DNG in the converted range |
| `setup.encode.experimental_env` | Full `OUTPUT_ENV` map (value or `null`) used for cache and setup identity |
| `results.*.decode_verification` | Requested / applied / unverifiable decode attributes from `brawdec --frame-info` |
| Chromaticity metrics | `duv_samples`, `duv_valid`, `duv_invalid`, `duv_valid_fraction` (global, ROI, neutral) |
| Cache material | `info_schema: 2` on BRAW and decode entries (invalidates schema-1 cache) |

`encode.experimental_env` in the manifest declares optional `CDNG_*` that affect
the BRAW. Ambient undeclared `CDNG_*` that can change output are **rejected**
(no silent overrides). Diagnostic vars (`CDNG_PIPELINE_TRACE`, …) are logged
and stripped from the worker child. Adding a new experimental knob means
listing it in `OUTPUT_ENV` in `tools/fidelity/pipeline.py` — do not implement
deblocking options here yet.

Frozen baselines from schema 1 must be regenerated with the same converter
build (`make benchmark` then `make benchmark-freeze … --force`) because setup
identity and metric keys changed.

## Run outputs

Each `out/benchmark/runs/<id>/` contains:

- `metrics.json`, `metrics.csv`, `report.html`
- `run.json` (git commit, worker/model/SDK/profile hashes, experimental env, command, timing, cache)
- `frames/*.f32`, `png/*`, `logs/*`
- `comparison.json` when `--baseline` is set

Comparative reports mark each metric as improved / worse / equivalent /
`not_evaluable` (NaN or non-numeric) using `benchmark/thresholds.json`.
Infinity serialised as `"inf"` compares correctly (∞↔∞ equivalent; finite↔∞
follows metric direction). Default acceptance list is **empty**; add rules
only with a documented `basis`.

## Decoder equivalence (brawdec vs Resolve)

`make benchmark-equivalence` decodes listed BRAW files with the same settings
as the bank and compares them to Resolve's BRAW EXRs. Use this to confirm the
SDK path before trusting reference-fidelity numbers. Results go to
`out/benchmark/equivalence/`.

Assumptions checked when building the bank:

- Explicit brawdec flags for P3 D65 / Linear / csgen 5 / hlry 0 match the
  tool's defaults bit-for-bit.
- Detail correlation between BRAW decode and DNG EXR separates correct frames
  (≥ ~0.82) from neighbours (~0.64); the manifest threshold is 0.74.

### Measured decoder equivalence (`make benchmark-equivalence`)

On BRAW files exported from Resolve alongside matching BRAW EXRs
(`BRAW400_00086516.exr`, `BRAW1600MINUS3_00086731.exr`):

| Clip / frame | PSNR (norm.) | Correlation | Mean signed error | Bit-identical |
|---|---:|---:|---:|---|
| C0037 f29 @ ISO 400 | 78.30 dB | 0.9999995 | +9.1×10⁻⁶ | no |
| C0039 f44 @ −3 EV | 78.26 dB | 0.9999995 | +9.1×10⁻⁶ | no |

So `brawdec` with the bank's decode settings is **near-equivalent** to Resolve's
BRAW render (sub-ULP bias, ~78 dB), not bit-identical. Reference-fidelity scores
against CinemaDNG EXRs therefore include that small decoder path difference plus
the conversion pipeline.

### First series-A run against baseline `352cfab` (q4, this bank)

| Result | ISO | PSNR norm. | MAE | Correlation | Y rel. % |
|---|---:|---:|---:|---:|---:|
| A01_f052 | 200 | 43.07 | 0.00276 | 0.99823 | +0.063 |
| A02_f029 | 400 | 42.68 | 0.00285 | 0.99804 | +0.063 |
| A03_f046 | 800 | 42.62 | 0.00288 | 0.99804 | +0.061 |
| A04_f044 (−3 EV) | 1600 | 42.68 | 0.00288 | 0.99809 | +0.062 |
| A04_f049 | 1600 | 42.70 | 0.00288 | 0.99810 | +0.060 |
| A05_f053 | 800 | 42.67 | 0.00289 | 0.99810 | +0.059 |

These sit next to the earlier hand measurements (~42.5–42.9 dB); they are **not**
asserted as golden values for other frames. Report (schema 2 re-freeze with the
same converter, no image-pipeline change):
`out/benchmark/runs/series-a-schema2-1/report.html`, frozen as
`out/benchmark/baselines/main-352cfab/`. Regenerated because setup identity now
includes the full DNG range and `experimental_env`, and metrics include decode
verification plus chroma validity counts.

Limitations:

- Resolve's CinemaDNG path is Gen 4; BRAW colour science for these clips is
  gen 5 in the SDK — they are different product paths; the bank measures the
  end-to-end difference, not “same gen”.
- Camera Support profiles on disk are hashed into decode identity: changing
  installed profiles invalidates the decode cache (by design).
- As Shot ISO / Kelvin / tint: when the SDK reports an effective value it is
  recorded as **applied**; cross-check against DNG AsShotNeutral / ISO is not
  implemented yet (those attributes stay unverifiable when the SDK omits them).
- A lower Δu′v′ with a higher `duv_invalid` count is visible in the report and
  counts as worse for `duv_invalid` / `duv_valid_fraction` in comparisons.

## Out of scope

Debayer, deblocking, chroma reconstruction, compression, colour matrices, ISO
model, calibrated profiles and signal curve. Image-algorithm work belongs to
BUG-002 and later tasks that consume this bank.
