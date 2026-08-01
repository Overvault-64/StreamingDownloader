"""AnimeUnity.

A Laravel site behind Cloudflare, so every call goes through the cloudscraper
session. Two endpoints are enough: ``/info_api`` for the title and its episode
list (in windows, because a long series does not fit one response), and
``/embed-url/<episode id>``, which answers with the vixcloud URL as plain text.
"""

from __future__ import annotations

import logging

from .. import urls
from ..http import Http
from . import ANIME, Episode, Source, Stream, Title, stream_from_embed

logger = logging.getLogger(__name__)

BASE = "https://www.animeunity.so"
# The /info_api window size the site itself uses.
BATCH = 120


class AnimeUnity(Source):
    label = "AnimeUnity"

    def __init__(self, http: Http):
        super().__init__(http)
        self._info: dict[str, dict] = {}

    def _get(self, path: str, **kwargs):
        return self.http.get(f"{BASE}{path}", cloudflare=True, **kwargs)

    def _info_api(self, identifier: str) -> dict:
        """The title record, fetched once: resolving a link and listing its
        episodes both need it."""
        if identifier not in self._info:
            self._info[identifier] = self.http.json(
                self._get(f"/info_api/{identifier}"), "anime information"
            )
        return self._info[identifier]

    def resolve(self, link: urls.Link) -> tuple[Title, Episode | None]:
        info = self._info_api(link.id)
        # The slug is the last resort for a name: better a filename built from the
        # URL than a failed download over a cosmetic detail.
        name = (
            info.get("title_eng") or info.get("title") or link.slug.replace("-", " ")
        ).strip() or link.id
        title = Title(
            id=link.id,
            name=name,
            media_type=ANIME,
            year=str(info.get("date") or "")[:4] or None,
            slug=link.slug,
        )
        if not link.episode_id:
            return title, None

        # The URL names an episode by id; only the list knows its number.
        episode = next(
            (item for item in self.episodes(title) if item.id == link.episode_id), None
        )
        if episode is None:
            raise RuntimeError(f"Episode {link.episode_id} is not listed for {name}")
        return title, episode

    def episodes(self, title: Title, season: int | None = None) -> list[Episode]:
        count = int(self._info_api(title.id).get("episodes_count") or 0)
        if not count:
            return []

        # Windows are inclusive and the API is happy to start at 0, so the last
        # one can overlap the previous by an episode: collected by id, which
        # drops the duplicate without risking a gap at the end of a long series.
        collected: dict[str, Episode] = {}
        for start in range(0, count + 1, BATCH):
            if start > count:
                break
            end = min(start + BATCH - 1, count)
            response = self._get(
                f"/info_api/{title.id}/0",
                params={"start_range": start, "end_range": end},
            )
            if not response.ok:
                logger.warning(
                    "Episode range %d-%d unavailable (HTTP %d)",
                    start, end, response.status_code,
                )
                continue
            for item in self.http.json(response, "episodes").get("episodes") or []:
                identifier = str(item.get("id"))
                collected.setdefault(identifier, Episode(
                    id=identifier,
                    number=str(item.get("number", "?")),
                    name=item.get("name") or "",
                ))
        return list(collected.values())

    def stream(self, title: Title, episode: Episode | None = None) -> Stream:
        if episode is None:
            raise RuntimeError("AnimeUnity requires an episode")

        # The endpoint answers with the embed URL as plain text.
        embed_url = self.http.text(
            self._get(f"/embed-url/{episode.id}"), "URL embed"
        ).strip()
        if not embed_url.startswith("http"):
            raise RuntimeError(f"Unexpected embed-url response: {embed_url[:80]!r}")
        return stream_from_embed(self.http, embed_url, referer=f"{BASE}/")
