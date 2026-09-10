Sparrow includes components maintained by their respective authors. Their
licences and notices continue to apply; Sparrow does not claim ownership of them.

- FFmpeg 8.0.1 and x264 c24e06c: GPL-2.0-or-later, separate command-line
  executables. Exact source archives, licences and the build script are included
  under `sources` in the package; the release workflow keeps them with binaries.
  https://ffmpeg.org/ and https://code.videolan.org/videolan/x264
- FFsubsync 0.5.1: MIT. https://github.com/smacke/ffsubsync
- faster-whisper 1.2.1: MIT. https://github.com/SYSTRAN/faster-whisper
- Whisper base speech model: MIT, converted by SYSTRAN from OpenAI Whisper.
  Pinned model revision is in `sources.json`; source/model information:
  https://huggingface.co/Systran/faster-whisper-base
- WinSW 2.12.0: MIT. https://github.com/winsw/winsw/tree/v2.12.0
- Microsoft Visual C++ x64 Redistributable: Microsoft's runtime terms apply.
  The Windows installer includes the pinned Microsoft installer for the native
  speech-processing runtime. https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist
- HLS.js: BSD-2-Clause. https://github.com/video-dev/hls.js

Python wheel metadata includes licences for the bundled Python dependencies.
`collect_licenses.py` copies their notices into each packaged distribution.
These are bundled libraries and programs; the owner does not run a separate
subtitle application. OpenSubtitles, TMDB and the configured reasoning provider
remain optional network integrations with their own account terms.
