"""Behavioral tests for public channels, private jobs and persistent recovery."""

# pylint: disable=too-many-locals

import asyncio
import io
import wave
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

import httpx
import pytest

from edge_tts_server.app import create_app
from edge_tts_server.config import ServerConfig
from edge_tts_server.jobs import Workspace, merge_subtitles
from edge_tts_server.mimo import MiMoError, MiMoTransientError
from edge_tts_server.models import TTSRequest
from edge_tts_server.synthesis import SynthesisEngine

WRITE = {"X-Requested-With": "tts-workspace"}
ADMIN = {**WRITE, "X-API-Key": "admin-secret"}


def sample_audio() -> bytes:
    """A valid 100 ms canonical WAV exercises the actual FFmpeg merge path."""
    output = io.BytesIO()
    with wave.Wave_write(output) as audio:
        audio.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
        audio.writeframes(b"\x01\x00" * 2400)
    return output.getvalue()


class FakeEngine(SynthesisEngine):
    """Offline provider with controllable failures and suspension."""

    calls: list[str] = []
    fail_on: str | None = None
    gate: asyncio.Event | None = None
    started: asyncio.Event | None = None

    async def synthesize(
        self,
        payload: TTSRequest,
        boundary: Any = "SentenceBoundary",
        *,
        include_subtitles: bool = False,
    ) -> tuple[bytes, str, str, int]:
        self.calls.append(payload.text)
        if self.started:
            self.started.set()
        if self.gate:
            await self.gate.wait()
        if payload.text == self.fail_on:
            raise MiMoError("Do not expose provider-secret")
        return (
            sample_audio(),
            ("1\n00:00:00,000 --> 00:00:00,080\nHello\n" if include_subtitles else ""),
            "wav",
            100,
        )


@asynccontextmanager
async def workspace_client(  # type: ignore[misc]
    tmp_path: Path, **overrides: Any
) -> AsyncIterator[tuple[httpx.AsyncClient, Workspace]]:
    """Start isolated application lifecycle and a cookie-preserving browser client."""
    FakeEngine.calls = []
    FakeEngine.fail_on = None
    FakeEngine.gate = None
    FakeEngine.started = None
    config = ServerConfig(api_key="admin-secret", data_dir=str(tmp_path), **overrides)
    app = create_app(config, workspace_engine_factory=FakeEngine)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://tts"
        ) as client:
            await client.get("/api/session")
            yield client, app.state.workspace


@pytest.mark.asyncio
async def test_channel_admin_persistence_and_secret_separation(tmp_path: Path) -> None:
    """Only authenticated administrators can configure connections; secrets never return."""
    async with workspace_client(tmp_path) as (client, _workspace):
        assert (await client.get("/api/admin/channels")).status_code == 401
        payload = {
            "name": "MiMo private",
            "provider": "mimo",
            "api_key": "provider-secret",
            "base_url": "https://provider.invalid/v1",
        }
        denied = await client.post("/api/admin/channels", json=payload, headers=WRITE)
        assert denied.status_code == 401
        created = await client.post("/api/admin/channels", json=payload, headers=ADMIN)
        assert created.status_code == 201
        channel_id = created.json()["id"]
        assert created.json()["has_api_key"] is True
        assert "provider-secret" not in created.text
        public = await client.get("/api/channels")
        assert (
            "provider-secret" not in public.text
            and "provider.invalid" not in public.text
        )
        assert {field["name"] for field in public.json()["channels"][-1]["fields"]} >= {
            "mimo_mode",
            "reference_audio",
            "instructions",
        }
        payload.pop("api_key")
        payload["name"] = "Renamed"
        edited = await client.put(
            f"/api/admin/channels/{channel_id}", json=payload, headers=ADMIN
        )
        assert edited.json()["has_api_key"] is True
    async with workspace_client(tmp_path) as (client, _workspace):
        channels = (await client.get("/api/channels")).json()["channels"]
        assert any(c["id"] == channel_id and c["name"] == "Renamed" for c in channels)


@pytest.mark.asyncio
async def test_jobs_merge_and_download_are_browser_private(tmp_path: Path) -> None:
    """Long documents bypass legacy total-text limits while segments remain bounded."""
    async with workspace_client(tmp_path, max_text_length=5) as (client, workspace):
        response = await client.post(
            "/api/jobs",
            headers=WRITE,
            json={
                "channel_id": "edge-default",
                "text": "第一句话。第二句话。第三句话。",
                "subtitles": True,
                "options": {"response_format": "wav"},
            },
        )
        assert response.status_code == 202
        job_id = response.json()["id"]
        await asyncio.wait_for(workspace.queue.join(), 10)
        job = (await client.get(f"/api/jobs/{job_id}")).json()
        assert job["status"] == "completed" and job["completed_segments"] == 3
        assert "".join(FakeEngine.calls) == "第一句话。第二句话。第三句话。"
        assert (
            job["duration_ms"] == 300
            and job["expires_at"] > job["created_at"] + 6 * 86400
        )
        audio = await client.get(
            f"/api/jobs/{job_id}/audio", headers={"Range": "bytes=0-43"}
        )
        assert audio.status_code == 206 and audio.content[:4] == b"RIFF"
        subtitles = await client.get(f"/api/jobs/{job_id}/subtitles")
        assert "00:00:00,200 --> 00:00:00,280" in subtitles.text
        cookies = httpx.Cookies(client.cookies)
        client.cookies.clear()
        await client.get("/api/session")
        assert (await client.get("/api/jobs")).json()["jobs"] == []
        assert (await client.get(f"/api/jobs/{job_id}/audio")).status_code == 404
        client.cookies = cookies
        assert (await client.get(f"/api/jobs/{job_id}")).json()["status"] == "completed"


@pytest.mark.asyncio
async def test_failed_job_retries_only_remaining_segments(tmp_path: Path) -> None:
    """Recovery reuses durable audio instead of charging for successful segments again."""
    async with workspace_client(tmp_path, max_text_length=3) as (client, workspace):
        FakeEngine.fail_on = "第二。"
        response = await client.post(
            "/api/jobs",
            headers=WRITE,
            json={"channel_id": "edge-default", "text": "第一。第二。"},
        )
        job_id = response.json()["id"]
        await asyncio.wait_for(workspace.queue.join(), 10)
        failed = (await client.get(f"/api/jobs/{job_id}")).json()
        assert failed["status"] == "failed" and failed["completed_segments"] == 1
        assert "provider-secret" not in str(failed)
        FakeEngine.fail_on = None
        assert (
            await client.post(f"/api/jobs/{job_id}/retry", headers=WRITE)
        ).status_code == 202
        await asyncio.wait_for(workspace.queue.join(), 10)
        assert FakeEngine.calls == ["第一。", "第二。", "第二。"]
        assert (await client.get(f"/api/jobs/{job_id}")).json()["status"] == "completed"


@pytest.mark.asyncio
async def test_cancellation_stops_active_work(tmp_path: Path) -> None:
    """Cancel terminates the active provider await and prevents later segment calls."""
    async with workspace_client(tmp_path, max_text_length=3) as (client, workspace):
        FakeEngine.gate = asyncio.Event()
        FakeEngine.started = asyncio.Event()
        response = await client.post(
            "/api/jobs",
            headers=WRITE,
            json={"channel_id": "edge-default", "text": "第一。第二。"},
        )
        job_id = response.json()["id"]
        await asyncio.wait_for(FakeEngine.started.wait(), 2)
        cancelled = await client.post(f"/api/jobs/{job_id}/cancel", headers=WRITE)
        assert cancelled.json()["status"] == "cancelled"
        await asyncio.wait_for(workspace.queue.join(), 2)
        assert FakeEngine.calls == ["第一。"]


@pytest.mark.asyncio
async def test_restart_and_expiry_preserve_then_remove_task(tmp_path: Path) -> None:
    """A restart never silently resubmits work, and expired files cannot be downloaded."""
    async with workspace_client(tmp_path) as (client, workspace):
        result = await client.post(
            "/api/jobs",
            headers=WRITE,
            json={"channel_id": "edge-default", "text": "hello"},
        )
        job_id = result.json()["id"]
        await asyncio.wait_for(workspace.queue.join(), 10)
        workspace.store.update_job(job_id, status="running")
        cookies = httpx.Cookies(client.cookies)
    async with workspace_client(tmp_path) as (client, workspace):
        client.cookies = cookies
        assert (await client.get(f"/api/jobs/{job_id}")).json()[
            "status"
        ] == "interrupted"
        assert not FakeEngine.calls
        workspace.store.update_job(job_id, expires_at=1)
        assert (await client.get(f"/api/jobs/{job_id}/audio")).status_code == 410
        await workspace.cleanup()
        assert not workspace.directory(job_id).exists()
        assert workspace.store.job(job_id) is None


@pytest.mark.asyncio
async def test_writes_reject_csrf_oversized_and_wrong_channel_options(
    tmp_path: Path,
) -> None:
    """Boundary protection applies before file creation and provider calls."""
    async with workspace_client(tmp_path, max_request_bytes=1000) as (
        client,
        _workspace,
    ):
        payload = {"channel_id": "edge-default", "text": "hello"}
        assert (await client.post("/api/jobs", json=payload)).status_code == 403
        assert (
            await client.post(
                "/api/jobs",
                json=payload,
                headers={**WRITE, "Origin": "https://attacker.invalid"},
            )
        ).status_code == 403
        assert (
            await client.post(
                "/api/jobs", json={**payload, "text": "x" * 1200}, headers=WRITE
            )
        ).status_code == 413
        for options in (
            {"mimo_mode": "clone"},
            {"reference_audio": "bad"},
            {"model": "mimo-v2-tts"},
            {"rate": "+101%"},
            {"volume": "-101%"},
        ):
            assert (
                await client.post(
                    "/api/jobs", json={**payload, "options": options}, headers=WRITE
                )
            ).status_code == 400
        assert not FakeEngine.calls


@pytest.mark.asyncio
async def test_retry_after_restart_preserves_original_text_segments(
    tmp_path: Path,
) -> None:
    """Changing deployment limits cannot change an already-submitted article's boundaries."""
    async with workspace_client(tmp_path, max_text_length=3) as (client, workspace):
        FakeEngine.fail_on = "第二。"
        result = await client.post(
            "/api/jobs",
            headers=WRITE,
            json={"channel_id": "edge-default", "text": "第一。第二。"},
        )
        job_id = result.json()["id"]
        await asyncio.wait_for(workspace.queue.join(), 10)
        cookies = httpx.Cookies(client.cookies)
    async with workspace_client(tmp_path, max_text_length=2) as (client, workspace):
        client.cookies = cookies
        await client.post(f"/api/jobs/{job_id}/retry", headers=WRITE)
        await asyncio.wait_for(workspace.queue.join(), 10)
        assert FakeEngine.calls == ["第二。"]
        assert (await client.get(f"/api/jobs/{job_id}")).json()["status"] == "completed"


@pytest.mark.asyncio
@pytest.mark.parametrize("failures, expected", [(2, "completed"), (3, "failed")])
async def test_temporary_provider_failures_have_bounded_automatic_retries(
    tmp_path: Path, failures: int, expected: str
) -> None:
    """Temporary provider outages retry twice; permanent failure never loops forever."""
    async with workspace_client(tmp_path) as (client, workspace):

        class TransientEngine(FakeEngine):
            """Fail a controlled number of attempts before returning real WAV data."""

            attempts = 0

            async def synthesize(
                self,
                payload: TTSRequest,
                boundary: Any = "SentenceBoundary",
                *,
                include_subtitles: bool = False,
            ) -> tuple[bytes, str, str, int]:
                self.attempts += 1
                if self.attempts <= failures:
                    raise MiMoTransientError("temporary")
                return await super().synthesize(
                    payload, boundary, include_subtitles=include_subtitles
                )

        engine = TransientEngine(workspace.config)
        workspace.engine_factory = lambda _config: engine
        result = await client.post(
            "/api/jobs",
            headers=WRITE,
            json={"channel_id": "edge-default", "text": "hello"},
        )
        await asyncio.wait_for(workspace.queue.join(), 10)
        assert engine.attempts == 3
        assert (await client.get(f"/api/jobs/{result.json()['id']}")).json()[
            "status"
        ] == expected


def test_subtitle_offset_includes_trailing_silence() -> None:
    """Caption end time is not the same as the duration of its audio segment."""
    srt = "1\n00:00:00,000 --> 00:00:00,100\nHello\n"
    result = merge_subtitles(
        [{"duration_ms": 250, "subtitles": srt}, {"duration_ms": 200, "subtitles": srt}]
    )
    assert "00:00:00,250 --> 00:00:00,350" in result
