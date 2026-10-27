# Rebuilding the Windows package

Build on Windows x64 with Python 3.11. Run from the repository root in PowerShell:

```powershell
py -3.11 -m venv build\launcher-venv
build\launcher-venv\Scripts\python.exe -m pip install -r packaging\launcher\requirements-build.txt
build\launcher-venv\Scripts\python.exe packaging\launcher\build_windows.py `
  --ffmpeg-root C:\BuildTools\ffmpeg-8.1.1-essentials_build `
  --iscc C:\BuildTools\InnoSetup\ISCC.exe
```

Obtain unmodified FFmpeg/FFprobe 8.1.1 essentials from the Gyan build archive
(https://www.gyan.dev/ffmpeg/builds/) and verify its archive checksum. The root
must contain `bin/ffmpeg.exe`, `bin/ffprobe.exe`, `LICENSE` and `README.txt`.
Use Inno Setup 6.x; this package was built with the official signed 6.2.2
compiler (https://github.com/jrsoftware/issrc/releases/tag/is-6_2_2).
The compiler can be extracted locally with innoextract without installing it.

Outputs: `dist/KinoTime-Windows-1.3.3/KinoTime.exe` (single file),
`KinoTime-Setup-1.3.3.exe` (per-user installer), instructions and checksums.
The installer embeds the directory build in `build/launcher-package/installed`.
Build logs are in `build/launcher-package`; errors stop the build.

The packager uses an explicit entry point and dependency list. It never copies
the repository, database, settings, credentials or user media into the app.
Source included in the release ZIP is likewise an explicit list of launcher
modules. Remote folder navigation and render controls require server 1.3.3.
Long names and multi-computer support require server 1.3.1 (or compatible later
code). Remote folder navigation and explicit render controls require the accompanying server update.

Offline smoke check:

```powershell
dist\KinoTime-Windows-1.3.3\KinoTime.exe --self-test C:\Temp\kinotime-check.json
```

Success is exit code 0 and `"ok": true`. Test both directory and single-file
builds with Python and FFmpeg removed from the child process PATH.
The test imports the Windows UI Automation backend without touching Telegram,
creates synthetic temporary media, prepares source/360p, validates output,
and builds/closes the complete hidden interface. Hardware results are local
to the current computer; AMD/NVIDIA require testing on their respective GPUs.

Installed app data lives outside the installation directory in
`%LOCALAPPDATA%/KinoTime/Launcher`. Source runs retain their former settings
paths. Tokens stay in memory. An update/uninstall does not remove media or
user settings. Binaries are unsigned; signing needs a publisher certificate.

Before making the launcher-only release ZIP, run the complete regression suite
and verify these exact binaries (in a profile without an existing KinoTime
installer registration):

```powershell
.venv\Scripts\python.exe -X utf8 -m unittest discover -s tests -v *> outputs\hardware-regressions.log
.venv\Scripts\python.exe -X utf8 packaging\launcher\verify_windows.py --install
.venv\Scripts\python.exe -X utf8 packaging\launcher\package_update.py --launcher-only
.venv\Scripts\python.exe -X utf8 packaging\launcher\package_release.py --launcher-only
```

The release packager requires successful portable/installer reports tied to the
current binary hashes, plus a passing full test log. It excludes production
settings, video, credentials and the bot database. P106-100 diagnostics are
covered with simulated driver responses; physical GPU support still depends on
the hardware and driver on the destination computer.
