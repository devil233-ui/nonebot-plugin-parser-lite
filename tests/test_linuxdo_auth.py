"""Linux.do 登录态校验与 Cookie 续用的离线单元测试（不联网）。"""

from typing import Any

import pytest
from render_test_support import ROOT  # noqa: F401  初始化插件包导入环境

from nonebot_plugin_parser_lite.config import pconfig
from nonebot_plugin_parser_lite.parsers.linuxdo.auth import (
    SESSION_URL,
    LinuxDoAuth,
    drop_cleared_cookies,
    format_response_diagnostics,
    is_cleared_cookie,
)


class FakeResponse:
    """`UniResponse` 的最小替身，只覆盖 auth 模块用到的接口。"""

    def __init__(
        self,
        status_code: int,
        *,
        payload: Any = None,
        body: bytes = b"",
        cookies: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self._payload = payload
        self.content = body
        self.cookies = dict(cookies or {})
        self.url = SESSION_URL
        self.headers = {
            "content-type": "application/json; charset=utf-8",
            "server": "cloudflare",
        }

    @property
    def is_success(self) -> bool:
        return 200 <= self.status_code < 300

    def json(self) -> Any:
        if self._payload is None:
            raise ValueError("响应不是 JSON")
        return self._payload


class FakeClient:
    """按顺序返回预置响应或抛出预置异常，并记录请求过的 URL。"""

    def __init__(self, *responses: object) -> None:
        self._responses = list(responses)
        self.calls: list[str] = []

    async def get(self, url: str, **kwargs: Any) -> Any:
        self.calls.append(url)
        if not self._responses:
            raise AssertionError(f"未预期的请求: {url}")
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def make_auth(
    client: FakeClient,
    tmp_path,
    *,
    bootstrap: str | None = None,
    monkeypatch=None,
) -> LinuxDoAuth:
    if monkeypatch is not None:
        monkeypatch.setattr(pconfig, "plite_linuxdo_ck", bootstrap)
    return LinuxDoAuth(client, {}, cookie_path=tmp_path / "linuxdo_cookies.txt")


def test_cleared_cookie_values() -> None:
    for value in (
        None,
        "",
        "   ",
        "deleted",
        '"deleted"',
        "NULL",
        "None",
        "undefined",
        "-",
    ):
        assert is_cleared_cookie(value) is True
    for value in ("1", "abc", "V2hhdGV2ZXI"):
        assert is_cleared_cookie(value) is False


def test_drop_cleared_cookies() -> None:
    cookies = {"_t": "real", "_forum_session": "deleted", "empty": ""}
    assert drop_cleared_cookies(cookies, source="测试") == {"_t": "real"}
    assert drop_cleared_cookies({}, source="测试") == {}


def test_diagnostics_do_not_leak_cookie_values() -> None:
    response = FakeResponse(403, cookies={"_t": "super-secret-value"})
    response.headers["set-cookie"] = "_t=super-secret-value"
    text = format_response_diagnostics(response)
    assert "super-secret-value" not in text
    assert "status=403" in text


@pytest.mark.asyncio
async def test_validate_login_true_and_cached(tmp_path, monkeypatch) -> None:
    client = FakeClient(
        FakeResponse(200, payload={"current_user": {"id": 1, "username": "neo"}})
    )
    auth = make_auth(client, tmp_path, bootstrap="_t=abc", monkeypatch=monkeypatch)

    assert await auth.validate_login(force=True) is True
    assert await auth.validate_login() is True
    assert client.calls == [SESSION_URL]


@pytest.mark.asyncio
async def test_validate_login_false_when_current_user_absent(
    tmp_path, monkeypatch
) -> None:
    client = FakeClient(FakeResponse(200, payload={}))
    auth = make_auth(client, tmp_path, bootstrap="_t=abc", monkeypatch=monkeypatch)

    assert await auth.validate_login(force=True) is False


@pytest.mark.asyncio
async def test_validate_login_404_means_anonymous(tmp_path, monkeypatch) -> None:
    # Discourse 对匿名请求的 /session/current.json 返回 404
    client = FakeClient(FakeResponse(404))
    auth = make_auth(client, tmp_path, bootstrap="_t=abc", monkeypatch=monkeypatch)

    assert await auth.validate_login(force=True) is False


@pytest.mark.asyncio
async def test_validate_login_unknown_on_server_error(tmp_path, monkeypatch) -> None:
    client = FakeClient(FakeResponse(500))
    auth = make_auth(client, tmp_path, bootstrap="_t=abc", monkeypatch=monkeypatch)

    assert await auth.validate_login(force=True) is None


@pytest.mark.asyncio
async def test_validate_login_unknown_on_transport_error(
    tmp_path, monkeypatch
) -> None:
    client = FakeClient(RuntimeError("connection reset"))
    auth = make_auth(client, tmp_path, bootstrap="_t=abc", monkeypatch=monkeypatch)

    assert await auth.validate_login(force=True) is None


@pytest.mark.asyncio
async def test_validate_login_without_cookies_skips_request(
    tmp_path, monkeypatch
) -> None:
    client = FakeClient()
    auth = make_auth(client, tmp_path, bootstrap=None, monkeypatch=monkeypatch)

    assert await auth.validate_login(force=True) is False
    assert client.calls == []


@pytest.mark.asyncio
async def test_update_from_response_persists_with_0600(tmp_path, monkeypatch) -> None:
    cookie_path = tmp_path / "linuxdo_cookies.txt"
    auth = LinuxDoAuth(FakeClient(), {}, cookie_path=cookie_path)
    monkeypatch.setattr(pconfig, "plite_linuxdo_ck", None)

    await auth.update_from_response(
        FakeResponse(200, cookies={"_t": "token", "gone": "deleted"})
    )

    assert cookie_path.read_text() == "_t=token"
    assert cookie_path.stat().st_mode & 0o777 == 0o600


@pytest.mark.asyncio
async def test_bootstrap_cookie_overrides_cache(tmp_path, monkeypatch) -> None:
    cookie_path = tmp_path / "linuxdo_cookies.txt"
    cookie_path.write_text("_t=old; cached=1")
    monkeypatch.setattr(pconfig, "plite_linuxdo_ck", "_t=new")

    auth = LinuxDoAuth(FakeClient(), {}, cookie_path=cookie_path)

    assert await auth.cookies() == {"_t": "new", "cached": "1"}


@pytest.mark.asyncio
async def test_tip_for_status_reports_expired_login(tmp_path, monkeypatch) -> None:
    client = FakeClient(FakeResponse(404))
    auth = make_auth(client, tmp_path, bootstrap="_t=abc", monkeypatch=monkeypatch)

    tip = await auth.tip_for_status(403)
    assert "登录态已失效" in tip.message


@pytest.mark.asyncio
async def test_tip_for_status_rate_limit(tmp_path, monkeypatch) -> None:
    auth = make_auth(FakeClient(), tmp_path, bootstrap=None, monkeypatch=monkeypatch)

    tip = await auth.tip_for_status(429)
    assert "过于频繁" in tip.message
