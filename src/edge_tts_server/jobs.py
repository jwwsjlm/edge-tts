"""Disk-backed synthesis jobs with bounded concurrency and explicit recovery."""

# pylint: disable=too-many-instance-attributes,too-many-locals,too-many-statements

import asyncio
import io
import logging
import re
import shutil
import time
import uuid
import wave
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable

import aiohttp
import imageio_ffmpeg  # type: ignore[import-untyped]

from .audio import AudioConversionError, inspect_audio
from .channels import channel_config, job_payload
from .config import ServerConfig
from .mimo import MiMoError, MiMoRateLimitError, MiMoTransientError
from .store import Store
from .synthesis import SynthesisEngine
from .text import split_text

LOGGER = logging.getLogger("uvicorn.error.edge_tts_server.jobs")
EngineFactory = Callable[[ServerConfig], SynthesisEngine]


class WorkspaceBusy(RuntimeError):
    """The server cannot accept more work or safely store another segment."""


def public_job(job: dict[str, Any]) -> dict[str, Any]:
    """Return a safe task summary, excluding text bodies, secrets and reference audio."""
    payload = job["payload"]
    return {
        "id": job["id"],
        "status": job["status"],
        "created_at": job["created_at"],
        "updated_at": job["updated_at"],
        "expires_at": job["expires_at"],
        "title": payload["text"].strip()[:50],
        "text_length": len(payload["text"]),
        "preview": payload.get("preview", False),
        "channel_name": job["channel"]["name"],
        "provider": job["channel"]["provider"],
        "voice": payload["options"].get("voice"),
        "response_format": payload["options"].get("response_format", "mp3"),
        "subtitles": payload.get("subtitles", False),
        "total_segments": job["total_segments"],
        "completed_segments": len(job["segments"]),
        "error": job["error"],
        "audio_bytes": job["audio_bytes"],
        "duration_ms": job["duration_ms"],
    }


def merge_subtitles(segments: list[dict[str, Any]]) -> str:
    """Offset SRT cues by decoded segment duration, including trailing silence."""

    def milliseconds(value: str) -> int:
        hours, minutes, seconds, millis = map(int, re.split(r"[:,]", value))
        return ((hours * 60 + minutes) * 60 + seconds) * 1000 + millis

    def timestamp(value: int) -> str:
        seconds, millis = divmod(value, 1000)
        minutes, seconds = divmod(seconds, 60)
        hours, minutes = divmod(minutes, 60)
        return f"{hours:02}:{minutes:02}:{seconds:02},{millis:03}"

    cues: list[str] = []
    offset = 0
    pattern = re.compile(
        r"\d+\s*\n(\d+:\d+:\d+,\d+) --> (\d+:\d+:\d+,\d+)\s*\n(.*?)(?=\n\s*\n|\Z)",
        re.DOTALL,
    )
    for segment in segments:
        for match in pattern.finditer(
            segment.get("subtitles", "").replace("\r\n", "\n")
        ):
            start = offset + milliseconds(match[1])
            end = min(offset + milliseconds(match[2]), offset + segment["duration_ms"])
            if end > start:
                cues.append(
                    f"{len(cues) + 1}\n{timestamp(start)} --> {timestamp(end)}\n"
                    f"{match[3].strip()}\n"
                )
        offset += segment["duration_ms"]
    return "\n".join(cues)


async def merge_audio(
    directory: Path, count: int, output_format: str, timeout: float, max_bytes: int
) -> Path:
    """Encode one final file from canonical WAV segments without loading it into RAM."""
    manifest = directory / "segments.txt"
    manifest.write_text(
        "".join(f"file '{index:06}.wav'\n" for index in range(count)), encoding="utf-8"
    )
    target = directory / f"speech.{output_format}"
    temporary = directory / f"merging.{output_format}"
    codec = (
        ["-c:a", "pcm_s16le"]
        if output_format == "wav"
        else ["-c:a", "libmp3lame", "-b:a", "128k"]
    )
    process = await asyncio.create_subprocess_exec(
        imageio_ffmpeg.get_ffmpeg_exe(),
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-f",
        "concat",
        "-safe",
        "1",
        "-i",
        str(manifest),
        "-ar",
        "24000",
        "-ac",
        "1",
        *codec,
        "-fs",
        str(max_bytes),
        "-y",
        str(temporary),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        await asyncio.wait_for(process.wait(), timeout)
        if (
            process.returncode
            or not temporary.is_file()
            or not 0 < temporary.stat().st_size < max_bytes
        ):
            raise AudioConversionError("Audio merge failed")
        temporary.replace(target)
        return target
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
        temporary.unlink(missing_ok=True)


class Workspace:
    """Single-process worker queue; SQLite preserves work across application restarts."""

    def __init__(
        self,
        config: ServerConfig,
        capacity: asyncio.Semaphore,
        engine_factory: EngineFactory = SynthesisEngine,
    ) -> None:
        self.config = config
        self.root = Path(config.data_dir).resolve()
        self.files = self.root / "jobs"
        self.capacity = capacity
        self.engine_factory = engine_factory
        self._store: Store | None = None
        self.start_lock = asyncio.Lock()
        self.storage_lock = asyncio.Lock()
        self.reserved_bytes = 0
        self.queue: asyncio.Queue[str] = asyncio.Queue(config.max_pending_jobs)
        self.workers: list[asyncio.Task[None]] = []
        self.running: dict[str, asyncio.Task[None]] = {}
        self.closing = False
        self.cleaner: asyncio.Task[None] | None = None

    @property
    def store(self) -> Store:
        """Require lifecycle initialization before accessing persistent state."""
        assert self._store is not None
        return self._store

    async def start(self) -> None:
        """Initialize once, recover interrupted work, then run bounded workers."""
        async with self.start_lock:
            if self._store is not None:
                return
            self._store = Store(self.root, self.config)
            self.files.mkdir(exist_ok=True, mode=0o700)
            self.store.recover()
            await self.cleanup()
            self.workers = [
                asyncio.create_task(self._worker())
                for _ in range(self.config.max_concurrent_requests)
            ]
            self.cleaner = asyncio.create_task(self._cleanup_loop())

    async def close(self) -> None:
        """Cancel in-flight calls and mark unfinished work as resumable."""
        self.closing = True
        tasks = [*self.workers, *self.running.values()]
        if self.cleaner:
            tasks.append(self.cleaner)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if self._store is not None:
            self.store.recover()
            self.store.close()
            self._store = None

    def directory(self, job_id: str) -> Path:
        """Never construct a file path from unchecked identifiers."""
        if not re.fullmatch(r"[0-9a-f]{32}", job_id):
            raise ValueError("Invalid job identifier")
        path = self.files / job_id
        if path.resolve().parent != self.files.resolve() or path.is_symlink():
            raise ValueError("Invalid job directory")
        return path

    def check_storage(self, extra: int = 0) -> None:
        """Keep free disk space and a deployment-wide storage budget."""
        used = sum(
            path.stat().st_size for path in self.root.rglob("*") if path.is_file()
        )
        if (
            used + extra + self.reserved_bytes > self.config.max_storage_bytes
            or shutil.disk_usage(self.root).free
            < extra + self.reserved_bytes + 256 * 1024 * 1024
        ):
            raise WorkspaceBusy("存储空间不足，请清理旧任务或联系管理员后重试。")

    def split(self, text: str, channel: dict[str, Any]) -> list[str]:
        """MiMo favors shorter segments; Edge retains the deployment's segment limit."""
        limit = self.config.max_text_length
        if channel["provider"] == "mimo":
            limit = min(limit, self.config.mimo_recommended_max_text_length, 3000)
        return split_text(text, limit)

    async def submit(  # pylint: disable=too-many-positional-arguments
        self,
        owner: str,
        channel: dict[str, Any],
        text: str,
        options: dict[str, Any],
        subtitles: bool,
        preview: bool,
    ) -> dict[str, Any]:
        """Validate the complete task before queueing or contacting a provider."""
        if preview:
            text = split_text(text, 120)[0]
        pieces = self.split(text, channel)
        if subtitles and channel["provider"] != "edge":
            raise ValueError("当前渠道不支持字幕。")
        job_payload(channel, channel_config(self.config, channel), pieces[0], options)
        payload: dict[str, Any] = {
            "text": text,
            "options": dict(options),
            "subtitles": subtitles,
            "preview": preview,
            "pieces": pieces,
        }
        reference = payload["options"].pop("reference_audio", None)
        payload["has_reference"] = bool(reference)
        async with self.storage_lock:
            if (
                self.store.pending_count() >= self.config.max_pending_jobs
                or self.queue.full()
            ):
                raise WorkspaceBusy("生成队列已满，请稍后重试。")
            self.check_storage(len(text.encode("utf-8")) + len(reference or "") + 65536)
            job_id = uuid.uuid4().hex
            directory = self.directory(job_id)
            directory.mkdir(mode=0o700)
            try:
                if reference:
                    (directory / "reference.txt").write_text(
                        reference, encoding="ascii"
                    )
                job = self.store.create_job(
                    job_id, owner, payload, channel, len(pieces)
                )
            except BaseException:
                shutil.rmtree(directory)
                raise
            self.queue.put_nowait(job_id)
        return public_job(job)

    async def cancel(self, job_id: str) -> None:
        """Stop queued and active work without deleting completed segments."""
        self.store.update_job(job_id, status="cancelled", error=None)
        task = self.running.get(job_id)
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def retry(self, job_id: str) -> None:
        """Resume only unfinished segments, using the original channel snapshot."""
        if (
            self.store.pending_count() >= self.config.max_pending_jobs
            or self.queue.full()
        ):
            raise WorkspaceBusy("生成队列已满，请稍后重试。")
        self.store.update_job(job_id, status="queued", error=None, expires_at=None)
        self.queue.put_nowait(job_id)

    async def _worker(self) -> None:
        while True:
            job_id = await self.queue.get()
            try:
                job = self.store.job(job_id)
                if job is not None and job["status"] == "queued":
                    self.store.update_job(job_id, status="running", error=None)
                    task = asyncio.create_task(self._generate(job))
                    self.running[job_id] = task
                    try:
                        await task
                    except asyncio.CancelledError:
                        if self.closing:
                            raise
                    finally:
                        self.running.pop(job_id, None)
            finally:
                self.queue.task_done()

    async def _segment(
        self,
        engine: SynthesisEngine,
        channel: dict[str, Any],
        text: str,
        options: dict[str, Any],
        subtitles: bool,
    ) -> tuple[bytes, str]:
        payload = job_payload(channel, engine.config, text, options).model_copy(
            update={"response_format": "wav"}
        )
        for attempt in range(3):
            try:
                async with self.capacity:
                    audio, srt, _format, _duration = await engine.synthesize(
                        payload, include_subtitles=subtitles
                    )
                return audio, srt
            except (
                asyncio.TimeoutError,
                aiohttp.ClientError,
                MiMoRateLimitError,
                MiMoTransientError,
            ) as exc:
                if attempt == 2:
                    raise
                if isinstance(exc, aiohttp.ClientResponseError) and getattr(
                    exc, "status"
                ) not in {
                    408,
                    429,
                    500,
                    502,
                    503,
                    504,
                }:
                    raise
                delay = float(2**attempt)
                if isinstance(exc, MiMoRateLimitError) and exc.retry_after:
                    try:
                        delay = max(delay, float(exc.retry_after))
                    except ValueError:
                        try:
                            delay = max(
                                delay,
                                parsedate_to_datetime(exc.retry_after).timestamp()
                                - time.time(),
                            )
                        except (TypeError, ValueError, OverflowError):
                            pass
                    if delay > 60:
                        raise
                await asyncio.sleep(delay)
        raise RuntimeError("Unreachable retry state")

    async def _generate(self, job: dict[str, Any]) -> None:
        job_id = job["id"]
        directory = self.directory(job_id)
        engine = self.engine_factory(channel_config(self.config, job["channel"]))
        try:
            self.store.update_job(job_id, status="running", error=None)
            payload = job["payload"]
            options = dict(payload["options"])
            if payload.get("has_reference"):
                options["reference_audio"] = (directory / "reference.txt").read_text(
                    encoding="ascii"
                )
            pieces = payload["pieces"]
            segments = job["segments"]
            for index in range(len(segments), len(pieces)):
                async with self.storage_lock:
                    self.check_storage(self.config.max_audio_bytes * 2)
                if not pieces[index].strip():
                    # Whitespace-only spans have no audible content and need no upstream call.
                    silence = io.BytesIO()
                    with wave.Wave_write(silence) as wav:
                        wav.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
                        wav.writeframes(b"\x00\x00" * 240)
                    audio, srt = silence.getvalue(), ""
                else:
                    audio, srt = await self._segment(
                        engine,
                        job["channel"],
                        pieces[index],
                        options,
                        payload["subtitles"],
                    )
                info = inspect_audio(audio, "wav")
                if not info.valid or info.duration_ms is None:
                    raise AudioConversionError("Provider returned invalid audio")
                async with self.storage_lock:
                    self.check_storage(len(audio))
                    temporary = directory / f"{index:06}.tmp"
                    temporary.write_bytes(audio)
                    temporary.replace(directory / f"{index:06}.wav")
                    segments.append({"duration_ms": info.duration_ms, "subtitles": srt})
                    self.store.update_job(
                        job_id,
                        segments=segments,
                        duration_ms=sum(s["duration_ms"] for s in segments),
                    )
            self.store.update_job(job_id, status="merging")
            async with self.storage_lock:
                needed = (
                    sum(p.stat().st_size for p in directory.glob("*.wav"))
                    + 2 * 1024 * 1024
                )
                self.check_storage(needed)
                self.reserved_bytes += needed
            try:
                output = await merge_audio(
                    directory,
                    len(pieces),
                    options.get("response_format", "mp3"),
                    max(120, sum(s["duration_ms"] for s in segments) / 1000),
                    needed,
                )
            finally:
                self.reserved_bytes -= needed
            if payload["subtitles"]:
                (directory / "speech.srt").write_text(
                    merge_subtitles(segments), encoding="utf-8"
                )
            self.store.update_job(
                job_id, status="completed", audio_bytes=output.stat().st_size
            )
            try:
                # Completed tasks only need final downloads; cleanup errors do not invalidate audio.
                for path in directory.glob("[0-9]*.wav"):
                    path.unlink()
                (directory / "segments.txt").unlink(missing_ok=True)
            except OSError:
                LOGGER.warning("Segment cleanup deferred id=%s", job_id)
        except asyncio.CancelledError:
            self.store.update_job(
                job_id,
                status="interrupted" if self.closing else "cancelled",
                error=None,
            )
            raise
        except Exception as exc:  # pylint: disable=broad-exception-caught
            message = "生成失败，已完成的片段已保存，请稍后重试。"
            if isinstance(exc, WorkspaceBusy):
                message = str(exc)
            elif isinstance(
                exc, (MiMoError, aiohttp.ClientError, asyncio.TimeoutError)
            ):
                message = "语音渠道暂时不可用或请求超时，请稍后重试。"
            self.store.update_job(job_id, status="failed", error=message)
            LOGGER.error("Job failed id=%s type=%s", job_id, type(exc).__name__)
        finally:
            await engine.close()

    async def cleanup(self) -> None:
        """Remove expired task files before metadata, retrying file errors later."""
        async with self.storage_lock:
            for job_id in self.store.expired_jobs():
                try:
                    directory = self.directory(job_id)
                    if directory.exists():
                        await asyncio.to_thread(shutil.rmtree, directory)
                    self.store.delete_job(job_id)
                except OSError:
                    LOGGER.warning("Expired job cleanup deferred id=%s", job_id)

    async def _cleanup_loop(self) -> None:
        while True:
            await asyncio.sleep(300)
            await self.cleanup()
