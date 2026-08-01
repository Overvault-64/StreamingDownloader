"""The terminal interface.

All of the interaction lives here; the modules underneath know nothing about
questionary, rich or the flow. One flow only: a URL in, files on disk, and in
between just the questions the link cannot answer — which season, which episode,
which language. Every prompt returns ``None`` when the user presses Esc, which
always means "back to the URL".
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from time import monotonic

import questionary
from questionary import Choice
from rich.console import Console

from . import download, ffmpeg, naming, urls
from .download import MissingAudioTrack
from .hls import Cancelled, Track
from .http import Http, HttpError
from .progress import Reporter
from .sources import MOVIE, Episode, Source, Title, for_link

logger = logging.getLogger(__name__)

# questionary answers None on Esc, so "no subtitles" needs a value of its own.
NO_SUBTITLE = "__none__"
BACK = "__back__"
DELETE_INTERRUPTED = "__delete_interrupted__"

STYLE = questionary.Style([
    ("qmark", "fg:#e02444 bold"),
    ("question", "bold"),
    ("answer", "fg:#e02444"),
    ("pointer", "fg:#e02444 bold"),
    ("highlighted", "fg:#e02444 bold"),
    ("selected", "fg:#0ec47c"),
    ("separator", "fg:#505060"),
    ("instruction", "fg:#808088"),
])


@dataclass(frozen=True)
class _Targets:
    scope: str
    items: tuple[Episode | None, ...]


# ── Small prompt wrappers ──────────────────────────────────────────────────────

def _select(message: str, choices: list):
    return questionary.select(message, choices=choices, style=STYLE, qmark="?").ask()


def _text(message: str):
    return questionary.text(message, style=STYLE, qmark="?").ask()


def _attempt(console: Console, what: str, action):
    """Run one call to a source under a spinner, reporting failure as a message.

    Sources raise; the interface never lets an exception reach the user as a
    traceback, and every screen treats None the same way.
    """
    with console.status(f"{what}..."):
        try:
            return action()
        except (HttpError, RuntimeError, KeyError, ValueError) as exc:
            console.print(f"[red]{what} failed:[/] {exc}")
            return None


# ── Selection ──────────────────────────────────────────────────────────────────

def _choose_season(seasons: list[int]) -> int | str | None:
    choices = [Choice(f"Season {n}", value=n) for n in seasons]
    choices.append(Choice("← What to download", value=BACK))
    return _select("Season:", choices)


def _episodes(console: Console, source: Source, title: Title,
              season: int | None) -> list[Episode] | None:
    episodes = _attempt(
        console, "Loading episodes", lambda: source.episodes(title, season)
    )
    if episodes is None:
        return None
    if not episodes:
        console.print("[yellow]No episodes available.[/]")
        return None
    return episodes


def _choose_targets(console: Console, source: Source, link: urls.Link, title: Title,
                    episode: Episode | None) -> _Targets | None:
    """What to download: one entry per file, ``None`` meaning the title itself.

    Returns None when there is nothing to do, including when the user backs out.
    """
    if episode is not None:
        return _Targets(download.SCOPE_EPISODE, (episode,))
    if title.media_type == MOVIE:
        return _Targets(download.SCOPE_MOVIE, (None,))

    seasons = _attempt(console, "Loading seasons", lambda: source.seasons(title))
    if seasons is None:
        return None
    # A /season-N link has already answered the season question.
    fixed = link.season if link.season in seasons else None

    while True:
        scope = _select("What would you like to download?", [
            Choice(
                f"Download all of season {fixed}" if fixed else "Download the entire series",
                value="all",
            ),
            # Pointless when there is one season, or when the link named one.
            *([Choice("Download one season", value="season")]
              if len(seasons) > 1 and not fixed else []),
            Choice("Download one episode", value="episode"),
        ])
        if scope is None:
            return None

        if scope == "all":
            if fixed:
                found = _episodes(console, source, title, fixed)
                return (
                    None if found is None
                    else _Targets(download.SCOPE_SEASON, tuple(found))
                )
            targets: list[Episode | None] = []
            for number in seasons:
                found = _episodes(console, source, title, number)
                if found is None:
                    return None
                targets.extend(found)
            return _Targets(download.SCOPE_SERIES, tuple(targets))

        if scope == "season":
            number = _choose_season(seasons)
            if number == BACK:
                continue
            if number is None:
                return None
            found = _episodes(console, source, title, number)
            return (
                None if found is None
                else _Targets(download.SCOPE_SEASON, tuple(found))
            )

        # One episode: pick the season first, and keep it reachable from the list.
        pick_season = not fixed and len(seasons) > 1
        while True:
            number = _choose_season(seasons) if pick_season else (fixed or seasons[0])
            if number is None:
                return None
            if number == BACK:
                break
            episodes = _episodes(console, source, title, number)
            if episodes is None:
                return None
            choices = [
                Choice(f"{item.label}  {item.name}".rstrip(), value=item)
                for item in episodes
            ]
            if pick_season:
                choices.append(Choice("← Seasons", value=BACK))
            picked = _select("Episode:", choices)
            if picked is None:
                return None
            if picked != BACK:
                return _Targets(download.SCOPE_EPISODE, (picked,))


def _track_choices(tracks: list[Track]) -> list[Choice]:
    """One entry per track, preserving the name advertised by the playlist."""
    return [Choice(track.name, value=track.name) for track in tracks]


def _choose_tracks(console: Console, source: Source, title: Title,
                   sample: Episode | None) -> tuple[str | None, str | None] | None:
    """Which audio and subtitle track to take, asked once for the whole download.

    The catalogue comes from the real playlist of the first item, so only what is
    on offer is offered — and for a source that ships a single embedded track
    (every anime, some films) nothing is asked at all.
    """
    stream = _attempt(
        console, "Loading tracks", lambda: source.stream(title, sample)
    )
    if stream is None:
        return None

    audio = None
    if stream.master.audio:
        choices = _track_choices(stream.master.audio)
        if len(choices) == 1:
            # The video rendition carries no audio of its own, so the only track
            # on offer is the one that gets muxed in: nothing to ask.
            audio = choices[0].value
        else:
            audio = _select("Audio language:", choices)
            if audio is None:
                return None

    subtitle = None
    if stream.master.subtitles:
        answer = _select("Subtitles:", [
            Choice("No subtitles", value=NO_SUBTITLE),
            *_track_choices(stream.master.subtitles),
        ])
        if answer is None:
            return None
        subtitle = None if answer == NO_SUBTITLE else answer

    return audio, subtitle


# ── Downloading ────────────────────────────────────────────────────────────────

def _advance_session(console: Console,
                     session: download.Session) -> download.Session | None:
    try:
        return download.advance(session)
    except OSError as exc:
        console.print(f"  [red]session state error:[/] {exc}")
        return session


def _run_session(console: Console, http: Http, session: download.Session,
                 source: Source | None = None) -> None:
    started = monotonic()
    done, skipped, failed = 0, 0, 0
    reporter = Reporter(console)
    source = source or for_link(http, session.link)

    while session is not None:
        job = session.job
        prefix = f"[{session.current + 1}/{session.total}]" if session.total > 1 else ""
        existing = (
            not session.active_path.exists()
            and download.existing_output(job.destination) is not None
        )
        if existing:
            console.print(f"{prefix} [dim]{job.label}: already exists, skipping.[/]")
            skipped += 1
            updated = _advance_session(console, session)
            if updated is session:
                break
            session = updated
            continue

        console.print(f"{prefix} [bold]{job.label}[/]")
        try:
            with reporter.live():
                final = download.execute(
                    http, source, job, reporter, session.active_path,
                )
            console.print(f"  [green]✓[/] {final}")
            done += 1
        except MissingAudioTrack as exc:
            logger.warning("Download of %s skipped: %s", job.label, exc)
            console.print(f"  [yellow]skipped:[/] {exc}")
            skipped += 1
        except (Cancelled, KeyboardInterrupt):
            console.print("  [yellow]cancelled.[/]")
            try:
                download.discard_session(session)
            except OSError as exc:
                console.print(f"  [red]temporary files could not be removed:[/] {exc}")
            break
        except ffmpeg.FfmpegError as exc:
            console.print(f"  [red]ffmpeg:[/] {exc}")
            failed += 1
        except (HttpError, RuntimeError, OSError, KeyError) as exc:
            logger.exception("Download of %s failed", job.label)
            console.print(f"  [red]error:[/] {exc}")
            failed += 1

        updated = _advance_session(console, session)
        if updated is session:
            break
        session = updated

    elapsed = timedelta(seconds=round(monotonic() - started))
    summary = [f"[green]{done} completed[/] in {elapsed}"]
    if skipped:
        summary.append(f"[yellow]{skipped} skipped[/]")
    if failed:
        summary.append(f"[red]{failed} failed[/]")
    console.print("  ".join(summary) + f"\n[dim]{naming.OUTPUT_ROOT}[/]\n")


def _session_label(session: download.Session) -> str:
    job = session.job
    if session.scope == download.SCOPE_MOVIE:
        return f"Movie · {session.title.name}"
    if session.scope == download.SCOPE_EPISODE:
        return f"Episode · {job.label}"

    progress = f"item {session.current + 1}/{session.total} · {job.episode.label}"
    if session.scope == download.SCOPE_SEASON:
        return (
            f"Season · {session.title.name} · Season {job.episode.season:02d} · "
            f"{progress}"
        )
    return f"Series · {session.title.name} · {progress}"


def _recover_interrupted(console: Console, http: Http) -> bool:
    """Offer one surviving session; return true after attempting one recovery."""
    if not download.has_temporary_data():
        return False

    pending = download.interrupted()
    choices = [
        Choice(
            f"{_session_label(item)}  ({item.saved_segments} saved segments)",
            value=item,
        )
        for item in pending
    ]
    if not choices:
        console.print("[yellow]Temporary data was found but cannot be resumed.[/]")
    choices.append(Choice(
        "Delete all interrupted downloads",
        value=DELETE_INTERRUPTED,
    ))

    selected = _select("Interrupted downloads:", choices)
    if selected is None:
        return False
    if selected == DELETE_INTERRUPTED:
        try:
            download.discard_all_interrupted()
        except OSError as exc:
            console.print(f"[red]Temporary files could not be removed:[/] {exc}")
            return True
        console.print("[dim]Interrupted downloads removed.[/]\n")
        return False

    console.print(f"[dim]Resuming {_session_label(selected)}...[/]")
    _run_session(console, http, selected)
    return True


def _download(console: Console, http: Http, link: urls.Link) -> None:
    """Resolve and download the content identified by one link."""
    source = for_link(http, link)
    resolved = _attempt(console, "Resolving link", lambda: source.resolve(link))
    if resolved is None:
        return
    title, episode = resolved
    year = f" [dim]({title.year})[/]" if title.year else ""
    console.print(f"[dim]{source.label}:[/] [bold]{title.name}[/]{year}")

    targets = _choose_targets(console, source, link, title, episode)
    if targets is None:
        return

    tracks = _choose_tracks(console, source, title, targets.items[0])
    if tracks is None:
        return
    audio, subtitle = tracks

    if len(targets.items) > 1 and not questionary.confirm(
        f"Download {len(targets.items)} files?", default=True, style=STYLE, qmark="?"
    ).ask():
        return
    try:
        session = download.start_session(
            link, title, list(targets.items), audio, subtitle, targets.scope,
        )
    except (OSError, ValueError) as exc:
        console.print(f"[red]Download session could not be created:[/] {exc}")
        return
    _run_session(console, http, session, source)


# ── Entry point ────────────────────────────────────────────────────────────────

def main(url: str | None = None) -> int:
    from . import __version__

    console = Console()
    console.print(f"[bold]StreamingDownloader[/] [dim]v{__version__}  ·  {naming.OUTPUT_ROOT}[/]\n")

    try:
        ffmpeg.require()
    except ffmpeg.FfmpegError as exc:
        console.print(f"[red]{exc}[/]")
        return 1

    http = Http()
    while True:
        if _recover_interrupted(console, http):
            continue

        if url is None:
            url = _text("Page URL (StreamingCommunity or AnimeUnity):")
            if url is None:
                return 0

        link = urls.parse(url)
        url = None
        if link is None:
            console.print(
                f"[red]URL not recognized.[/] Supported formats:\n{urls.EXAMPLES}\n"
            )
            continue

        _download(console, http, link)
