# YouTube Downloader

Simple desktop YouTube downloader with a light UI — videos, playlists, quality choice, and download controls.

## Features

- Paste a link → **Fetch** shows title, channel, duration, views, thumbnail
- **Mix links** load the current video only (fast). Use **Fetch playlist** for up to 100 items
- Lightweight item list (no UI freeze on big playlists)
- Quality picker (best / resolution / audio MP3)
- Start, pause/resume, stop + moving progress bar (speed / ETA)
- Saves into `downloads` (changeable) with **Open** folder button
- Bundled ffmpeg via `imageio-ffmpeg` (needed because YouTube splits video/audio)

## Run

1. Install [Python 3.10+](https://www.python.org/downloads/) and tick **Add to PATH**
2. Double-click `run.bat`

## Notes

- Use only for content you have the right to download
- If a download fails, check the popup — the app no longer reports success when nothing was saved
