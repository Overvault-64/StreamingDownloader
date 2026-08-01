"""The download pipeline: from a chosen item to a file on disk.

One implementation for films, episodes and anime — they differ only in the
``Stream`` their source produces and in the destination path, both decided
before getting here.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from shutil import rmtree
from uuid import uuid4

from . import ffmpeg, hls, naming, urls
from .config import SEGMENT_WORKERS, TEMP_DIR, label_of
from .http import Http
from .sources import Episode, Source, Stream, Title

logger = logging.getLogger(__name__)

MANIFEST = "session.json"
MANIFEST_VERSION = 2

SCOPE_MOVIE = "movie"
SCOPE_EPISODE = "episode"
SCOPE_SEASON = "season"
SCOPE_SERIES = "series"
SCOPES = {SCOPE_MOVIE, SCOPE_EPISODE, SCOPE_SEASON, SCOPE_SERIES}


class MissingAudioTrack(RuntimeError):
    """The chosen audio track is not on offer for this episode.

    Never substituted with another one: a file that looks right and plays in the
    wrong language is worse than a download that stops and says so.
    """


@dataclass(frozen=True)
class Job:
    """One file to produce."""
    title: Title
    episode: Episode | None
    # Tracks are named, not indexed: the selection is made once on the first item
    # of a batch, while every episode resolves its own playlist with its own URIs,
    # so the advertised name is the only handle that survives.
    audio: str | None = None
    subtitle: str | None = None

    @property
    def label(self) -> str:
        if self.episode is None:
            return self.title.name
        return f"{self.title.name} {self.episode.label}"

    @property
    def destination(self) -> Path:
        """Always .mp4; ``execute`` swaps it for .mkv when tracks are muxed in."""
        return naming.output_path(self.title, self.episode)


@dataclass(frozen=True)
class Session:
    """A complete user selection, including its next item and active workspace."""
    path: Path
    link: urls.Link
    title: Title
    episodes: tuple[Episode | None, ...]
    audio: str | None
    subtitle: str | None
    scope: str
    current: int = 0

    @property
    def total(self) -> int:
        return len(self.episodes)

    @property
    def job(self) -> Job:
        return Job(
            title=self.title,
            episode=self.episodes[self.current],
            audio=self.audio,
            subtitle=self.subtitle,
        )

    @property
    def active_path(self) -> Path:
        return self.path / "active"

    @property
    def saved_segments(self) -> int:
        return sum(1 for _ in self.active_path.rglob("*.ts"))


def _manifest_data(session: Session) -> dict:
    return {
        "version": MANIFEST_VERSION,
        "link": asdict(session.link),
        "title": asdict(session.title),
        "episodes": [asdict(item) if item else None for item in session.episodes],
        "audio": session.audio,
        "subtitle": session.subtitle,
        "scope": session.scope,
        "current": session.current,
    }


def _valid_session(session: Session) -> bool:
    if (
        session.scope not in SCOPES
        or not session.episodes
        or not 0 <= session.current < session.total
    ):
        return False
    if session.scope == SCOPE_MOVIE:
        return session.episodes == (None,)
    episodes = tuple(item for item in session.episodes if item is not None)
    if len(episodes) != session.total:
        return False
    if session.scope == SCOPE_EPISODE:
        return session.total == 1
    return session.scope != SCOPE_SEASON or len({item.season for item in episodes}) == 1


def _write_manifest(session: Session) -> None:
    path = session.path / MANIFEST
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(_manifest_data(session), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


def _load_session(path: Path) -> Session | None:
    try:
        data = json.loads((path / MANIFEST).read_text(encoding="utf-8"))
        if data.get("version") != MANIFEST_VERSION:
            return None
        episodes = tuple(
            Episode(**item) if item else None for item in data["episodes"]
        )
        session = Session(
            path=path,
            link=urls.Link(**data["link"]),
            title=Title(**data["title"]),
            episodes=episodes,
            audio=data.get("audio"),
            subtitle=data.get("subtitle"),
            scope=data["scope"],
            current=int(data["current"]),
        )
        if not _valid_session(session):
            return None
        return session
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        logger.warning("Invalid interrupted-session manifest in %s", path)
        return None


def interrupted() -> list[Session]:
    """Recoverable sessions currently present under ``tmp``."""
    if not TEMP_DIR.is_dir():
        return []
    found = [
        item for path in TEMP_DIR.iterdir()
        if path.is_dir() and (item := _load_session(path)) is not None
    ]
    return sorted(found, key=lambda item: item.path.stat().st_mtime)


def has_temporary_data() -> bool:
    try:
        return next(TEMP_DIR.iterdir(), None) is not None
    except OSError:
        return False


def start_session(link: urls.Link, title: Title,
                  episodes: list[Episode | None], audio: str | None,
                  subtitle: str | None, scope: str) -> Session:
    """Publish the complete selection before its first item starts."""
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    identifier = uuid4().hex
    staging = TEMP_DIR / f".{identifier}.tmp"
    path = TEMP_DIR / identifier
    staging.mkdir()
    session = Session(
        path=staging,
        link=link,
        title=title,
        episodes=tuple(episodes),
        audio=audio,
        subtitle=subtitle,
        scope=scope,
    )
    if not _valid_session(session):
        rmtree(staging)
        raise ValueError("The download selection does not match its scope")
    try:
        _write_manifest(session)
        staging.replace(path)
    except BaseException:
        rmtree(staging, ignore_errors=True)
        raise
    return replace(session, path=path)


def advance(session: Session) -> Session | None:
    """Commit completion of the current item and return the next session state."""
    if session.active_path.exists():
        rmtree(session.active_path)
    if session.current + 1 == session.total:
        rmtree(session.path)
        return None
    updated = replace(session, current=session.current + 1)
    _write_manifest(updated)
    return updated


def discard_session(session: Session) -> None:
    """Discard the active partial item and every not-yet-started queue entry."""
    if session.active_path.exists():
        _discard(session.job.destination)
    rmtree(session.path)


def discard_all_interrupted() -> None:
    """Remove every partial output described by ``tmp``, then ``tmp`` itself."""
    for session in interrupted():
        discard_session(session)
    if TEMP_DIR.exists():
        rmtree(TEMP_DIR)


def existing_output(destination: Path) -> Path | None:
    """Whether this job's file is already there, under either extension."""
    for candidate in (destination, destination.with_suffix(".mkv")):
        if candidate.exists():
            return candidate
    return None


def _match(tracks: list[hls.Track], wanted: str | None) -> hls.Track | None:
    """Find the chosen track again in this episode's playlist."""
    return next((t for t in tracks if t.name == wanted), None) if wanted else None


def _fetch(http: Http, reporter, stream: Stream, uri: str, *, label: str,
           temp_dir: Path, destination: Path) -> Path:
    """One track from its playlist to a finished mp4, in the two phases the
    interface reports: the segments, then the remux that streams them out."""
    with reporter.task(label) as task:
        parts = hls.download(
            http, uri, key=stream.key, referer=stream.referer,
            temp_dir=temp_dir, workers=SEGMENT_WORKERS, task=task,
        )
    with reporter.task(f"{label} → mp4") as task:
        return ffmpeg.join(parts, destination, task)


def execute(http: Http, source: Source, job: Job, reporter,
            temp: Path) -> Path:
    """Download one video and return the final path. Raises on failure."""
    temp.mkdir(parents=True, exist_ok=True)
    video = job.destination
    try:
        # Resolved now, not when the title was picked: the playlist token is
        # short-lived, so both queued and resumed jobs receive fresh URLs.
        with reporter.task("preparing stream"):
            stream = source.stream(job.title, job.episode)

        audio = _match(stream.master.audio, job.audio)
        if job.audio and audio is None:
            raise MissingAudioTrack(
                f"audio track «{job.audio}» is unavailable (available: "
                f"{', '.join(t.name for t in stream.master.audio) or 'none'})"
            )
        subtitle = _match(stream.master.subtitles, job.subtitle)

        _fetch(http, reporter, stream, stream.playlist_url, label="video",
               temp_dir=temp / "video", destination=video)

        audio_file = None
        if audio is not None:
            track = _fetch(
                http, reporter, stream, audio.uri,
                label=f"audio {label_of(audio.code)}",
                temp_dir=temp / "audio", destination=temp / "audio.mp4",
            )
            audio_file = (track, audio.code)

        subtitle_file = None
        if subtitle is not None:
            with reporter.task(f"subtitles {label_of(subtitle.code)}"):
                path = hls.download_subtitle(
                    http, subtitle, stream.referer,
                    temp / f"sub_{subtitle.code}.vtt",
                )
            if path is None:
                reporter.note(
                    f"  [yellow]{label_of(subtitle.code)} subtitles unavailable"
                )
            else:
                subtitle_file = (path, subtitle.code, subtitle.forced)

        final = video
        if audio_file or subtitle_file:
            # Matroska is the only container that carries a separate audio track
            # plus WebVTT subtitles without re-encoding anything.
            final = video.with_suffix(".mkv")
            with reporter.task("mux mkv"):
                ffmpeg.mux(video, audio_file, subtitle_file, final)
            video.unlink(missing_ok=True)
        return final
    except BaseException:
        # Includes KeyboardInterrupt: a half-written file must not be left
        # looking like a finished download.
        _discard(video)
        raise
    finally:
        rmtree(temp, ignore_errors=True)


def _discard(destination: Path) -> None:
    """Remove partial output and any directory it left behind empty."""
    for candidate in (destination, destination.with_suffix(".mkv")):
        try:
            candidate.unlink(missing_ok=True)
        except OSError:
            logger.debug("Unable to remove %s", candidate)
    for folder in (destination.parent, destination.parent.parent):
        try:
            folder.rmdir()
        except OSError:
            break  # not empty, or not ours: stop climbing
