"""Static configuration: paths, limits, language labels.

There is no user configuration by design — no settings file, no first-run
questions, nothing to keep up to date. Paths and network limits are fixed here;
audio and subtitles are asked for the single download at hand.
"""

from __future__ import annotations

import os
from pathlib import Path

# Project root: the directory holding run.bat, so the log and the temporary files
# sit next to the launcher rather than wherever the shell happened to be.
APP_DIR = Path(__file__).resolve().parent.parent
TEMP_DIR = APP_DIR / "tmp"

# Finished files go to the user's Downloads folder. USERPROFILE is native on
# Windows and reaches WSL as the Windows profile when forwarded with
# WSLENV=USERPROFILE/p, so both land in the same folder; elsewhere the home is used.
OUTPUT_ROOT = Path(os.environ.get("USERPROFILE") or Path.home()) / "Downloads" / "StreamingDownloads"

# The CDN accepts sixteen simultaneous segment requests; above that it starts
# rejecting them with HTTP 503. The HTTP connection pool enforces the same cap.
SEGMENT_WORKERS = 16

# Language names for the codes the playlists advertise: ISO 639-2/B, the way
# vixcloud sends them ("fre", "ger", "chi"), plus the 639-2/T spellings in case a
# source uses those instead. English names keep the interface consistently
# localized. This is the only place a language is named; an unknown code is shown
# as it arrives.
LANGUAGES: dict[str, str] = {
    "ita": "Italian",
    "eng": "English",
    "jpn": "Japanese",
    "fre": "French", "fra": "French",
    "spa": "Spanish",
    "ger": "German", "deu": "German",
    "por": "Portuguese",
    "rus": "Russian",
    "chi": "Chinese", "zho": "Chinese",
    "kor": "Korean",
    "ara": "Arabic",
    "dut": "Dutch", "nld": "Dutch",
    "pol": "Polish",
    "tur": "Turkish",
    "gre": "Greek", "ell": "Greek",
    "ukr": "Ukrainian",
    "cze": "Czech", "ces": "Czech",
    "dan": "Danish",
    "fin": "Finnish",
    "heb": "Hebrew",
    "hin": "Hindi",
    "hun": "Hungarian",
    "ind": "Indonesian",
    "may": "Malay", "msa": "Malay",
    "nob": "Norwegian Bokmål", "nor": "Norwegian",
    "rum": "Romanian", "ron": "Romanian",
    "swe": "Swedish",
    "tha": "Thai",
    "vie": "Vietnamese",
    "slo": "Slovak", "slk": "Slovak",
    "slv": "Slovenian",
    "srp": "Serbian",
    "hrv": "Croatian",
    "bul": "Bulgarian",
    "cat": "Catalan",
    "est": "Estonian",
    "lav": "Latvian",
    "lit": "Lithuanian",
    "per": "Persian", "fas": "Persian",
    "tgl": "Tagalog",
}


def label_of(code: str) -> str:
    """Human-readable name for a language code, falling back to the code."""
    return LANGUAGES.get(code, code)
