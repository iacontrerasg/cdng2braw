# Blackmagic RAW, as we understand it

Blackmagic has not published how a `.braw` file is written. What follows is
what this encoder does, worked out from clips recorded in both CinemaDNG and
BRAW, and from synthetic frames fed to the Blackmagic RAW SDK. Every rule here
is one the SDK decodes correctly; where we explain *why* the format does
something, that is our reading, and it is marked as such.

The article that goes with this repository tells the same story with figures
and interactive examples,
[on elcacharrista.com](https://www.elcacharrista.com/en/articles/cinemadng-to-braw-converter-with-ai/).

## Three planes instead of a mosaic

A CinemaDNG frame is the sensor's Bayer mosaic: one value per photosite, each
behind a red, green or blue filter. A BRAW frame is a **partial demosaic**:

- **Y**, a luma plane at full resolution, which carries the detail;
- **Cb** and **Cr**, two colour planes at half resolution, one value per 2 × 2
  quartet.

All three are stored after the same curve, close to a square root:

    code = 64.527 + 65.29 · √(x + 0.233)

with `x` the linear sensor signal, 0 at black. Below black, where noise gives
negative values, the curve carries on as a straight line instead of clipping.
In a sensor, noise grows with the signal; after a square root it is about the
same size across the whole scale, so the compression treats shadows and
highlights alike. This is probably the "12-bit non-linear space" Blackmagic
mentions.

**Luma.** `Y = (R + 2G + B) / 4` around each photosite. The engine takes it from
a 3 × 3 window centred on the photosite (centre weight 2, sides 1, corners ½),
which keeps the centre where it belongs, plus a slight sharpening that matches
the Video Assist.

**Colour.** For each blue and red photosite, its ratio to the green that would
be there, `b = (B − Ĝ) / Ĝ` and `r = (R − Ĝ) / Ĝ`, with Ĝ interpolated (the
engine uses directional cubic interpolation). The decoder expects Cb at the
bottom-right pixel of each quartet and Cr at the top-left, whatever the sensor's
Bayer pattern, and stores **half the difference with the green of that pixel**:

    Cb = ½ · b · G_b        Cr = ½ · r · G_r
    G = Y / (1 + (r + b) / 4)

The decoder does the reverse: it divides each Cb and Cr by the luma of its
pixel, interpolates those ratios between quartets and multiplies them by each
pixel's luma, so the colour follows the detail of the luma. The engine filters
the ratios with a binomial kernel computed at the pixel where the colour is
painted; computing it at the centre of the quartet shifts each plane a different
way and leaves coloured fringes on edges.

## Compression

- **DCT of 8 × 8 blocks** on each plane, as in JPEG.
- **One quantiser per frame**, `q` from 0 to 24, which sets the step for every
  coefficient (`model/quant_steps.json`). A ratio such as 5:1 means picking, for
  each frame, the `q` that makes it fit. The cameras' constant-quality levels
  Q0, Q1, Q3 and Q5 write `q` 0, 2, 9 and 20.
- **Huffman codes**: the example luminance table of the JPEG standard with a
  few changes (`model/vlc-table.json`), checked code by code against the SDK.
- **Coefficient order** that lets a decoder read only part of each block and get
  the image at ½, ¼ or ⅛ resolution without decoding it all.
- **Deblocking**: the decoder always blends each pixel next to a block border
  with its neighbours, with weights 3, 10 and 3 over 16. The camera files show no
  softening at those borders, so the encoder probably pre-compensates for it; the
  engine does, adjusting border pixels so that they land back where they were.
- **Noise added by the decoder**: two 16-bit fields in each frame header, bytes
  20–21 for luma and 22–23 for colour, tell the decoder how much noise to add.
  It hides the flat 8 × 8 blocks that quantisation leaves in smooth areas. The
  added noise is proportional to the value (σ ≈ value / 287 code values in Y,
  value / 600 in each colour plane). The engine writes the values the Video
  Assist would write for each `q`, lowered at low `q`, and can write zero.

## The file

A QuickTime container:

- a video track, `brxq`, one sample per frame;
- a timecode track and, when there is one, a 24-bit PCM audio track;
- `mebx` metadata tracks for motion data, `mogy` and `moac` (gyro and
  accelerometer, three float32 values each), as the cameras write them;
- clip metadata as `moov/meta` keys: `camera_type`, `manufacturer`,
  `viewing_bmdgen` (colour science generation), `analog_gain`,
  `sensor_line_time`, `lens_type`, `camera_id`, `clip_number`, `date_recorded`…

Each video sample is a 1024-byte `bmdf` block of per-frame metadata (frame rate,
ISO, white balance, exposure, gain) followed by the frame itself: `braw`, its
size and a 120-byte header (byte 1 is `q`, bytes 2 and 3 the number of planes
and slices), a slice table at 0x80 and the first slice at 0x1200. The SDK stops
decoding with more than 33 slices in one column strip, and the rows per slice
have to be a multiple of 8, which limits the height unless the slices are
made taller.

## Colour

The SDK develops a clip with the colour science of the camera named in
`camera_type`. For the Sigma fp it has one, because the Video Assist records it:
the engine writes `SIGMA fp` and reproduces the input response of the Video
Assist, measured from a clip recorded on both. The original Blackmagic Pocket
Cinema Camera (2013) recorded its raw as CinemaDNG,
before BRAW existed, and has none, so for it the app
installs a Camera Support profile of its own, in
`/Library/Application Support/Blackmagic Design/Blackmagic RAW/Camera Support`
on macOS and `%ProgramFiles(x86)%\Blackmagic Design\Blackmagic RAW\Camera Support`
on Windows (both need administrator rights), which the SDK loads alongside its
own when the decoder is created, so Resolve must be restarted after installing. Its clips carry
`viewing_gamut` "DCI-P3 D65" and `viewing_gamma` "Linear", so Resolve opens them
in the space where they match the DNG.

## References

- Blackmagic RAW SDK manual (it only decodes):
  https://documents.blackmagicdesign.com/DeveloperManuals/BlackmagicRAW-SDK.pdf
