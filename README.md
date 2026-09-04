# Cambo Dubber

Standalone desktop workspace for subtitle transcription/translation, Khmer
voice generation, timeline editing, dubbing, and video rendering.

The Downloader is a separate project at `F:\Desktop\Download\DramBoxDL`.
This project does not import or store Downloader source code.

Generated dubbed videos, batch renders, and merged videos are saved under
`output/` by default, keeping media output separate from the application source.
You can still choose a different location from a Save dialog.

## Run

Double-click `Launch-Dubber.bat`, or run:

```powershell
.\.venv\Scripts\python.exe .\main.py
```

## AI model environment

Translation defaults to `Best Quality Auto`: Gemini 3.7 Flash with high
thinking first, then OpenAI/SeekAI, and finally Google Web. Source-language
Auto Detect supports Chinese, Japanese, Korean, Thai, Vietnamese, French,
Spanish, Russian, Arabic, Hindi, Indonesian, English, and Khmer.

## Batch dubbing

The Batch Dubbing tab has its own saved translator, source/target language,
voice, music, mix, and cleanup settings. Auto Male/Female Voice uses the gender
returned by Gemini/SeekAI and analyzes the original movie audio when a fallback
translator does not return gender. Each line includes its subtitle duration so
TTS pacing matches the video, and failed episodes do not stop the remaining
queue.

`Auto VoxCPM (Male / Female)` is the default Khmer voice. It maps detected male
characters to the saved VoxCPM male reference and female characters to the
female reference. Mixed-character batches load the VoxCPM model once and switch
reference audio per dialogue line.

NLLB and VoxCPM use the Python executable configured in
`dubber_settings.json`; this machine currently points to:

```text
F:\Desktop\Tool Somrayrerng\.venv\Scripts\python.exe
```

That environment needs compatible builds of PyTorch, Transformers, VoxCPM and
SoundFile. Keep large model caches on drive F.

## Font

The Dubber bundles Noto Sans Khmer under the SIL Open Font License in
`assets/fonts/` so Khmer text renders consistently on every Windows machine.
