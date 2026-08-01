"""FFmpeg calls: turning segments into a playable file.

Two operations only — join the downloaded segments into an .mp4, and mux extra
audio and subtitle tracks into an .mkv — sharing one subprocess wrapper.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryFile

from .config import label_of

logger = logging.getLogger(__name__)

STDERR_TAIL = 400
CHUNK = 1 << 20

INSTALL_HINT = (
    "FFmpeg is not installed or is not on PATH.\n"
    "  Windows:  winget install Gyan.FFmpeg     (or: choco install ffmpeg)\n"
    "  Debian:   sudo apt install ffmpeg\n"
    "  macOS:    brew install ffmpeg\n"
    "Then reopen the terminal and restart the application."
)


class FfmpegError(RuntimeError):
    pass


def require() -> str:
    """Absolute path to the ffmpeg binary, or an explanatory error."""
    path = shutil.which("ffmpeg")
    if path is None:
        raise FfmpegError(INSTALL_HINT)
    return path


def _feed(stdin, parts: list[Path], task) -> None:
    """Write the segments into ffmpeg's stdin as one continuous stream.

    MPEG-TS is a continuous stream format, so joining the segments at byte level
    lets FFmpeg's TS demuxer handle PCR/PTS continuity natively — the concat
    demuxer reintroduces the timestamp drift this avoids. The bytes go through a
    pipe instead of an intermediate .ts file, which would cost a full write and a
    full read of the whole video before ffmpeg even started.

    It also makes the remux measurable: a write blocks until ffmpeg has consumed
    what came before, so the segments fed are real progress.
    """
    task.set_total(len(parts))
    try:
        for part in parts:
            written = 0
            with open(part, "rb") as segment:
                # Copied in chunks: a single segment can be tens of megabytes.
                while chunk := segment.read(CHUNK):
                    stdin.write(chunk)
                    written += len(chunk)
            task.advance(1, written)
    except BrokenPipeError:
        # ffmpeg gave up early; what it wrote on stderr is the real error, so
        # leave the reporting to _run.
        logger.debug("ffmpeg closed stdin before all segments were written")
    finally:
        try:
            stdin.close()
        except BrokenPipeError:
            pass


def _run(args: list[str], *, parts: list[Path] | None = None, task=None) -> None:
    """Run ffmpeg, streaming ``parts`` into its stdin when given."""
    command = [require(), "-hide_banner", "-loglevel", "error", "-y", *args]
    logger.debug("ffmpeg %s", " ".join(command[1:]))
    # stderr goes to a file rather than a pipe: while the segments are being
    # written nobody is draining stderr, and a pipe filling up would deadlock
    # both ends.
    with TemporaryFile() as errors:
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE if parts else subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=errors,
        )
        try:
            if parts:
                _feed(process.stdin, parts, task)
            returncode = process.wait()
        except BaseException:
            process.kill()
            process.wait()
            raise
        if returncode != 0:
            errors.seek(0)
            stderr = errors.read().decode(errors="replace").strip()
            if len(stderr) > STDERR_TAIL:
                stderr = "..." + stderr[-STDERR_TAIL:]
            raise FfmpegError(stderr or "ffmpeg returned an error without a message")


def join(parts: list[Path], destination: Path, task) -> Path:
    """Remux the downloaded MPEG-TS segments into an MP4.

    ``-fflags +genpts`` and ``-avoid_negative_ts`` normalize timestamps while
    the MP4 muxer automatically converts AAC's ADTS framing. The encoded audio
    and video therefore remain untouched.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    _run([
        "-fflags", "+genpts",
        "-avoid_negative_ts", "make_zero",
        "-i", "pipe:0",
        "-c", "copy",
        str(destination),
    ], parts=parts, task=task)
    return destination


def mux(video: Path, audio: tuple[Path, str] | None,
        subtitle: tuple[Path, str, bool] | None, destination: Path) -> Path:
    """Combine a video with the chosen audio and subtitle files into an MKV.

    ``audio`` is ``(path, language code)``, ``subtitle`` is
    ``(path, language code, forced)``; either can be absent.

    Every ``-map`` / ``-metadata`` / ``-disposition`` is an **output** option: it
    must come after all the inputs and before the output filename. Placed after
    the output, ffmpeg silently ignores them — which is how subtitle language
    tags end up missing while the command still succeeds.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)

    args = ["-i", str(video)]
    if audio:
        args += ["-i", str(audio[0])]
    if subtitle:
        args += ["-i", str(subtitle[0])]

    # The external audio replaces the video's own: it is the track the user asked
    # for, and the video rendition carries none of its own anyway.
    args += ["-map", "0:v:0", "-map", "1:a:0" if audio else "0:a?"]
    if subtitle:
        args += ["-map", f"{1 + bool(audio)}:0"]
    args += ["-c", "copy"]

    if audio:
        code = audio[1]
        args += ["-metadata:s:a:0", f"language={code}",
                 "-metadata:s:a:0", f"title={label_of(code)}"]
    if subtitle:
        _, code, forced = subtitle
        title = label_of(code) + (" (Forced)" if forced else "")
        args += ["-metadata:s:s:0", f"language={code}",
                 "-metadata:s:s:0", f"title={title}",
                 "-disposition:s:0", "forced" if forced else "0"]

    args.append(str(destination))
    _run(args)
    return destination
