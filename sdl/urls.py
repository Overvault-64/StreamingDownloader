"""Recognising a pasted URL.

The link is the whole input: it carries the host to talk to, the locale segment
the site requires in its paths, the identifiers and — when it points at a single
file — the episode. Nothing is configured and nothing is remembered, so a link
copied from a working browser tab is by definition current.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

STREAMINGCOMMUNITY = "streamingcommunity"
ANIMEUNITY = "animeunity"

TITLE = "title"    # /it/titles/9423-batman-caped-crusader[/season-2]
WATCH = "watch"    # /it/watch/9423[?e=73269]
ANIME = "anime"    # /anime/2616-attack-on-titan[/45578]

DEFAULT_LOCALE = "it"

EXAMPLES = (
    "  https://<domain>/it/titles/9423-batman-caped-crusader\n"
    "  https://<domain>/it/titles/9423-batman-caped-crusader/season-2\n"
    "  https://<domain>/it/watch/9423             (also with ?e=<episode-id>)\n"
    "  https://www.animeunity.so/anime/2616-attack-on-titan\n"
    "  https://www.animeunity.so/anime/2616-attack-on-titan/45578"
)

# The locale segment and the slug are mandatory on the live site (both answer 404
# without them); they are optional here only so a hand-trimmed link still parses —
# requests are rebuilt from `locale`, and a missing slug is recovered from the
# watch page.
_SC_TITLE = re.compile(
    r"^/(?:([a-z]{2})/)?titles/(\d+)(?:-([^/]+))?(?:/(?:season-)?(\d+))?/?$"
)
_SC_WATCH = re.compile(r"^/(?:([a-z]{2})/)?watch/(\d+)/?$")
_AU_ANIME = re.compile(r"^/anime/(\d+)(?:-([^/]+))?(?:/(\d+))?/?$")


@dataclass(frozen=True)
class Link:
    source: str
    kind: str
    host: str
    id: str                    # AnimeUnity ids carry the slug: "2616-attack-on-titan"
    slug: str = ""
    locale: str = DEFAULT_LOCALE
    season: int | None = None  # a /season-N link has already chosen one
    episode_id: str = ""       # set when the URL names a single file


def parse(raw: str) -> Link | None:
    """Classify a URL, or return None when it is not one we can resolve."""
    text = (raw or "").strip().strip('"').strip("'")
    if not text:
        return None
    if "://" not in text:
        # People paste "streamingcommunityz.support/it/watch/9423" just as often.
        text = "https://" + text

    parsed = urlparse(text)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return None
    host = parsed.hostname.lower()
    path = parsed.path or "/"

    if ANIMEUNITY in host:
        match = _AU_ANIME.match(path)
        if not match:
            return None
        identifier, slug = match.group(1), match.group(2) or ""
        return Link(
            source=ANIMEUNITY,
            kind=ANIME,
            host=host,
            id=f"{identifier}-{slug}" if slug else identifier,
            slug=slug,
            episode_id=match.group(3) or "",
        )

    # Any other host is treated as a StreamingCommunity mirror: the domain
    # changes constantly, so matching it against a known list would go stale.
    match = _SC_TITLE.match(path)
    if match:
        return Link(
            source=STREAMINGCOMMUNITY,
            kind=TITLE,
            host=host,
            id=match.group(2),
            slug=match.group(3) or "",
            locale=match.group(1) or DEFAULT_LOCALE,
            season=int(match.group(4)) if match.group(4) else None,
        )

    match = _SC_WATCH.match(path)
    if match:
        # ?e=<id> is how the site itself points at one episode of a series. Only
        # a numeric value is kept: it ends up in a URL we request.
        episode = (parse_qs(parsed.query).get("e") or [""])[0].strip()
        return Link(
            source=STREAMINGCOMMUNITY,
            kind=WATCH,
            host=host,
            id=match.group(2),
            locale=match.group(1) or DEFAULT_LOCALE,
            episode_id=episode if episode.isdigit() else "",
        )
    return None
