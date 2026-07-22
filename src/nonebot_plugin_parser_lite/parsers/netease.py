import contextlib
import random
import time
from typing import ClassVar

from nonebot import logger

from ..config import pconfig
from ..data import MediaContent, Platform
from .base import (
    BaseParser,
    ContentItem,
    MatchWithParams,
    ParseException,
    PlatformEnum,
    handle,
)


def random_ip() -> str:
    return ".".join(str(random.randint(0, 255)) for _ in range(4))


def parse_duration_to_seconds(duration: str) -> int:
    """将时长字符串解析为总秒数。"""
    parts = duration.split(":")
    if not (1 <= len(parts) <= 3):
        raise ValueError(f"非法的时长格式: {duration!r}")

    try:
        parts_int = [int(p) for p in parts]
    except ValueError as exc:
        raise ValueError(f"时长中包含非法数字: {duration!r}") from exc

    if len(parts_int) == 1:
        hours = minutes = 0
        seconds = parts_int[0]
    elif len(parts_int) == 2:
        hours = 0
        minutes, seconds = parts_int
    else:
        hours, minutes, seconds = parts_int

    if not (0 <= seconds < 60 and 0 <= minutes < 60 and hours >= 0):
        raise ValueError(f"时长数值不合法: {duration!r}")

    return hours * 3600 + minutes * 60 + seconds


class NCMParser(BaseParser):
    platform: ClassVar[Platform] = Platform(
        name=PlatformEnum.NETEASE, display_name="网易云音乐"
    )

    def __init__(self):
        super().__init__()
        self.httpx.headers.update({"Referer": "https://wyapi.toubiec.cn/"})
        self.httpx.base_url = "https://nextmusic.toubiec.cn/api"

    async def fetch(self, endpoint: str, payload: dict) -> dict:
        payload["timestamp"] = int(time.time() * 1000)
        payload["ip"] = random_ip()
        resp = await self.httpx.post(endpoint, json=payload)
        resp.raise_for_status()
        result = resp.json()
        if result.get("code") != 200:
            raise ParseException(f"接口返回错误: {result}")
        return result["data"]

    async def _probe_audio_size(self, audio_url: str) -> int:
        """探测音频大小；HEAD 不可用时仅读取 Range 响应头。"""
        try:
            head_resp = await self.httpx.head(
                audio_url, follow_redirects=True, timeout=5.0
            )
            head_resp.raise_for_status()
            if size := int(head_resp.headers.get("Content-Length", 0)):
                return size
        except Exception:
            pass

        try:
            async with self.httpx.stream(
                "GET",
                audio_url,
                headers={"Range": "bytes=0-0"},
                follow_redirects=True,
                timeout=8.0,
            ) as range_resp:
                range_resp.raise_for_status()
                content_range = range_resp.headers.get("Content-Range", "")
                total = content_range.rsplit("/", 1)[-1]
                if total.isdigit():
                    return int(total)
                if range_resp.status_code == 200:
                    return int(range_resp.headers.get("Content-Length", 0))
        except Exception as exc:
            logger.warning(f"探测解灰音频大小失败: {exc}")
        return 0

    async def parse_local(self, ncm_id: int) -> dict:
        """使用本地 API 解灰，并返回完整的歌曲信息。"""
        base_url = (pconfig.plite_netease_local_api or "").rstrip("/")
        url_resp = await self.httpx.get(
            f"{base_url}/song/url/v1",
            params={"id": ncm_id, "level": "standard", "unblock": "true"},
            timeout=30.0,
        )
        url_resp.raise_for_status()
        url_result = url_resp.json()
        url_data = (url_result.get("data") or [{}])[0]
        if url_result.get("code") != 200 or not url_data.get("url"):
            raise ParseException(f"本地网易云 API 无法获取音频地址: {url_result}")

        audio_url = url_data["url"]
        reported_type = (url_data.get("type") or "mp3").lower()
        url_no_params = audio_url.split("?", 1)[0]
        url_type = (
            url_no_params.rsplit(".", 1)[-1].lower()
            if "." in url_no_params
            else ""
        )
        supported_types = {"flac", "wav", "m4a", "aac", "mp3"}
        audio_type = url_type if url_type in supported_types else reported_type
        audio_br = url_data.get("br") or 0
        size_bytes = url_data.get("size", 0)

        # 解灰分支不返回 size；HEAD 不可用时再用 Range 响应头探测。
        if not size_bytes:
            size_bytes = await self._probe_audio_size(audio_url)

        audio_size = (
            f"{size_bytes / (1024 * 1024):.2f}MB" if size_bytes else "未知大小"
        )

        detail_resp = await self.httpx.get(
            f"{base_url}/song/detail", params={"ids": ncm_id}, timeout=10.0
        )
        detail_resp.raise_for_status()
        detail_result = detail_resp.json()
        detail = (detail_result.get("songs") or [{}])[0]
        if not detail:
            raise ParseException(f"本地网易云 API 无法获取歌曲信息: {detail_result}")

        duration = (detail.get("dt") or 0) / 1000
        if not audio_br and size_bytes and duration:
            audio_br = round(size_bytes * 8 / duration)
        br_str = f"({audio_br // 1000}kbps)" if audio_br else ""

        lyric = ""
        try:
            lyric_resp = await self.httpx.get(
                f"{base_url}/lyric", params={"id": ncm_id}, timeout=10.0
            )
            lyric_resp.raise_for_status()
            lyric = lyric_resp.json().get("lrc", {}).get("lyric", "")
            if lyric:
                logger.info(f"找到歌词，长度: {len(lyric)}字符")
        except Exception as exc:
            logger.warning(f"获取歌词失败，忽略: {exc}")

        title = detail.get("name", "未知歌曲")
        author = "/".join(
            artist.get("name", "") for artist in detail.get("ar", [])
        )
        logger.info(f"本地网易云 API 解析成功: {title} - {author}")

        # 不信任 API 的 level，使用真实码率和格式判断音质。
        if audio_type == "flac" or (audio_br and audio_br >= 700000):
            quality_tag = "无损"
        elif audio_br and audio_br >= 320000:
            quality_tag = "极高"
        else:
            quality_tag = "标准"

        audio_info = (
            f"音质: {quality_tag}{br_str} | 格式: {audio_type.upper()}"
            f" | 大小: {audio_size}"
        )
        return {
            "title": title,
            "author": author,
            "duration": duration,
            "audio_info": audio_info,
            "cover_url": detail.get("al", {}).get("picUrl", ""),
            "audio_url": audio_url,
            "audio_type": audio_type,
            "mv_info": {},
            "lyric": lyric,
        }

    async def _parse_local_netease(self, ncm_id: int, share_url: str):
        """构建本地解灰方案的解析结果。"""
        result = await self.parse_local(ncm_id)
        contents: list[MediaContent] = []
        if result["audio_url"]:
            audio_type = result.get("audio_type", "mp3")
            contents.append(
                self.create_audio(
                    result["audio_url"],
                    duration=result.get("duration", 0.0),
                    audio_name=f"{result['title']}-{result['author']}.{audio_type}",
                )
            )
        if result["cover_url"]:
            contents.append(self.create_image(result["cover_url"], need_send=False))

        text = result["audio_info"]
        if result["lyric"]:
            text += f"\n歌词:\n{result['lyric']}"
        return self.result(
            title=result["title"],
            author=self.create_author(name=result["author"]),
            url=share_url,
            content=contents,
            extra={
                "info": result["audio_info"],
                "lyric": text,
                "type": "audio",
                "type_tag": "音乐",
                "type_icon": "fa-music",
            },
        )

    @handle("163cn.tv", r"https?://[^\s]*?163cn\.tv/[a-zA-Z0-9]+")
    async def _parse_163cn(self, searched: MatchWithParams):
        return await self.parse_with_redirect(searched[0])

    @handle("y.music.163.com", params={"id": {"as_int": True}})
    @handle("music.163.com", params={"id": {"as_int": True}})
    @handle("music.163.com", r"song/(?P<id>\d+)")
    async def _parse_netease(self, searched: MatchWithParams):
        ncm_id = searched["id"]
        if pconfig.plite_netease_local_api:
            try:
                return await self._parse_local_netease(ncm_id, searched.url)
            except Exception as exc:
                logger.warning(f"本地网易云 API 调用失败，回退上游接口: {exc}")

        song = await self.fetch("getSongInfo", {"id": ncm_id})
        title = song.get("name", "未知")
        artist = song.get("singer", "未知歌手")
        duration = parse_duration_to_seconds(song.get("duration", "0"))
        lyric = ""
        with contextlib.suppress(Exception):
            lyric = (await self.fetch("getSongLyric", {"id": ncm_id})).get("lrc")
        url_data = await self.fetch("getSongUrl", {"id": ncm_id, "level": "standard"})
        if not (audio_url := url_data.get("url")):
            raise ParseException("无法获取音频下载地址")
        url_no_params = audio_url.split("?", 1)[0]
        ext = url_no_params.rsplit(".", 1)[-1].lower() if "." in url_no_params else ""
        audio_type = ext if ext in {"flac", "wav", "m4a", "aac", "mp3"} else "mp3"
        contents: list[ContentItem] = []

        audio_name = f"{title}-{artist}.{audio_type}"
        audio = self.create_audio(
            audio_url,
            duration=duration,
            audio_name=audio_name,
        )
        contents.append(audio)

        if cover_url := song.get("picimg"):
            contents.append(self.create_image(cover_url))

        audio_info = f"大小: {await audio.get_display_size()} | 格式: {audio_type}"

        extra = {
            "info": audio_info,
            "lyric": lyric,
        }

        return self.result(
            title=title,
            author=self.create_author(name=artist),
            url=f"https://music.163.com/song/{ncm_id}",
            content=contents,
            extra=extra,
        )
