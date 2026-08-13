"""Integration tests for the webhook server (mocked Telegram application)."""

import asyncio
from unittest.mock import MagicMock

import pytest
from aiohttp.test_utils import TestClient, TestServer

import main
from bot.config import Config


def make_config() -> Config:
    return Config(  # type: ignore[call-arg]
        telegram_bot_token="123456:dummy-token",
        webhook_secret="s3cret",
    )


def make_mock_application():
    app = MagicMock()
    app.update_queue = asyncio.Queue()
    app.bot_data = {"manager": MagicMock(active_jobs=MagicMock(return_value=0))}
    return app


@pytest.fixture
async def client():
    config = make_config()
    application = make_mock_application()
    aio_app = main.make_web_app(application, config)
    test_client = TestClient(TestServer(aio_app))
    await test_client.start_server()
    yield test_client, application
    await test_client.close()


async def test_health_endpoint(client):
    test_client, _ = client
    resp = await test_client.get("/")
    assert resp.status == 200
    body = await resp.json()
    assert body["status"] in ("ok", "degraded")
    assert "free_disk_mb" in body


async def test_webhook_rejects_missing_secret(client):
    # design doc gotcha #8: anyone who knows /webhook must NOT get through
    test_client, application = client
    resp = await test_client.post("/webhook", json={"update_id": 1})
    assert resp.status == 403
    assert application.update_queue.qsize() == 0


async def test_webhook_rejects_wrong_secret(client):
    test_client, application = client
    resp = await test_client.post(
        "/webhook",
        json={"update_id": 1},
        headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
    )
    assert resp.status == 403
    assert application.update_queue.qsize() == 0


async def test_webhook_accepts_correct_secret(client):
    test_client, application = client
    resp = await test_client.post(
        "/webhook",
        json={"update_id": 1},
        headers={"X-Telegram-Bot-Api-Secret-Token": "s3cret"},
    )
    assert resp.status == 200
    assert application.update_queue.qsize() == 1


async def test_webhook_rejects_invalid_json(client):
    test_client, _ = client
    resp = await test_client.post(
        "/webhook",
        data=b"not-json",
        headers={
            "X-Telegram-Bot-Api-Secret-Token": "s3cret",
            "Content-Type": "application/json",
        },
    )
    assert resp.status == 400
