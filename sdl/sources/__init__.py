"""Sources: what a site has to provide for the downloader to work with it.

A source turns a pasted link into a title — plus the episode, when the link names
a single file — lists what a series contains and resolves one item to a
``Stream``. Everything after that (segments, decryption, muxing, filenames) is
shared and lives outside this package.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from .. import hls, urls, vixcloud
from ..http import Http
from ..naming import fmt_ep

MOVIE = "movie"
TV = "tv"
ANIME = "anime"


@dataclass(frozen=True)
class Title:
    id: str
    name: str
    media_type: str            # MOVIE | TV | ANIME
    year: str | None = None
    slug: str = ""


@dataclass(frozen=True)
class Episode:
    id: str
    number: str
    name: str = ""
    season: int = 1

    @property
    def label(self) -> str:
        """The SxxExx form, used both on screen and in the filename."""
        return f"S{self.season:02d}E{fmt_ep(self.number)}"


@dataclass(frozen=True)
class Stream:
    """One resolved video: where the segments are and how to decrypt them."""
    playlist_url: str
    key: bytes
    referer: str
    master: hls.Master


class Source(ABC):
    label: str

    def __init__(self, http: Http):
        self.http = http

    @abstractmethod
    def resolve(self, link: urls.Link) -> tuple[Title, Episode | None]:
        """The title the link names, and the episode when it names one file."""

    def seasons(self, title: Title) -> list[int]:
        """The season numbers on offer. Sources without seasons keep the default."""
        return [1]

    @abstractmethod
    def episodes(self, title: Title, season: int | None = None) -> list[Episode]:
        ...

    @abstractmethod
    def stream(self, title: Title, episode: Episode | None = None) -> Stream:
        ...


def for_link(http: Http, link: urls.Link) -> Source:
    """The source that serves this link, built around the host the link carries.
    Imported here to keep the two site modules out of each other's way."""
    from .animeunity import AnimeUnity
    from .streamingcommunity import StreamingCommunity

    if link.source == urls.ANIMEUNITY:
        return AnimeUnity(http)
    return StreamingCommunity(http, link.host, link.locale)


def stream_from_embed(http: Http, embed_url: str, referer: str) -> Stream:
    """From a vixcloud embed URL to a stream ready to download.

    The two sources differ only in how they hand out that URL; from here on the
    path is identical, so it is written once.
    """
    embed = vixcloud.fetch(http, embed_url, referer=referer)
    playlist_url = embed.playlist_url
    master = hls.parse_master(
        http.fetch_playlist(playlist_url, embed.referer), playlist_url
    )
    return Stream(
        playlist_url=master.video_uri or playlist_url,
        key=vixcloud.fetch_key(http, embed.referer),
        referer=embed.referer,
        master=master,
    )
