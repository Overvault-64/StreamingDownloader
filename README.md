# StreamingDownloader

StreamingDownloader is a terminal application for downloading films, TV series,
and anime from StreamingCommunity and AnimeUnity. Paste a supported page URL,
choose the desired episodes and tracks, and the application writes media files to
the local `downloads` directory.

Use this software only for content that you are authorized to download. You are
responsible for complying with the terms of the source websites and the laws that
apply in your jurisdiction.

## Requirements

- Python 3.11 or later
- [FFmpeg](https://ffmpeg.org/) available on `PATH`
- Windows for the included `run.bat` launcher; the Python module can also be run
  manually on other operating systems

Install FFmpeg on Windows with one of these commands:

```powershell
winget install Gyan.FFmpeg
# or
choco install ffmpeg
```

Restart the terminal after installation so that the updated `PATH` is available.

## Installation and usage

On Windows, run:

```bat
run.bat
```

The launcher creates a local `.venv` environment and installs the packages from
[`requirements.txt`](requirements.txt) on its first run. You can also pass the
URL directly:

```bat
run.bat "https://<current-mirror>/it/watch/9423"
```

To set up and run the application manually:

```console
python -m venv .venv
```

On Windows:

```console
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m sdl
```

On Linux or macOS:

```console
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m sdl
```

Pass a URL after `sdl` to skip the initial URL prompt. After a download finishes,
the application asks whether to process another URL.

## Supported URLs

| URL pattern | Behavior |
| --- | --- |
| `<mirror>/<locale>/titles/<id>-<slug>` | Downloads a film or opens the series selection menu. |
| `<mirror>/<locale>/titles/<id>-<slug>/season-2` | Opens the selection menu with season 2 already selected. |
| `<mirror>/<locale>/watch/<id>` | Downloads the film or the first episode selected by the page. |
| `<mirror>/<locale>/watch/<id>?e=<episode-id>` | Downloads the specified episode. |
| `animeunity.so/anime/<id>-<slug>` | Opens the anime episode selection menu. |
| `animeunity.so/anime/<id>-<slug>/<episode-id>` | Downloads the specified anime episode. |

The `http://` or `https://` scheme may be omitted. StreamingCommunity links may
also omit the locale, which defaults to `it`, and a season may be written as
either `/season-2` or `/2`.

StreamingCommunity changes mirrors regularly. Its host is taken from the pasted
URL, so use a current link that already works in your browser.

## Download behavior

- Films and URLs that identify a single episode do not show content-selection
  prompts.
- Series can be downloaded in full, by season, or by episode.
- Available audio and subtitle tracks are read from the selected content. Track
  choices are made once and applied to the complete batch.
- If the selected audio track is unavailable for a later episode, that episode
  is skipped instead of being downloaded with a different track.
- Existing `.mp4` or `.mkv` files are skipped, so rerunning a batch downloads only
  missing items.
- Pressing `Ctrl+C` stops the active download and removes its partial output and
  temporary files.
- Closing the terminal or forcibly stopping the process preserves the complete
  selection, including the active item's segments and the remaining batch. On
  the next launch, the application identifies each interrupted movie, episode,
  season, or series so one session can be resumed, or all can be deleted before
  starting a new URL.
- A resumed season or series continues through all remaining episodes. After it
  finishes, any other interrupted sessions are offered again.

## Output

Files are written under `downloads` next to the launcher:

```text
downloads/
├── Film/
│   └── Supergirl (2026)/Supergirl (2026).mkv
├── Serie TV/
│   └── Batman Caped Crusader (2024)/Season 01/Batman Caped Crusader S01E01.mkv
└── Anime/
    └── Attack on Titan (2013)/Season 01/Attack on Titan S01E02.mkv
```

The application produces an `.mp4` when the selected stream is self-contained
and uses `.mkv` when separate audio or subtitles must be included. Included
tracks receive language metadata, and forced subtitles are marked accordingly.

## Troubleshooting

### FFmpeg is not found

Confirm that `ffmpeg -version` works in a new terminal. If it does not, install
FFmpeg or add its executable directory to `PATH`.

### A source URL no longer resolves

First copy a fresh URL from a working browser session. These services can change
their pages or player responses without notice, which may temporarily break
resolution or downloading.

### Enable diagnostic logging

In Command Prompt:

```bat
set SDL_DEBUG=1
run.bat
```

In PowerShell:

```powershell
$env:SDL_DEBUG = "1"
.\run.bat
```

The application writes `sdl.log` in the project directory and replaces it on
each debug run. The log can contain request details, so review it before sharing
it publicly.
