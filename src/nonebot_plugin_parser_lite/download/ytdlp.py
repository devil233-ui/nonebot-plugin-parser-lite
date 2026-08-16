import asyncio
from collections import defaultdict
import copy
import os
import shutil
from typing import Any

from anyio import Path
from httpx import AsyncClient, Headers
from msgspec import Struct, convert
from nonebot import logger
import yt_dlp

from ..config import pconfig
from ..exception import DownloadException, ParseException, SizeLimitException
from ..utils.common import LimitedSizeDict, generate_file_name
from .task import auto_task

_NODE_IPC_ENV_KEYS = ("NODE_CHANNEL_FD", "NODE_CHANNEL_SERIALIZATION_MODE")


def _install_node_runtime_env_guard() -> None:
    """避免 PM2 的 Node IPC 环境污染 yt-dlp challenge 子进程。"""
    from yt_dlp.extractor.youtube.jsc._builtin import node as node_jsc

    original_popen = node_jsc.Popen
    if getattr(original_popen, "_plite_pm2_env_guard", False):
        return

    def guarded_popen(*args: Any, **kwargs: Any):
        provided_env = kwargs.get("env")
        child_env = dict(os.environ if provided_env is None else provided_env)
        for key in _NODE_IPC_ENV_KEYS:
            child_env.pop(key, None)
        kwargs["env"] = child_env
        return original_popen(*args, **kwargs)

    setattr(guarded_popen, "_plite_pm2_env_guard", True)
    node_jsc.Popen = guarded_popen
    if any(key in os.environ for key in _NODE_IPC_ENV_KEYS):
        logger.info("已隔离 PM2 Node IPC 环境，避免影响 yt-dlp JS challenge")


class VideoInfo(Struct):
    """yt-dlp 返回的油管视频元数据。"""

    title: str = ""
    channel: str = ""
    uploader: str = ""
    duration: float | None = None
    timestamp: int | None = None
    thumbnail: str = ""
    description: str = ""
    channel_id: str = ""
    view_count: int | None = None
    concurrent_view_count: int | None = None
    like_count: int | None = None
    comment_count: int | None = None
    repost_count: int | None = None
    live_status: str | None = None
    size_bytes: int | None = None

    @property
    def is_live(self) -> bool:
        """是否为正在直播或预约中的直播。"""
        return self.live_status in ("is_live", "is_upcoming")

    @property
    def author_name(self) -> str:
        return f"{self.channel}@{self.uploader}".strip("@")


def _cookiefile_option(cookiefile: os.PathLike[str] | str | None) -> str | None:
    """只把存在的 Cookie 文件交给 yt-dlp，避免空配置阻塞解析器加载。"""
    if cookiefile is None:
        return None
    path = os.fspath(cookiefile)
    return path if os.path.isfile(path) else None


def _positive_size(value: Any) -> int | None:
    try:
        size = int(value)
    except (TypeError, ValueError):
        return None
    return size if size > 0 else None


def _selected_formats(info_dict: dict[str, Any]) -> list[dict[str, Any]]:
    requested_formats = info_dict.get("requested_formats")
    if isinstance(requested_formats, list) and requested_formats:
        return [item for item in requested_formats if isinstance(item, dict)]
    if info_dict.get("url"):
        return [info_dict]
    return []


def _estimate_media_size(info_dict: dict[str, Any]) -> int | None:
    """汇总 yt-dlp 已选视频/音频流的大小，作为合并文件的展示大小。"""
    selected_formats = _selected_formats(info_dict)
    if len(selected_formats) > 1:
        sizes: list[int] = []
        for format_info in selected_formats:
            size = _positive_size(format_info.get("filesize"))
            if size is None:
                size = _positive_size(format_info.get("filesize_approx"))
            if size is None:
                break
            sizes.append(size)
        else:
            if sizes:
                return sum(sizes)

    return _positive_size(info_dict.get("filesize")) or _positive_size(
        info_dict.get("filesize_approx")
    )


def _size_from_headers(headers: Headers) -> int | None:
    content_range = headers.get("Content-Range", "")
    if "/" in content_range:
        return _positive_size(content_range.rsplit("/", 1)[-1])
    return _positive_size(headers.get("Content-Length"))


async def _probe_format_size(
    client: AsyncClient,
    format_info: dict[str, Any],
) -> int | None:
    url = format_info.get("url")
    if not isinstance(url, str) or not url:
        return None
    headers = dict(format_info.get("http_headers") or {})
    try:
        response = await client.head(url, headers=headers)
        if response.is_success and (size := _size_from_headers(response.headers)):
            return size
    except Exception as e:
        logger.debug(f"HEAD 探测油管格式 {format_info.get('format_id')} 大小失败: {e}")

    headers["Range"] = "bytes=0-0"
    try:
        async with client.stream("GET", url, headers=headers) as response:
            if response.is_success:
                return _size_from_headers(response.headers)
    except Exception as e:
        logger.debug(f"Range 探测油管格式 {format_info.get('format_id')} 大小失败: {e}")
    return None


async def _probe_media_size(info_dict: dict[str, Any]) -> int | None:
    selected_formats = _selected_formats(info_dict)
    if not selected_formats:
        return None
    async with AsyncClient(follow_redirects=True, timeout=10.0) as client:
        sizes = await asyncio.gather(
            *(_probe_format_size(client, item) for item in selected_formats)
        )
    if any(size is None for size in sizes):
        return None
    return sum(size for size in sizes if size is not None)


class YtdlpDownloader:
    """基于 yt-dlp 的惰性油管下载器。"""

    def __init__(self):
        _install_node_runtime_env_guard()
        self._video_info_mapping = LimitedSizeDict[str, VideoInfo]()
        self._info_dict_mapping = LimitedSizeDict[str, dict[str, Any]]()
        self._video_format = "bv*+ba/b"
        self._extract_base_opts: dict[str, Any] = {
            "quiet": True,
            "skip_download": True,
            "force_generic_extractor": True,
            # 元数据提取不应因当前响应暂时没有可选媒体格式而失败。
            "ignore_no_formats_error": True,
            "noplaylist": True,
            "format": self._video_format,
            "merge_output_format": "mp4",
        }
        self._download_base_opts: dict[str, Any] = {
            "noplaylist": True,
        }
        if js_runtime := self._find_js_runtime():
            self._extract_base_opts["js_runtimes"] = js_runtime
            self._download_base_opts["js_runtimes"] = js_runtime
        self._url_locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    @staticmethod
    def _find_js_runtime() -> dict[str, dict[str, str]] | None:
        """按 yt-dlp 推荐顺序启用主机上可用的 JS runtime。"""
        for runtime in ("deno", "node", "bun", "quickjs"):
            if path := shutil.which(runtime):
                return {runtime: {"path": path}}
        return None

    async def extract_video_info(
        self,
        url: str,
        cookiefile: os.PathLike[str] | str | None = None,
    ) -> VideoInfo:
        """使用 yt-dlp 提取视频信息，不下载媒体。"""
        if video_info := self._video_info_mapping.get(url):
            return video_info

        ydl_opts = self._extract_base_opts.copy()
        if cookie_path := _cookiefile_option(cookiefile):
            ydl_opts["cookiefile"] = cookie_path

        info_dict: dict[str, Any] | None = None
        for attempt in range(1, 3):
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info_dict = await asyncio.to_thread(
                    ydl.extract_info,
                    url,
                    download=False,
                )
            if info_dict and _selected_formats(info_dict):
                break
            if attempt == 1:
                logger.warning("油管首次提取未返回可下载格式，重新提取一次")
        if not info_dict or not _selected_formats(info_dict):
            raise ParseException("获取油管视频信息失败：未返回可下载格式")

        video_info = convert(info_dict, VideoInfo, strict=False)
        video_info.size_bytes = _estimate_media_size(info_dict)
        if video_info.size_bytes is None:
            video_info.size_bytes = await _probe_media_size(info_dict)
            if video_info.size_bytes is None:
                logger.warning("yt-dlp 未返回油管媒体大小，源站探测也未获得结果")
        self._video_info_mapping[url] = video_info
        self._info_dict_mapping[url] = info_dict
        return video_info

    @auto_task
    async def download_video(
        self,
        url: str,
        cookiefile: os.PathLike[str] | str | None = None,
        ext_headers: dict[str, str] | None = None,
        use_curl_cffi: bool = False,
    ) -> Path:
        """使用 yt-dlp 下载并合并油管视频。"""
        del ext_headers, use_curl_cffi
        video_info = await self.extract_video_info(url, cookiefile)
        duration = video_info.duration or 0
        if duration > pconfig.duration_maximum:
            raise DownloadException(
                f"视频时长 {duration:.0f} 秒，超过 {pconfig.duration_maximum} 秒"
            )
        if video_info.size_bytes:
            size_mb = video_info.size_bytes / 1024 / 1024
            if size_mb > pconfig.max_size:
                raise SizeLimitException(size_mb)

        file_name = generate_file_name(url)
        video_path = pconfig.cache_dir / f"{file_name}.mp4"
        if await video_path.exists():
            return video_path

        async with self._url_locks[url]:
            if await video_path.exists():
                return video_path

            await pconfig.cache_dir.mkdir(parents=True, exist_ok=True)
            ydl_opts = self._download_base_opts.copy()
            ydl_opts.update(
                {
                    "outtmpl": str(video_path),
                    "merge_output_format": "mp4",
                    # 不用 filesize 条件筛选：部分视频没有该字段，会导致所有格式被过滤。
                    "format": self._video_format,
                    "postprocessors": [
                        {
                            "key": "FFmpegVideoConvertor",
                            "preferedformat": "mp4",
                        }
                    ],
                }
            )
            if cookie_path := _cookiefile_option(cookiefile):
                ydl_opts["cookiefile"] = cookie_path

            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info_dict = self._info_dict_mapping.get(url)
                    if info_dict is None:
                        raise DownloadException("缺少油管首次提取结果")
                    await asyncio.to_thread(
                        ydl.process_info,
                        copy.deepcopy(info_dict),
                    )
            except Exception:
                if await video_path.exists():
                    logger.warning(
                        "yt-dlp 下载视频报错但目标文件已生成，继续使用现有文件"
                    )
                    return video_path
                raise

        if not await video_path.exists():
            raise DownloadException("yt-dlp 下载完成但未找到 MP4 文件")
        return video_path

    @auto_task
    async def download_audio(
        self,
        url: str,
        cookiefile: os.PathLike[str] | str | None = None,
        ext_headers: dict[str, str] | None = None,
        use_curl_cffi: bool = False,
    ) -> Path:
        """使用 yt-dlp 下载并提取无损 FLAC 音频。"""
        del ext_headers, use_curl_cffi
        file_name = generate_file_name(url)
        audio_path = pconfig.cache_dir / f"{file_name}.flac"
        if await audio_path.exists():
            return audio_path

        async with self._url_locks[url]:
            if await audio_path.exists():
                return audio_path

            await pconfig.cache_dir.mkdir(parents=True, exist_ok=True)
            ydl_opts = self._download_base_opts.copy()
            ydl_opts.update(
                {
                    "outtmpl": f"{pconfig.cache_dir / file_name}.%(ext)s",
                    "format": "bestaudio/best",
                    "postprocessors": [
                        {
                            "key": "FFmpegExtractAudio",
                            "preferredcodec": "flac",
                            "preferredquality": "0",
                        }
                    ],
                }
            )
            if cookie_path := _cookiefile_option(cookiefile):
                ydl_opts["cookiefile"] = cookie_path

            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    await asyncio.to_thread(ydl.download, [url])
            except Exception:
                if await audio_path.exists():
                    logger.warning(
                        "yt-dlp 下载音频报错但目标文件已生成，继续使用现有文件"
                    )
                    return audio_path
                raise

        if not await audio_path.exists():
            raise DownloadException("yt-dlp 下载完成但未找到 FLAC 文件")
        return audio_path
