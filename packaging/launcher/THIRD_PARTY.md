# Included components

KinoTime packages Python 3.11, Tcl/Tk, PyInstaller's bootloader, pywinauto,
pywin32, comtypes, six, Pillow and FFmpeg/FFprobe. Their original license
notices are copied to this directory at build time. They retain their own
licenses; packaging does not change them.

- Python and Tcl/Tk: https://www.python.org/downloads/source/
- PyInstaller: https://github.com/pyinstaller/pyinstaller
- pywinauto: https://github.com/pywinauto/pywinauto
- pywin32: https://github.com/mhammond/pywin32
- comtypes: https://github.com/enthought/comtypes
- Pillow: https://github.com/python-pillow/Pillow
- six: https://github.com/benjaminp/six

FFmpeg and FFprobe are the unmodified Gyan FFmpeg 8.1.1 essentials Windows
build, licensed under GPL v3, including its enabled external libraries.
The original LICENSE and README and the exact configuration/version output
are included as FFmpeg-LICENSE.txt, FFmpeg-README.txt and FFmpeg-build.txt.

- Build provider, corresponding source/build information and dependencies:
  https://www.gyan.dev/ffmpeg/builds/
- FFmpeg source for this binary build: https://github.com/FFmpeg/FFmpeg/commit/239f2c733d
- FFmpeg license and source information: https://ffmpeg.org/legal.html

Hardware encoder references:
- Intel Quick Sync: https://ffmpeg.org/ffmpeg-codecs.html#QSV-Encoders
- AMD AMF: https://github.com/GPUOpen-LibrariesAndSDKs/AMF/wiki/AMF-Encoder-Settings-and-Tuning-in-FFmpeg
- NVIDIA NVENC: https://docs.nvidia.com/video-technologies/video-codec-sdk/13.0/ffmpeg-with-nvidia-gpu/index.html

Inno Setup is used only to build the installer. It is not a runtime
dependency: https://jrsoftware.org/isinfo.php

Jackett 0.24.2910 is bundled as a separate, unmodified Windows executable and
its original dependencies. Its GPL-2.0 license is included as Jackett-LICENSE.txt
and also in the jackett directory. Corresponding source:
https://github.com/Jackett/Jackett/archive/refs/tags/v0.24.2910.zip
Original release: https://github.com/Jackett/Jackett/releases/tag/v0.24.2910
