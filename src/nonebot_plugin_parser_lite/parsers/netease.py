import contextlib
import json
from typing import Any, ClassVar
from urllib.parse import urlparse

from nonebot.log import logger

from ..config import pconfig
from ..exception import ParseException, TipException
from .base import (
    BaseParser,
    ContentItem,
    MatchWithParams,
    Platform,
    PlatformEnum,
    handle,
)

SONG_DETAIL = "https://music.163.com/api/song/detail"
SONG_LYRIC = "https://music.163.com/api/song/lyric"
OUTER_AUDIO = "https://music.163.com/song/media/outer/url"


def flatten_yrc_lyric(raw: str) -> str:
    """把逐字歌词（YRC JSON 行）转成纯文本；普通 LRC 行原样保留。

    网易云部分歌曲（尤其逐字歌词）会在 ``lrc`` 字段里混入
    ``{"t": 毫秒, "c": [{"tx": "文本"}]}`` 形式的 JSON 行，直接展示会是
    一团原始 JSON；这里把每行的 ``c[].tx`` 合并回可读文本。

    没有 JSON 行时原样返回，避免改动普通 LRC 的原始格式。
    """
    if not any(line.lstrip().startswith("{") for line in raw.splitlines()):
        return raw

    lines: list[str] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if stripped.startswith("{"):
            try:
                parsed = json.loads(stripped)
            except ValueError:
                parsed = None
            if isinstance(parsed, dict):
                content = "".join(
                    seg.get("tx", "")
                    for seg in parsed.get("c", [])
                    if isinstance(seg, dict)
                ).strip()
                if content:
                    lines.append(content)
                continue
        lines.append(line)
    return "\n".join(lines)


class NCMParser(BaseParser):
    platform: ClassVar[Platform] = Platform(
        name=PlatformEnum.NETEASE, display_name="网易云音乐"
    )

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

    async def parse_local(self, ncm_id: int) -> dict[str, Any]:
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
            url_no_params.rsplit(".", 1)[-1].lower() if "." in url_no_params else ""
        )
        supported_types = {"flac", "wav", "m4a", "aac", "mp3"}
        audio_type = url_type if url_type in supported_types else reported_type
        audio_br = url_data.get("br") or 0
        size_bytes = url_data.get("size", 0)

        # 解灰分支不返回 size；HEAD 不可用时再用 Range 响应头探测。
        if not size_bytes:
            size_bytes = await self._probe_audio_size(audio_url)

        audio_size = f"{size_bytes / (1024 * 1024):.2f}MB" if size_bytes else "未知大小"

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
            lyric_data = lyric_resp.json()
            lyric = lyric_data.get("lrc", {}).get("lyric", "")
            if not lyric and (yrc := lyric_data.get("yrc", {}).get("lyric")):
                lyric = flatten_yrc_lyric(yrc)
            if lyric:
                lyric = flatten_yrc_lyric(lyric)
                logger.info(f"找到歌词，长度: {len(lyric)}字符")
        except Exception as exc:
            logger.warning(f"获取歌词失败，忽略: {exc}")

        title = detail.get("name", "未知歌曲")
        author = "/".join(artist.get("name", "") for artist in detail.get("ar", []))
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
            "lyric": lyric,
        }

    async def _parse_local_netease(self, ncm_id: int, share_url: str):
        """构建本地解灰方案的解析结果。"""
        result = await self.parse_local(ncm_id)
        contents: list[ContentItem] = []
        if result["audio_url"]:
            contents.append(
                self.create_audio(
                    result["audio_url"],
                    duration=result.get("duration", 0.0),
                    cache_key=f"netease:{ncm_id}:local",
                )
            )
        if result["cover_url"]:
            contents.append(
                self.create_image(
                    result["cover_url"],
                    need_send=False,
                    cache_key=f"netease:{ncm_id}:cover",
                )
            )

        if result["lyric"]:
            # 与上游一致：歌词走 content 文本项，主题模板经 render_content 渲染。
            contents.append(result["lyric"])

        return self.result(
            title=result["title"],
            author=self.create_author(name=result["author"]),
            url=share_url,
            content=contents,
            extra={
                "info": result["audio_info"],
                "type": "audio",
                "type_tag": "音乐",
                "type_icon": "fa-music",
            },
        )

    @handle("163cn.tv", r"https?://[^\s]*?163cn\.tv/[a-zA-Z0-9]+")
    async def _parse_163cn(self, searched: MatchWithParams):
        return await self.parse_with_redirect(searched[0])

    @handle("music.163.com", params={"id": {"as_int": True}})
    @handle("music.163.com", r"song/(?P<id>\d+)")
    async def _parse_netease(self, searched: MatchWithParams):
        ncm_id = searched["id"]
        if pconfig.plite_netease_local_api:
            try:
                return await self._parse_local_netease(int(ncm_id), searched.url)
            except Exception as exc:
                logger.warning(f"本地网易云 API 调用失败，回退上游接口: {exc}")

        detail = await self.httpx.get(SONG_DETAIL, params={"ids": f"[{ncm_id}]"})
        detail.raise_for_status()
        info = detail.json()
        code = info.get("code")
        if code != 200:
            raise ParseException(f"网易云歌曲详情接口返回 code={code}")
        songs = info.get("songs") or []
        if not songs:
            raise TipException("网易云音乐没有查到这首歌，歌单和专辑链接暂不支持解析")
        song = songs[0]

        title = song.get("name") or "未知"
        artist = (
            "、".join(name for item in song.get("artists") or [] if (name := item.get("name")))
            or "未知歌手"
        )
        duration = float(song.get("duration") or 0) / 1000

        # 匿名可用的音源只有这条外链，只有 302 落在 /404 才是真的没有音源
        outer = await self.httpx.get(
            OUTER_AUDIO, params={"id": f"{ncm_id}.mp3"}, follow_redirects=False
        )
        location = outer.headers.get("location") or ""
        if not outer.is_redirect or not location:
            raise ParseException(f"网易云音频外链接口没有音源跳转: {outer.status_code}")
        audio_url = str(outer.url.join(location))
        if urlparse(audio_url).path.rstrip("/") == "/404":
            if song.get("fee"):
                raise TipException("网易云音乐该歌曲需要 VIP 或付费，取不到整曲音源")
            raise TipException("网易云音乐没有可播放的音频源，可能受版权限制")

        lyric = ""
        with contextlib.suppress(Exception):
            lyric_resp = await self.httpx.get(
                SONG_LYRIC, params={"id": ncm_id, "lv": "-1", "kv": "-1", "tv": "-1"}
            )
            lyric_resp.raise_for_status()
            lyric = (lyric_resp.json().get("lrc") or {}).get("lyric") or ""
            lyric = flatten_yrc_lyric(lyric)

        contents: list[ContentItem] = []
        audio = self.create_audio(
            audio_url,
            duration=duration,
            cache_key=f"netease:{ncm_id}",
        )
        contents.append(audio)

        if cover_url := (song.get("album") or {}).get("picUrl"):
            contents.append(self.create_image(cover_url))
        if lyric:
            contents.append(lyric)

        extra = {"info": f"大小: {await audio.get_display_size()}"}

        return self.result(
            title=title,
            author=self.create_author(name=artist),
            url=f"https://music.163.com/song/{ncm_id}",
            content=contents,
            extra=extra,
        )
