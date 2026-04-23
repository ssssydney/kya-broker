"""Popup server tests — exercises the HTTP endpoints against a real local server."""

from __future__ import annotations

import asyncio
import urllib.parse
import urllib.request

import pytest

from src.popup_server import (
    FieldType,
    PopupField,
    PopupOutcome,
    PopupServer,
)


@pytest.fixture
def server():
    s = PopupServer()
    s.ensure_started()
    yield s
    s.shutdown()


def _post(url: str, data: dict[str, str]) -> int:
    body = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req, timeout=2) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        return e.code


def _get(url: str) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(url, timeout=2) as resp:
            return resp.status, resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8") if e.fp else ""


def test_health(server):
    status, _ = _get(f"http://127.0.0.1:{server.port}/health")
    assert status == 200


def test_create_session_and_get_page(server):
    session = server.create_session(
        title="enter otp",
        instruction="paste the code",
        fields=[PopupField(key="code", label="code", type=FieldType.OTP)],
        open_browser=False,
    )
    status, body = _get(server.url_for(session.session_id))
    assert status == 200
    assert "enter otp" in body
    assert "code" in body


def test_unknown_session_returns_404(server):
    status, _ = _get(f"http://127.0.0.1:{server.port}/gate/bogus")
    assert status == 404


@pytest.mark.asyncio
async def test_submission_flow(server):
    session = server.create_session(
        title="x",
        instruction="y",
        fields=[PopupField(key="code", label="code", type=FieldType.OTP)],
        timeout_seconds=5,
        open_browser=False,
    )

    async def simulate_user():
        await asyncio.sleep(0.05)
        status = await asyncio.to_thread(
            _post,
            f"http://127.0.0.1:{server.port}/submit/{session.session_id}",
            {"code": "123456", "_csrf": session.session_id},
        )
        assert status == 200

    asyncio.create_task(simulate_user())
    result = await server.wait_for_submission(session)
    assert result.outcome == PopupOutcome.SUBMITTED
    assert result.data == {"code": "123456"}


@pytest.mark.asyncio
async def test_decline_flow(server):
    session = server.create_session(
        title="x",
        instruction="y",
        fields=[PopupField(key="code", label="code")],
        timeout_seconds=5,
        open_browser=False,
    )

    async def simulate_decline():
        await asyncio.sleep(0.05)
        await asyncio.to_thread(
            _post,
            f"http://127.0.0.1:{server.port}/decline/{session.session_id}",
            {"_csrf": session.session_id},
        )

    asyncio.create_task(simulate_decline())
    result = await server.wait_for_submission(session)
    assert result.outcome == PopupOutcome.DECLINED


@pytest.mark.asyncio
async def test_timeout_flow(server):
    session = server.create_session(
        title="x", instruction="y",
        fields=[PopupField(key="code", label="code")],
        timeout_seconds=1,
        open_browser=False,
    )
    result = await server.wait_for_submission(session)
    assert result.outcome == PopupOutcome.TIMEOUT


def test_csrf_required(server):
    session = server.create_session(
        title="x", instruction="y",
        fields=[PopupField(key="code", label="code")],
        open_browser=False,
    )
    # No _csrf → 403
    status = _post(
        f"http://127.0.0.1:{server.port}/submit/{session.session_id}",
        {"code": "123456"},
    )
    assert status == 403


def test_required_field_enforced(server):
    session = server.create_session(
        title="x", instruction="y",
        fields=[PopupField(key="code", label="code", required=True)],
        open_browser=False,
    )
    status = _post(
        f"http://127.0.0.1:{server.port}/submit/{session.session_id}",
        {"code": "", "_csrf": session.session_id},
    )
    assert status == 400
