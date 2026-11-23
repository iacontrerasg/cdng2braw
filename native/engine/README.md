# The conversion engine

`cdng-convert` turns a folder of CinemaDNG frames into one Blackmagic RAW file.
It is C++17 with two dependencies, LibRaw (to read the DNG) and simdjson (to
read the requests and the calibration files), and no Qt: the app starts it as a
separate process and talks to it with one JSON line per message.

| Part | Files | What it does |
|---|---|---|
| Common | `common.*`, `geometry.*` | Errors, byte helpers, frame geometry and slices |
| DNG | `dng.*`, `dng_math.hpp`, `resize.cpp`, `wav.cpp` | Reads the mosaic and the DNG tags, scales open gate and narrower widths, reads the WAV |
| Signal | `signal*.cpp`, `signal.hpp`, `nr.cpp` | Calibration of each camera route, the Y, Cb and Cr planes, noise reduction |
| Codec | `codec*.cpp/hpp` (+ `../src/entropy.cpp`) | 8 × 8 DCT, quantisers, Huffman coding, deblocking compensation |
| Container | `container.*` | The QuickTime file: frames, audio, timecode, motion tracks, clip metadata |
| Worker | `worker/` | The process the app runs: requests, batches, preview, gyro data and Gyroflow project |

How the format works, and why the engine does what it does, is in
[docs/format.md](../../docs/format.md).

## Building and testing

The engine builds with the rest of the project (see the README at the root):
`make test` on macOS, `.\build.ps1 test` on Windows. The tests in `tests/` run
without any sample footage.

```sh
./cdng-convert --self-test           # checks that it starts and finds its calibration
./cdng-convert --request job.json    # one conversion; progress and result on stdout
```

The comments in the source are in Spanish.
