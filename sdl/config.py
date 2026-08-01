"""Static configuration: paths, limits, language labels.

There is no user configuration by design — no settings file, no first-run
questions, nothing to keep up to date. Paths and network limits are fixed here;
audio and subtitles are asked for the single download at hand.
"""

from __future__ import annotations

from pathlib import Path

# Project root: the directory holding run.bat, so downloads and the log sit next
# to the launcher rather than wherever the shell happened to be.
APP_DIR = Path(__file__).resolve().parent.parent
TEMP_DIR = APP_DIR / "tmp"

# The source CDN returns 503 for most segments under high concurrency. Four
# connections preserve parallel throughput without forcing a mostly-sequential
# recovery pass afterward.
SEGMENT_WORKERS = 4

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
