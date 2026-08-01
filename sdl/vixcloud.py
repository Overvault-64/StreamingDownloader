"""vixcloud.co: the player both sources embed.

StreamingCommunity and AnimeUnity differ only in how they hand out the embed
URL. From that URL on the path is identical, and it lives here: read the token
out of the embed page, build the playlist URL, fetch the AES key.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup

from .http import Http

logger = logging.getLogger(__name__)

HOST = "https://vixcloud.co"
KEY_URL = f"{HOST}/storage/enc.key"

# window.video = { id: 12345, ... }. The key is unquoted on the live page, but
# both spellings are accepted: it is a one-character difference away from
# breaking every download, and the page is not ours.
_VIDEO_ID = re.compile(
    r"window\.video\s*=\s*\{[^}]*?[\"']?\bid[\"']?\s*:\s*['\"]?(\d+)['\"]?", re.DOTALL
)
# window.masterPlaylist = { params: { 'token': '...', 'expires': '...' }, ... }
_PARAMS = re.compile(r"[\"']?params[\"']?\s*:\s*\{([^}]*)\}", re.DOTALL)


@dataclass(frozen=True)
class Embed:
    """Everything the playlist and key URLs need, read from one embed page."""
    video_id: str
    token: str
    expires: str
    can_play_fhd: bool
    scz: bool
    lang: str

    @property
    def playlist_url(self) -> str:
        parts = [f"token={self.token}", f"expires={self.expires}"]
        if self.can_play_fhd:
            parts.append("h=1")
        if self.scz:
            parts.append("scz=1")
        parts.append(f"lang={self.lang}")
        return f"{HOST}/playlist/{self.video_id}?" + "&".join(parts)

    @property
    def referer(self) -> str:
        """Referer accepted by /playlist, /storage/enc.key and the CDN.

        The player sends a longer one for episodes, with the title and the
        Sxx:Exx description; measured against the live site it changes nothing —
        same playlist, same segments, same key — so it is not reproduced.
        """
        return f"{HOST}/embed/{self.video_id}?token={self.token}&expires={self.expires}"


def script_of(html: str) -> str:
    """The first <script> in the body, which is where the player state lives."""
    body = BeautifulSoup(html, "html.parser").find("body")
    if body is None:
        raise RuntimeError("Empty embed page")
    script = body.find("script")
    if script is None:
        raise RuntimeError("Video unavailable (no script found on the embed page)")
    return script.text


def parse(script_text: str, embed_url: str) -> Embed:
    video_match = _VIDEO_ID.search(script_text)
    if not video_match:
        raise RuntimeError(f"Video ID not found in the embed ({script_text[:200]!r})")

    params_match = _PARAMS.search(script_text)
    if not params_match:
        raise RuntimeError(f"Token not found in the embed ({script_text[:200]!r})")

    # The block is JS object syntax, close enough to JSON once whitespace, the
    # trailing comma and the single quotes are dealt with.
    raw = params_match.group(1).replace("\n", "").replace(" ", "")
    params = json.loads(("{" + raw + "}").replace(",}", "}").replace("'", '"'))

    query = parse_qs(urlparse(embed_url).query)
    return Embed(
        video_id=video_match.group(1),
        token=str(params["token"]),
        expires=str(params["expires"]),
        can_play_fhd=bool(query.get("canPlayFHD")),
        scz=bool(query.get("scz")),
        lang=query.get("lang", ["it"])[0],
    )


def fetch(http: Http, embed_url: str, referer: str) -> Embed:
    """Load an embed page through cloudscraper and parse it.

    Only this page is Cloudflare-gated; the playlist, the key and the segments
    are fetched with the plain session.
    """
    response = http.get(embed_url, referer=referer, cloudflare=True)
    embed = parse(script_of(http.text(response, "embed page")), embed_url)
    logger.debug("embed %s -> video %s (fhd=%s)", embed_url, embed.video_id,
                 embed.can_play_fhd)
    return embed


def fetch_key(http: Http, referer: str) -> bytes:
    """The AES-128-CBC key for the segments. Same key for video and audio."""
    response = http.get(KEY_URL, referer=referer)
    key = response.content if response.ok else b""
    if len(key) != 16:
        raise RuntimeError(
            f"Invalid decryption key (HTTP {response.status_code}, "
            f"{len(key)} bytes)"
        )
    return key
