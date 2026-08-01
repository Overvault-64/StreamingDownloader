"""HLS: playlist parsing, parallel segment download, AES-CBC decryption.

Nothing here knows about StreamingCommunity or vixcloud — it takes a playlist
URL and produces the decrypted segments of one track.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import m3u8
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from .http import Http, HttpError

logger = logging.getLogger(__name__)

SEGMENT_TIMEOUT = 30
SEGMENT_RETRIES = 3
DOWNLOAD_CHUNK = 64 * 1024
SEGMENT_MANIFEST = "segments.json"
SEGMENT_MANIFEST_VERSION = 1


class Cancelled(RuntimeError):
    """Raised when the user interrupts a download."""


@dataclass(frozen=True)
class Track:
    kind: str          # "audio" | "subtitles"
    code: str          # ISO 639-2 code as advertised, forced- prefix removed
    name: str
    uri: str
    forced: bool = False


@dataclass
class Master:
    """A parsed master playlist: the best video rendition plus its side tracks.

    Tracks are exposed one by one rather than folded into a set of language codes:
    a playlist happily offers "Italian" next to "Italian [Forced]" under the same
    code, and collapsing them would silently pick whichever came first.
    """
    video_uri: str | None
    tracks: list[Track]

    @property
    def audio(self) -> list[Track]:
        return [t for t in self.tracks if t.kind == "audio"]

    @property
    def subtitles(self) -> list[Track]:
        # "auto" is a vixcloud placeholder, not a language.
        return [t for t in self.tracks if t.kind == "subtitles" and t.code != "auto"]


def _base_uri(url: str) -> str:
    return url.rsplit("/", 1)[0] + "/"


def _absolute(item, fallback: str) -> str:
    try:
        return item.absolute_uri or fallback
    except (AttributeError, ValueError):
        return fallback


def parse_master(text: str, url: str) -> Master:
    """Pick the highest-bandwidth rendition and collect the media tracks."""
    playlist = m3u8.M3U8(text, base_uri=_base_uri(url))

    video_uri = None
    if playlist.playlists:
        best = max(playlist.playlists, key=lambda p: p.stream_info.bandwidth or 0)
        video_uri = _absolute(best, best.uri)
        logger.debug(
            "selected rendition: %s (bandwidth=%s)",
            best.stream_info.resolution, best.stream_info.bandwidth,
        )

    tracks = []
    for media in playlist.media:
        if not media.uri:
            continue
        # vixcloud marks a forced subtitle track inside the language token itself
        # and uses both spellings — "forced-ita" and "ita-forced" — while leaving
        # FORCED="NO". Matroska wants the bare code plus the forced disposition,
        # so the two parts are separated here and nowhere else.
        raw = (media.language or media.name or "").strip()
        code = raw.removeprefix("forced-").removesuffix("-forced")
        forced = code != raw or str(media.forced).upper() == "YES"
        tracks.append(Track(
            kind="subtitles" if (media.type or "").upper() == "SUBTITLES" else "audio",
            code=code or "und",
            name=media.name or code or "und",
            uri=_absolute(media, media.uri),
            forced=forced,
        ))
    return Master(video_uri=video_uri, tracks=tracks)


# ── Segments ───────────────────────────────────────────────────────────────────

@dataclass
class _Media:
    segments: list[str]
    iv: bytes | None


def _failure_summary(counts: Counter[str]) -> str:
    return ", ".join(
        f"{reason}: {count}"
        for reason, count in counts.most_common()
    )


def _parse_media(text: str, url: str) -> _Media:
    playlist = m3u8.M3U8(text, base_uri=_base_uri(url))
    segments = [
        _absolute(s, s.uri) for s in playlist.segments
        if s.uri and "vtt" not in s.uri
    ]
    iv = None
    for key in playlist.keys:
        if key is not None and key.iv:
            iv = bytes.fromhex(key.iv.lower().replace("0x", ""))
    return _Media(segments=segments, iv=iv)


def _decrypt(data: bytes, key: bytes, iv: bytes) -> bytes:
    decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    return decryptor.update(data) + decryptor.finalize()


def _sequence_iv(index: int) -> bytes:
    """IV for a playlist that declares a key but no IV (RFC 8216 §5.2)."""
    return index.to_bytes(16, "big")


def _segment_identity(url: str) -> str:
    """Stable playlist identity without short-lived query credentials."""
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def _prepare_temp(temp_dir: Path, segments: list[str]) -> None:
    """Keep complete segments only when the refreshed playlist still matches."""
    temp_dir.mkdir(parents=True, exist_ok=True)
    path = temp_dir / SEGMENT_MANIFEST
    current = {
        "version": SEGMENT_MANIFEST_VERSION,
        "segments": [_segment_identity(url) for url in segments],
    }
    try:
        previous = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        previous = None

    for partial in temp_dir.glob("*.part"):
        partial.unlink(missing_ok=True)
    if previous == current:
        return

    for segment in temp_dir.glob("*.ts"):
        segment.unlink()
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(current), encoding="utf-8")
    temporary.replace(path)


def resolve(http: Http, url: str, referer: str | None) -> _Media:
    """Follow a URL to a media playlist, descending through a master if needed."""
    text = http.fetch_playlist(url, referer)
    media = _parse_media(text, url)
    if media.segments:
        return media

    master = parse_master(text, url)
    if not master.video_uri:
        raise RuntimeError("The playlist contains neither segments nor renditions")
    text = http.fetch_playlist(master.video_uri, referer)
    media = _parse_media(text, master.video_uri)
    if not media.segments:
        raise RuntimeError("The selected rendition contains no segments")
    return media


def download(http: Http, url: str, *, key: bytes | None, referer: str | None,
             temp_dir: Path, workers: int, task) -> list[Path]:
    """Download every segment of ``url`` and return them in playback order.

    ``task`` receives the segment total, byte updates while a response streams,
    and one completed unit after each segment is safely stored.
    """
    media = resolve(http, url, referer)
    total = len(media.segments)
    task.set_total(total)
    _prepare_temp(temp_dir, media.segments)
    worker_count = max(1, workers)
    task.set_status(f"{worker_count} connections")

    # Set the moment the main thread is interrupted, so the worker threads stop
    # fetching instead of draining the whole queue while the pool shuts down.
    stop = threading.Event()
    failures: dict[int, str] = {}
    issue_counts: Counter[str] = Counter()
    issue_lock = threading.Lock()

    def check_cancelled():
        if stop.is_set():
            raise Cancelled()

    def report_issue(reason: str, index: int, attempt: int,
                     recovering: bool) -> None:
        if recovering:
            task.set_status(
                f"recovering {len(failures)} · segment {index + 1}/{total}, "
                f"attempt {attempt + 1}/{SEGMENT_RETRIES}: {reason}"
            )
            return
        with issue_lock:
            issue_counts[reason] += 1
            summary = _failure_summary(issue_counts)
        task.set_status(f"{worker_count} connections · errors {summary}")

    def fetch(
        index: int, *, recovering: bool = False
    ) -> tuple[bytes | None, str | None]:
        reason = "unknown error"
        for attempt in range(SEGMENT_RETRIES):
            check_cancelled()
            try:
                response = http.get(
                    media.segments[index], referer=referer,
                    stream=True, timeout=SEGMENT_TIMEOUT, retries=1,
                )
                if response.status_code == 200:
                    payload = bytearray()
                    with response:
                        for chunk in response.iter_content(DOWNLOAD_CHUNK):
                            check_cancelled()
                            if chunk:
                                payload.extend(chunk)
                                task.advance(0, len(chunk))
                    return bytes(payload), None
                reason = f"HTTP {response.status_code}"
                response.close()
                report_issue(reason, index, attempt, recovering)
                retryable = (
                    response.status_code == 429 or response.status_code >= 500
                )
                if retryable and attempt < SEGMENT_RETRIES - 1:
                    time.sleep(2 ** attempt)
                    continue
                return None, reason
            except Cancelled:
                raise
            except HttpError as exc:
                reason = exc.kind or (
                    f"HTTP {exc.status}" if exc.status is not None else "HTTP error"
                )
                report_issue(reason, index, attempt, recovering)
                logger.debug("segment %d, attempt %d: %s", index, attempt + 1, exc)
            except Exception as exc:  # noqa: BLE001 - one bad segment is recoverable
                reason = type(exc).__name__
                report_issue(reason, index, attempt, recovering)
                logger.debug("segment %d, attempt %d: %s", index, attempt + 1, exc)
            if attempt < SEGMENT_RETRIES - 1:
                time.sleep(1)
        return None, reason

    def store(index: int, payload: bytes) -> None:
        path = temp_dir / f"{index:06d}.ts"
        if key is not None:
            iv = media.iv or _sequence_iv(index)
            payload = _decrypt(payload, key, iv)
        partial = path.with_suffix(".part")
        partial.write_bytes(payload)
        partial.replace(path)

    def worker(index: int) -> tuple[int, str] | None:
        path = temp_dir / f"{index:06d}.ts"
        if path.exists():
            # A previous run of the same job already wrote it.
            task.advance(1)
            return None
        payload, reason = fetch(index)
        if payload is None:
            return index, reason or "unknown error"
        store(index, payload)
        task.advance(1)
        return None

    with ThreadPoolExecutor(max_workers=worker_count) as pool:
        try:
            failures.update(
                failure for failure in pool.map(worker, range(total))
                if failure is not None
            )
        except (KeyboardInterrupt, Cancelled):
            stop.set()
            pool.shutdown(wait=False, cancel_futures=True)
            raise Cancelled() from None

    check_cancelled()

    # Second pass, sequential and slower: a gap in the stream is visible as a
    # skip in the finished file, so it is worth one more attempt.
    if failures:
        failure_counts = Counter(failures.values())
        logger.warning(
            "First pass: %d failed segments (%s)",
            len(failures), _failure_summary(failure_counts),
        )
        task.set_status(
            f"recovering {len(failures)} · {_failure_summary(failure_counts)}"
        )
        for index in sorted(failures):
            check_cancelled()
            payload, reason = fetch(index, recovering=True)
            previous = failures[index]
            if payload is not None:
                store(index, payload)
                task.advance(1)
                del failures[index]
            else:
                failures[index] = reason or "unknown error"
            current = failures.get(index)
            if current != previous:
                failure_counts[previous] -= 1
                if not failure_counts[previous]:
                    del failure_counts[previous]
                if current is not None:
                    failure_counts[current] += 1
            if failures:
                task.set_status(
                    f"recovering {len(failures)} · {_failure_summary(failure_counts)}"
                )
    if failures:
        summary = _failure_summary(failure_counts)
        logger.error(
            "%d segments permanently missing (%s)", len(failures), summary
        )
        raise RuntimeError(
            f"{len(failures)} segments were not downloaded ({summary}); download cancelled"
        )
    task.set_status("")

    # Zero-padded names, so lexicographic order is playback order.
    parts = sorted(temp_dir.glob("*.ts"))
    if not parts:
        raise RuntimeError("No segments downloaded")
    return parts


def download_subtitle(http: Http, track: Track, referer: str | None,
                      destination: Path) -> Path | None:
    """Fetch a WebVTT subtitle. The playlist points at the .vtt as a segment."""
    try:
        text = http.fetch_playlist(track.uri, referer)
        playlist = m3u8.M3U8(text, base_uri=_base_uri(track.uri))
        vtt = next(
            (_absolute(s, s.uri) for s in playlist.segments if s.uri and "vtt" in s.uri),
            None,
        )
        if vtt is None:
            return None
        response = http.get(vtt, referer=referer)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(http.text(response, "subtitle").encode("utf-8"))
        return destination
    except Exception as exc:  # noqa: BLE001 - a missing subtitle is not fatal
        logger.warning("Subtitle %s was not downloaded: %s", track.code, exc)
        return None
