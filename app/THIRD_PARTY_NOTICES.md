# Third-party components

CDNG to BRAW Converter (cdng2braw) is written by Jose A. Hurtado (www.elcacharrista.com). It includes the following third-party
components. Licence texts supplied by the local installations are retained in the package where available.

## Conversion engine (`Contents/Resources/converter/cdng-convert`)

Statically linked, unmodified:

- **LibRaw 0.22.1** — Copyright LibRaw LLC. Used under the Common Development and Distribution License (CDDL) 1.0,
  one of LibRaw's two licence options (LGPL 2.1 / CDDL 1.0). Source code: https://www.libraw.org/ and
  https://github.com/LibRaw/LibRaw (tag 0.22.1).
- **Little CMS 2** — Copyright Marti Maria Saguer. MIT licence. https://www.littlecms.com/
- **libjpeg-turbo** — Copyright the libjpeg-turbo project and the Independent JPEG Group. IJG licence and
  Modified (3-clause) BSD licence. "This software is based in part on the work of the Independent JPEG Group."
  https://libjpeg-turbo.org/
- **LLVM OpenMP runtime (libomp)** — Copyright the LLVM project. Apache License 2.0 with LLVM Exceptions.
  https://openmp.llvm.org/
- **simdjson** — Copyright the simdjson authors. Apache License 2.0 (also available under MIT). https://simdjson.org/
- **zlib** and **Accelerate** are system libraries of macOS.

## User interface

Qt Core, Gui, Widgets and Network with their platform plugins, dynamically linked. Qt is copyright The Qt Company Ltd.
and other contributors and is used under the GNU Lesser General Public License v3. Qt source and licence information:
https://www.qt.io/licensing/open-source-lgpl-obligations and https://code.qt.io/.

## Not included

No source or binary from cDNG Raw Convert is included. The Blackmagic RAW SDK and DaVinci Resolve are not bundled.
Blackmagic RAW, DaVinci Resolve and Blackmagic Video Assist are trademarks of Blackmagic Design; SIGMA and fp are
trademarks of SIGMA Corporation. This application is not affiliated with or endorsed by either company.

## Source

CDNG to BRAW Converter is free software under the GNU General Public License, version 3 or later. Its source code,
including the scripts that build these packages, is at https://github.com/jahurtado/cdng2braw.
