# Changelog

## Unreleased

- Image fidelity benchmark (`make benchmark`): convert selected CinemaDNG frames
  with the worker, decode with `brawdec`, compare RGB float to Resolve OpenEXR
  references and to frozen baselines; ROI metrics, error maps and HTML reports
  under `out/benchmark/` (see `docs/image-fidelity-benchmark.md`). Series A
  manifest included; series B–D pending completion from the DNGs.
- Original Pocket Cinema Camera: the sensor data is encoded at unity gain and
  the exposure travels as the clip's ISO (200, 400, 800 or 1600, inferred from
  the DNG's BaselineExposure and checked against its EXIF ISO when there is
  one), applied when the clip is decoded. Highlights that ISO 800 and 1600 takes
  used to clip are kept, and Resolve opens them with Exposure at 0. Takes with
  a BaselineExposure outside the table are refused.
- A new revision of the Pocket camera profile with that ISO table. The installer
  keeps the earlier revision next to it, so BRAW files written before keep
  opening; the colour and signal calibration are unchanged.
- The clip and frames now declare unity analog gain (`analog_gain` 1,
  `analog_gain_is_constant` 1, `agpf` 1), with no change to the decoded image.
- `CDNG_PIPELINE_TRACE=1` writes a pipeline trace next to each BRAW (off by
  default).
- Known limitation: Resolve shows no ISO selector for cameras added through
  Camera Support profiles (the SDK does not expose their ISO list to it).

## 1.0.0

The first public release.

- Converts the CinemaDNG takes of the Sigma fp into Blackmagic RAW, one BRAW per
  take, with the take's WAV inside. Also the original Blackmagic Pocket Cinema
  Camera (2013), through an experimental camera profile the app installs, and,
  through a generic route with the white balance fixed, other cameras.
- The camera of each Sigma fp take is written automatically ("SIGMA fp" or
  "SIGMA fp L", as in the DNG).
- Original Pocket Cinema Camera: the BRAW matches the DNG as Resolve develops it
  without colour management, white balance included, and opens in P3-D65
  linear. With the profile, the app installs a LUT for DaVinci Resolve, measured
  on its DNG decoder, from DaVinci Wide Gamut / Intermediate to the DNG's default
  "Blackmagic Design" look, and a second one that gives Sigma fp takes the same
  look.
- Compression at a target ratio, 3:1 to 12:1, or at constant quality, Q0, Q1,
  Q3 and Q5, or any quantiser from 0 to 24.
- Resolution: the original or a narrower width, keeping the aspect ratio of each
  take, open gate included.
- Noise reduction before encoding: Soft, Medium, Strong or custom percentages
  for luma and colour, with a window that compares the original and the result.
- Gyro data from fpSup's Gyro2 module, written into the BRAW as motion tracks and
  in a Gyroflow project with a lens profile from the DNGs; optionally also as
  `.gcsv` and `.fsg2` files.
- Each BRAW gets its own camera ID, the take's name as clip number and the
  recording date of the DNGs.
- Desktop builds for Apple Silicon (macOS 26 or later) and x64 Windows, each
  with the conversion engine inside.
