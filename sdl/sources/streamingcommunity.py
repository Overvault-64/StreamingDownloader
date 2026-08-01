"""StreamingCommunity.

Every page of the site is an Inertia.js render carrying its own state in
``div#app[data-page]``: the title, the episodes of the loaded season and, on a
watch page, the URL of the player iframe. Reading that payload out of a plain GET
is all this module does — no JSON API, no asset version, no XSRF token, no
cookies.

The domain and the locale come from the pasted link, the only thing that knows
which mirror is alive today.
"""

from __future__ import annotations

import json
import logging

from bs4 import BeautifulSoup

from .. import urls
from ..http import Http
from . import MOVIE, TV, Episode, Source, Stream, Title, stream_from_embed

logger = logging.getLogger(__name__)

# A page carries ~50 KB of props, most of it interface translations and
# recommendation sliders; only these survive into the cache.
_KEEP = ("title", "episode", "loadedSeason", "embedUrl")


class StreamingCommunity(Source):
    label = "StreamingCommunity"

    def __init__(self, http: Http, domain: str, locale: str = urls.DEFAULT_LOCALE):
        super().__init__(http)
        self.base = f"https://{domain.strip().strip('/')}"
        self.locale = locale or urls.DEFAULT_LOCALE
        self._pages: dict[str, dict] = {}

    # ── Pages ──────────────────────────────────────────────────────────────────

    def _props(self, path: str) -> dict:
        """The props of one page, fetched at most once per run.

        The same page answers two questions — what this title is, and what its
        loaded season contains — so caching by path is what keeps resolving a link
        and then listing its episodes down to a single request.
        """
        if path not in self._pages:
            response = self.http.get(f"{self.base}{path}", referer=f"{self.base}/")
            props = _page_data(self.http.text(response, f"page {path}")).get("props")
            if not isinstance(props, dict) or not isinstance(props.get("title"), dict):
                raise RuntimeError(f"{path}: the page does not contain title data")
            self._pages[path] = {key: props[key] for key in _KEEP if key in props}
        return self._pages[path]

    def _title_path(self, title_id: str, slug: str, season: int | None = None) -> str:
        path = f"/{self.locale}/titles/{title_id}-{slug}"
        return f"{path}/season-{season}" if season else path

    def _watch_path(self, title_id: str, episode_id: str = "") -> str:
        path = f"/{self.locale}/watch/{title_id}"
        return f"{path}?e={episode_id}" if episode_id else path

    # ── Catalogue ──────────────────────────────────────────────────────────────

    def resolve(self, link: urls.Link) -> tuple[Title, Episode | None]:
        if link.kind == urls.TITLE and link.slug:
            props = self._props(self._title_path(link.id, link.slug, link.season))
            return _to_title(props["title"]), None

        # The watch page resolves from the id alone and hands back the slug the
        # season pages need, which is also what makes a bare /titles/<id> work.
        # Its `episode` is the file the page would play: for a series that is the
        # one named by ?e=, or the first episode when nothing was asked for.
        props = self._props(self._watch_path(link.id, link.episode_id))
        title = _to_title(props["title"])
        episode = props.get("episode")
        if link.kind == urls.TITLE or not isinstance(episode, dict):
            return title, None
        return title, _to_episode(episode)

    def seasons(self, title: Title) -> list[int]:
        listed = self._props(self._title_path(title.id, title.slug))["title"].get("seasons")
        return [int(s["number"]) for s in listed or [] if s.get("number")] or [1]

    def episodes(self, title: Title, season: int | None = None) -> list[Episode]:
        loaded = self._props(
            self._title_path(title.id, title.slug, season)
        ).get("loadedSeason") or {}
        number = int(loaded.get("number") or season or 1)
        return [_to_episode(item, number) for item in loaded.get("episodes") or []]

    # ── Stream ─────────────────────────────────────────────────────────────────

    def stream(self, title: Title, episode: Episode | None = None) -> Stream:
        embed_url = self._props(
            self._watch_path(title.id, episode.id if episode else "")
        ).get("embedUrl")
        if not embed_url:
            raise RuntimeError("The player page does not provide an embed URL")

        response = self.http.get(embed_url, referer=f"{self.base}/")
        iframe = BeautifulSoup(
            self.http.text(response, "iframe page"), "html.parser"
        ).find("iframe")
        player_url = iframe.get("src") if iframe else None
        if not player_url:
            raise RuntimeError("The iframe page does not contain a player")
        return stream_from_embed(self.http, player_url, referer=f"{self.base}/")


def _to_title(item: dict) -> Title:
    date = item.get("release_date") or item.get("last_air_date") or ""
    return Title(
        id=str(item["id"]),
        name=item.get("name") or "?",
        media_type=MOVIE if item.get("type") == "movie" else TV,
        year=str(date)[:4] or None,
        slug=item.get("slug") or "",
    )


def _to_episode(item: dict, season: int | None = None) -> Episode:
    """One episode. A title page states the season once, in ``loadedSeason``; a
    watch page carries it on the episode itself."""
    own = item.get("season")
    number = season or (own.get("number") if isinstance(own, dict) else None) or 1
    return Episode(
        id=str(item["id"]),
        number=str(item.get("number", "?")),
        name=item.get("name") or "",
        season=int(number),
    )


def _page_data(html: str) -> dict:
    """The ``div#app[data-page]`` payload every page of the site carries."""
    app = BeautifulSoup(html, "html.parser").find("div", {"id": "app"})
    raw = app.get("data-page") if app else None
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}
