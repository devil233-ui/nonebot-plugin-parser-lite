import json
import re
from typing import Any, ClassVar

from httpx import AsyncClient
from nonebot import logger

from ...config import pconfig
from ...data import ContentItem, ParseResult
from ...download import yt_dlp_downloader
from ...exception import ParseException
from ...utils.cookie import save_cookies_with_netscape
from ..base import BaseParser, MatchWithParams, Platform, PlatformEnum, handle


def _join_runs(container: dict[str, Any] | None) -> str:
    """拼接 YouTube runs 结构中的文本。"""
    if not container:
        return ""
    runs = container.get("runs") or []
    return "".join(run.get("text", "") for run in runs if isinstance(run, dict))


def _best_thumbnail_url(thumbnails: list | None) -> str | None:
    """从 thumbnails 中选取最大尺寸的图片，并补全协议。"""
    if not thumbnails:
        return None

    best: str | None = None
    best_width = -1
    for thumbnail in thumbnails:
        if not isinstance(thumbnail, dict) or not thumbnail.get("url"):
            continue
        try:
            width = int(thumbnail.get("width", 0))
        except (TypeError, ValueError):
            width = 0
        if width >= best_width:
            best_width = width
            best = thumbnail["url"]

    if best is None and isinstance(thumbnails[-1], dict):
        best = thumbnails[-1].get("url")
    if best and best.startswith("//"):
        return "https:" + best
    return best


def _find_first(obj: Any, key: str) -> Any:
    """递归查找 JSON 对象中第一个指定键的值。"""
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for value in obj.values():
            if (found := _find_first(value, key)) is not None:
                return found
    elif isinstance(obj, list):
        for item in obj:
            if (found := _find_first(item, key)) is not None:
                return found
    return None


def _extract_count(text: str) -> str | None:
    """从评论数量文本中提取纯数字。"""
    if match := re.search(r"[\d,]+", text):
        return match.group(0).replace(",", "")
    return None


def _format_stat(value: int | str | None) -> str:
    """按 YouTube 卡片习惯格式化统计数字。"""
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return str(value) if value else "0"
    if number < 0:
        return "0"
    if number >= 100_000_000:
        return f"{number / 100_000_000:.1f}亿"
    if number >= 10_000:
        return f"{number / 10_000:.1f}万"
    return str(number)


def _browse_context(hl: str = "zh-HK") -> dict[str, Any]:
    """构造 YouTube InnerTube browse 请求的 context。"""
    return {
        "context": {
            "client": {
                "hl": hl,
                "gl": "US",
                "deviceMake": "Apple",
                "deviceModel": "",
                "clientName": "WEB",
                "clientVersion": "2.20251002.00.00",
                "osName": "Macintosh",
                "osVersion": "10_15_7",
            },
            "user": {"lockedSafetyMode": False},
            "request": {
                "useSsl": True,
                "internalExperimentFlags": [],
                "consistencyTokenJars": [],
            },
        }
    }

if yt_dlp_downloader is not None:

    class YouTubeParser(BaseParser):
        platform: ClassVar[Platform] = Platform(
            name=PlatformEnum.YOUTUBE,
            display_name="油管",
        )

        def __init__(self):
            super().__init__()
            self.cookies_file = pconfig.config_dir / "ytb_cookies.txt"
            if pconfig.ytb_ck:
                save_cookies_with_netscape(
                    pconfig.ytb_ck,
                    self.cookies_file,
                    "youtube.com",
                )

        @handle("youtu", r"youtu\.be/[A-Za-z\d\._\?%&\+\-=/#]+")
        @handle(
            "youtube",
            r"youtube\.com/(?:watch|shorts|live|post)(?:/[A-Za-z\d_\-]+|\?v=[A-Za-z\d_\-]+)",
        )
        async def _parse_video(self, searched: MatchWithParams) -> ParseResult:
            url = f"https://{searched.url}"
            if "/post/" in url:
                return await self.parse_post(url)
            return await self.parse_video(url)

        async def parse_video(self, url: str) -> ParseResult:
            """解析油管视频并创建惰性视频内容。"""
            if yt_dlp_downloader is None:  # pragma: no cover - class 不会在此时注册
                raise ParseException("未安装 yt-dlp，无法解析油管链接")

            video_info = await yt_dlp_downloader.extract_video_info(
                url,
                self.cookies_file,
            )
            try:
                author = await self._fetch_author_info(video_info.channel_id)
            except Exception as e:
                logger.warning(f"获取油管频道信息失败，使用视频元数据回退: {e}")
                author = self.create_author(
                    name=video_info.author_name or video_info.channel or "未知频道"
                )

            display_stats = []
            if video_info.is_live and video_info.concurrent_view_count is not None:
                display_stats.append(
                    {
                        "icon": "eye",
                        "value": _format_stat(video_info.concurrent_view_count),
                        "label": "正在观看",
                    }
                )
            view_label = "累计观看" if video_info.is_live else "观看"
            for icon, value, label in (
                ("eye", video_info.view_count, view_label),
                ("like", video_info.like_count, "点赞"),
                ("comment", video_info.comment_count, "评论"),
            ):
                if value is not None:
                    display_stats.append(
                        {"icon": icon, "value": _format_stat(value), "label": label}
                    )

            extra: dict[str, Any] = {
                "content_type": "直播" if video_info.is_live else "视频"
            }
            if display_stats:
                extra["stats"] = display_stats

            content: list[ContentItem] = []
            if description := video_info.description.strip():
                content.append(f"简介: {description}")

            duration = video_info.duration or 0.0
            if video_info.is_live or video_info.duration is None:
                if video_info.thumbnail:
                    content.append(self.create_image(video_info.thumbnail))
            elif duration <= pconfig.duration_maximum:
                video = yt_dlp_downloader.download_video(
                    url=url,
                    cookiefile=self.cookies_file,
                )
                video_content = self.create_video(
                    video,
                    cover_url=video_info.thumbnail or None,
                    duration=duration,
                    cache_key=f"youtube:{url}",
                )
                if video_info.size_bytes:
                    video_content._size_bytes = video_info.size_bytes
                content.append(video_content)
            elif video_info.thumbnail:
                content.append(self.create_image(video_info.thumbnail))

            return self.result(
                author=author,
                title=video_info.title,
                timestamp=video_info.timestamp,
                url=url,
                content=content,
                stats=self.create_stats(
                    view_count=(
                        _format_stat(video_info.view_count)
                        if video_info.view_count is not None
                        else None
                    ),
                    like_count=(
                        _format_stat(video_info.like_count)
                        if video_info.like_count is not None
                        else None
                    ),
                    comment_count=(
                        _format_stat(video_info.comment_count)
                        if video_info.comment_count is not None
                        else None
                    ),
                ),
                extra=extra,
            )

        async def parse_post(self, url: str) -> ParseResult:
            """解析 YouTube 社区帖子，支持文字、单图和多图帖子。"""
            response = await self.httpx.get(url)
            response.raise_for_status()

            match = re.search(
                r"var ytInitialData = (\{.*?\});\s*</script>",
                response.text,
                re.DOTALL,
            )
            if not match:
                raise ParseException("获取油管帖子信息失败")

            data = json.loads(match.group(1))
            post = _find_first(data, "backstagePostRenderer")
            if not isinstance(post, dict):
                raise ParseException("获取油管帖子信息失败")

            author_name = _join_runs(post.get("authorText")) or "YouTube"
            author_avatar = _best_thumbnail_url(
                (post.get("authorThumbnail") or {}).get("thumbnails")
            )
            text = _join_runs(post.get("contentText")) or None
            images = self._extract_post_images(post)

            vote_count = post.get("voteCount") or {}
            like = vote_count.get("simpleText") or _join_runs(vote_count)
            comment_text = await self._fetch_post_comment_count(data)
            published = _join_runs(post.get("publishedTimeText"))

            display_stats = []
            if like:
                display_stats.append({"icon": "like", "value": like, "label": "赞"})
            if comment_text and (comment_num := _extract_count(comment_text)):
                display_stats.append(
                    {"icon": "comment", "value": comment_num, "label": "评论"}
                )

            info_parts = []
            if like:
                info_parts.append(f"{like} 赞")
            if comment_text:
                info_parts.append(comment_text)
            if tip := self._post_attachment_tip(post.get("backstageAttachment")):
                info_parts.append(tip)

            extra: dict[str, Any] = {"content_type": "图文"}
            if display_stats:
                extra["stats"] = display_stats
            if info_parts:
                extra["info"] = " · ".join(info_parts)
            if tip:
                extra["attachment_tip"] = tip
            if published:
                extra["datetime_text"] = published

            content: list[ContentItem] = []
            if text:
                content.append(text)
            content.extend(self.create_images(images))

            return self.result(
                author=self.create_author(author_name, author_avatar),
                url=url,
                content=content,
                stats=self.create_stats(
                    like_count=like,
                    comment_count=_extract_count(comment_text)
                    if comment_text
                    else None,
                ),
                extra=extra,
            )

        @staticmethod
        def _extract_post_images(post: dict[str, Any]) -> list[str]:
            """提取帖子中的图片 URL（支持单图和多图）。"""
            attachment = post.get("backstageAttachment") or {}
            images: list[str] = []

            if multi := attachment.get("postMultiImageRenderer"):
                for item in multi.get("images") or []:
                    if not isinstance(item, dict):
                        continue
                    renderer = item.get("backstageImageRenderer") or {}
                    if url := _best_thumbnail_url(
                        (renderer.get("image") or {}).get("thumbnails")
                    ):
                        images.append(url)
            elif renderer := attachment.get("backstageImageRenderer"):
                if url := _best_thumbnail_url(
                    (renderer.get("image") or {}).get("thumbnails")
                ):
                    images.append(url)

            return images

        @staticmethod
        def _post_attachment_tip(attachment: Any) -> str | None:
            """对当前未直接展开的帖子附件返回提示。"""
            if not isinstance(attachment, dict):
                return None
            if "pollRenderer" in attachment:
                return "投票帖暂不支持解析"
            if "videoRenderer" in attachment or "playlistRenderer" in attachment:
                return "引用视频/播放列表暂不支持解析"
            return None

        async def _fetch_author_info(self, channel_id: str):
            """读取频道名称、头像和简介。"""
            if not channel_id:
                raise ParseException("油管返回结果缺少频道 ID")

            from . import meta

            url = "https://www.youtube.com/youtubei/v1/browse?prettyPrint=false"
            payload = {**_browse_context(), "browseId": channel_id}

            async with AsyncClient(
                headers=self.headers,
                timeout=self.timeout,
            ) as client:
                response = await client.post(url, json=payload)
                response.raise_for_status()

            browse = meta.decoder.decode(response.content)
            return self.create_author(
                name=browse.name,
                avatar_url=browse.avatar_url,
                description=browse.description,
                id=channel_id,
            )

        async def _fetch_post_comment_count(self, data: dict[str, Any]) -> str | None:
            """获取帖子评论数文本，失败时静默返回 None。"""
            try:
                command = _find_first(data, "continuationCommand")
                token = command.get("token") if isinstance(command, dict) else None
                if not token:
                    return None

                url = "https://www.youtube.com/youtubei/v1/browse?prettyPrint=false"
                payload = {**_browse_context("zh-CN"), "continuation": token}
                response = await self.httpx.post(url, json=payload)
                response.raise_for_status()

                header = _find_first(response.json(), "commentsHeaderRenderer")
                if isinstance(header, dict):
                    return _join_runs(header.get("countText")) or None
            except Exception:
                logger.debug("获取油管帖子评论数失败", exc_info=True)
            return None


__all__ = ["YouTubeParser"] if yt_dlp_downloader is not None else []
