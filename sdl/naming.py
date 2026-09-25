"""The output layout and the filenames in it.

One implementation, used for films, episodes and anime alike, so the layout
cannot drift between them:

    StreamingDownloads/Film/Titolo (2021)/Titolo (2021).mp4
    StreamingDownloads/Serie TV/Titolo (2019)/Season 01/Titolo S01E01.mkv
    StreamingDownloads/Anime/Titolo (2013)/Season 01/Titolo S01E07.mkv

The root is ``config.OUTPUT_ROOT``: there is nothing to configure, so nothing
can point it somewhere unexpected.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from .config import OUTPUT_ROOT

if TYPE_CHECKING:  # only for the annotations; importing at runtime would be a cycle
    from .sources import Episode, Title

# One directory per content type, matching how Jellyfin expects libraries to be
# separated.
TYPE_DIRS = {"movie": "Film", "tv": "Serie TV", "anime": "Anime"}

# Windows forbids \ / : * ? " < > | and control characters; POSIX only forbids /
# and NUL. The filter is applied everywhere regardless: titles come from a
# third-party site and end up in filesystem paths, so a title containing "../"
# must not become a traversal just because the host happens to be Linux.
_FORBIDDEN = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
MAX_NAME = 150


def safe_name(name: str) -> str:
    """A title turned into a usable path component."""
    cleaned = _FORBIDDEN.sub("", str(name).replace("+", " ").replace(",", ""))
    cleaned = cleaned.strip().strip(".")
    # "." and ".." survive the character filter but are not usable names.
    if not cleaned or set(cleaned) <= {"."}:
        return "senza-nome"
    return cleaned[:MAX_NAME]


def fmt_ep(number) -> str:
    """Zero-pad the integer part of an episode number: 7 -> '07', 7.5 -> '07.5'."""
    head, _, tail = str(number).partition(".")
    head = head.zfill(2)
    return f"{head}.{tail}" if tail else head


def output_path(title: Title, episode: Episode | None) -> Path:
    """Destination for one downloaded file, extension included (.mp4).

    ``download`` swaps the extension for .mkv when extra tracks end up muxed in;
    that is the only place allowed to change it.
    """
    name = safe_name(title.name)
    folder_name = f"{name} ({title.year})" if title.year else name
    folder = OUTPUT_ROOT / TYPE_DIRS.get(title.media_type, "Altro") / folder_name

    if episode is None:
        return folder / f"{folder_name}.mp4"
    # Anime have no season on the source, but a Jellyfin library still wants one.
    return folder / f"Season {episode.season:02d}" / f"{name} {episode.label}.mp4"
