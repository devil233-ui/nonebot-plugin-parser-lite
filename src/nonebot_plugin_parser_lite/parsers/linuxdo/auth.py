from __future__ import annotations

import asyncio
import os
from time import monotonic

from anyio import Path
from nonebot import logger

from ...config import pconfig
from ...download.client import UniHttpClient, UniResponse
from ...exception import TipException
from ...utils.cookie import ck2dict

SESSION_URL = "https://linux.do/session/current.json"
COOKIE_FILE_NAME = "linuxdo_cookies.txt"


def format_response_diagnostics(response: UniResponse) -> str:
    """格式化不含 Cookie 和正文的响应诊断信息。"""
    headers = response.headers
    cf_headers = sorted(name for name in headers if name.lower().startswith("cf-"))
    return (
        f"status={response.status_code} url={response.url} "
        f"content_type={headers.get('content-type')!r} "
        f"server={headers.get('server')!r} "
        f"content_length={headers.get('content-length')!r} "
        f"cf_headers={cf_headers}"
    )


class LinuxDoAuth:
    # 管理 Cookie、登录态验证和服务端 Cookie 续用。

    _VALIDATION_TTL = 10 * 60
    _FAILED_VALIDATION_TTL = 60
    _UNKNOWN_VALIDATION_TTL = 30

    def __init__(
        self,
        client: UniHttpClient,
        base_headers: dict[str, str],
        cookie_path: Path | None = None,
    ) -> None:
        self.client = client
        self.headers = {
            **base_headers,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Referer": "https://linux.do/",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "X-Requested-With": "XMLHttpRequest",
            "Discourse-Logged-In": "true",
        }
        self.cookie_path = cookie_path or (
            Path(str(pconfig.config_dir)) / COOKIE_FILE_NAME
        )
        self._cookies: dict[str, str] | None = None
        self._cookie_lock = asyncio.Lock()
        self._validation_lock = asyncio.Lock()
        self._validation: bool | None = None
        self._validation_expires_at = 0.0

    @staticmethod
    def _parse_cookie(raw: str) -> dict[str, str]:
        if not raw.strip():
            return {}
        try:
            return ck2dict(raw)
        except (AttributeError, TypeError, ValueError):
            logger.warning("Linux.do Cookie 格式无效，忽略该来源")
            return {}

    @staticmethod
    def _serialize_cookie(cookies: dict[str, str]) -> str:
        return "; ".join(
            f"{name}={cookies[name]}" for name in sorted(cookies)
        )

    async def _read_cookie_file(self) -> dict[str, str]:
        try:
            if await self.cookie_path.exists():
                return self._parse_cookie(await self.cookie_path.read_text())
        except Exception as exc:
            logger.warning(f"读取 Linux.do Cookie 缓存失败: {type(exc).__name__}")
        return {}

    async def _ensure_loaded(self) -> dict[str, str]:
        if self._cookies is not None:
            return self._cookies

        cached = await self._read_cookie_file()
        bootstrap = self._parse_cookie(pconfig.linuxdo_ck or "")
        self._cookies = {**cached, **bootstrap}
        return self._cookies

    async def cookies(self) -> dict[str, str]:
        async with self._cookie_lock:
            return dict(await self._ensure_loaded())

    async def _persist_locked(self) -> None:
        cookies = await self._ensure_loaded()
        if not cookies:
            return

        content = self._serialize_cookie(cookies)
        try:
            if await self.cookie_path.exists():
                if await self.cookie_path.read_text() == content:
                    return
            await self.cookie_path.parent.mkdir(parents=True, exist_ok=True)
            temporary_path = self.cookie_path.with_name(
                f".{self.cookie_path.name}.tmp"
            )
            await temporary_path.write_text(content, encoding="utf-8")
            os.chmod(str(temporary_path), 0o600)
            os.replace(str(temporary_path), str(self.cookie_path))
        except Exception as exc:
            logger.warning(
                f"保存 Linux.do Cookie 缓存失败: {type(exc).__name__}"
            )

    async def update_from_response(self, response: UniResponse) -> None:
        response_cookies = response.cookies
        async with self._cookie_lock:
            cookies = await self._ensure_loaded()
            changed = any(
                cookies.get(name) != value
                for name, value in response_cookies.items()
            )
            if changed:
                cookies.update(response_cookies)
            if changed or response.is_success:
                await self._persist_locked()

    def _set_validation(self, value: bool | None, ttl: float) -> bool | None:
        self._validation = value
        self._validation_expires_at = monotonic() + ttl
        return value

    async def validate_login(self, *, force: bool = False) -> bool | None:
        now = monotonic()
        if not force and now < self._validation_expires_at:
            return self._validation

        async with self._validation_lock:
            now = monotonic()
            if not force and now < self._validation_expires_at:
                return self._validation

            cookies = await self.cookies()
            if not cookies:
                return self._set_validation(False, self._FAILED_VALIDATION_TTL)

            try:
                response = await self.client.get(
                    SESSION_URL,
                    headers=self.headers,
                    cookies=cookies,
                    use_curl_cffi=True,
                )
            except Exception as exc:
                logger.warning(
                    f"验证 Linux.do 登录态失败: {type(exc).__name__}"
                )
                return self._set_validation(None, self._UNKNOWN_VALIDATION_TTL)

            await self.update_from_response(response)
            response_diagnostics = format_response_diagnostics(response)
            if response.status_code != 200:
                logger.warning(
                    f"Linux.do 登录态响应异常: {response_diagnostics}"
                )
                return self._set_validation(None, self._UNKNOWN_VALIDATION_TTL)

            try:
                data = response.json()
            except Exception as exc:
                logger.warning(
                    "Linux.do 登录态响应 JSON 解析失败: "
                    f"{type(exc).__name__}; {response_diagnostics}"
                )
                return self._set_validation(None, self._UNKNOWN_VALIDATION_TTL)

            logged_in = isinstance(data, dict) and bool(data.get("current_user"))
            logger.info(
                "Linux.do 登录态判定: "
                f"{response_diagnostics} "
                f"current_user={'present' if logged_in else 'absent'}"
            )
            async with self._cookie_lock:
                await self._persist_locked()
            return self._set_validation(
                logged_in,
                self._VALIDATION_TTL
                if logged_in
                else self._FAILED_VALIDATION_TTL,
            )

    async def tip_for_status(self, status_code: int) -> TipException:
        if status_code == 429:
            return TipException("Linux.do 请求过于频繁，请稍后再试")

        if status_code in {401, 403, 404}:
            cookies = await self.cookies()
            login_state = await self.validate_login(force=True)
            if login_state is False:
                if cookies:
                    return TipException(
                        "Linux.do 登录态已失效，请更新 Cookie"
                    )
                return TipException(
                    "该 Linux.do 帖子需要登录或更高等级权限，请配置 Cookie"
                )
            if login_state is True:
                if status_code == 404:
                    return TipException(
                        "Linux.do 帖子不存在，或当前账号权限/等级不足"
                    )
                return TipException("Linux.do 拒绝了请求，可能是权限或风控限制")
            return TipException(
                "Linux.do 暂时无法确认登录态，可能是权限不足或站点风控"
            )

        if 500 <= status_code < 600:
            return TipException("Linux.do 服务暂时不可用，请稍后再试")
        return TipException(f"Linux.do 请求失败（HTTP {status_code}）")
