"""The one HTTP client.

Every outbound request in the package goes through here, which is what keeps a
single copy of the three quirks this scraping needs:

* **Cloudflare.** vixcloud serves the ``/embed/`` page behind a challenge, and so
  does AnimeUnity, so a plain GET intermittently returns a 403 challenge page.
  Those requests ask for cloudscraper. Everything downstream (``/playlist``,
  ``/storage/enc.key`` and the CDN segments) is not gated, so it uses the plain
  session — and a 403 from a request that did *not* ask for cloudscraper is
  retried through it once anyway, in case the site extends the challenge.
* **``?b=1``.** Episode playlist URLs sometimes answer 403 until the parameter is
  appended. ``fetch_playlist`` is the only place that knows this.
* **Transient 5xx / connection resets.** Retried with exponential backoff.
"""

from __future__ import annotations

import logging
import random
import time
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import requests

logger = logging.getLogger(__name__)

TIMEOUT = 20
RETRIES = 3
BACKOFF = 2.0

# A small static pool. The point is to look like a browser, not to be unique per
# request: building a fresh generator on every call (as the original panel did)
# costs far more than it buys.
USER_AGENTS = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36",
)


class HttpError(RuntimeError):
    def __init__(self, message: str, status: int | None = None,
                 kind: str | None = None):
        super().__init__(message)
        self.status = status
        self.kind = kind


def with_param(url: str, key: str, value: str) -> str:
    """Return ``url`` with ``key=value`` added, preserving the existing query."""
    parts = urlparse(url)
    query = parse_qsl(parts.query, keep_blank_values=True)
    query.append((key, value))
    return urlunparse(parts._replace(query=urlencode(query)))


class Http:
    """Shared session state for one run of the application."""

    def __init__(self):
        self.user_agent = random.choice(USER_AGENTS)
        self.session = requests.Session()
        self._scraper = None

    def _cloudscraper(self):
        """Lazily built: importing cloudscraper is not free and the site pages
        themselves are not always gated."""
        if self._scraper is None:
            import cloudscraper

            self._scraper = cloudscraper.create_scraper(
                browser={"browser": "chrome", "platform": "windows", "desktop": True}
            )
        return self._scraper

    # ── Requests ───────────────────────────────────────────────────────────────

    def get(
        self,
        url: str,
        *,
        referer: str | None = None,
        params: dict | None = None,
        cloudflare: bool = False,
        stream: bool = False,
        timeout: int = TIMEOUT,
        retries: int = RETRIES,
    ) -> requests.Response:
        """GET with retries. Returns the response even when it is not ok.

        A streamed response must be closed by the caller.
        """
        session = self._cloudscraper() if cloudflare else self.session
        escalated = cloudflare
        headers = {"user-agent": self.user_agent}
        if referer:
            headers["referer"] = referer
        last_error: Exception | None = None

        attempt = 0
        while attempt < retries:
            try:
                response = session.get(
                    url, headers=headers, params=params, stream=stream,
                    timeout=timeout,
                )
            except requests.RequestException as exc:
                last_error = exc
                attempt += 1
                if attempt == retries:
                    break
                time.sleep(BACKOFF ** (attempt - 1))
                continue

            if response.ok:
                return response

            if response.status_code == 403 and not escalated:
                # The site started challenging a URL that used to be open.
                logger.debug("403 on %s, retrying through cloudscraper", url)
                response.close()
                session = self._cloudscraper()
                escalated = True
                # Escalation is part of the same attempt. In particular, callers
                # using retries=1 must still make the promised scraper request.
                continue

            attempt += 1
            if response.status_code >= 500 and attempt < retries:
                response.close()
                time.sleep(BACKOFF ** (attempt - 1))
                continue

            return response

        kind = (
            "timeout" if isinstance(last_error, requests.Timeout)
            else "network error"
        )
        raise HttpError(f"{url} is unreachable: {last_error}", kind=kind)

    # ── Response helpers ───────────────────────────────────────────────────────

    @staticmethod
    def text(response: requests.Response, what: str = "resource") -> str:
        if not response.ok:
            raise HttpError(f"{what}: HTTP {response.status_code}", response.status_code)
        return response.text

    @staticmethod
    def json(response: requests.Response, what: str = "resource"):
        if not response.ok:
            raise HttpError(f"{what}: HTTP {response.status_code}", response.status_code)
        try:
            return response.json()
        except ValueError as exc:
            raise HttpError(
                f"{what}: non-JSON response ({response.text[:120]!r})"
            ) from exc

    # ── Playlists ──────────────────────────────────────────────────────────────

    def fetch_playlist(self, url: str, referer: str | None = None) -> str:
        """Fetch an M3U8 playlist, applying the ``?b=1`` workaround on 403.

        The only implementation of that fallback: master playlists, audio
        renditions and subtitle playlists all come through here.
        """
        response = self.get(url, referer=referer)
        if response.status_code == 403:
            logger.debug("playlist 403, retrying with b=1: %s", url)
            response = self.get(with_param(url, "b", "1"), referer=referer)
        return self.text(response, "playlist M3U8")
