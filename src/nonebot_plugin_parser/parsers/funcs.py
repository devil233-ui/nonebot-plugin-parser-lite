from collections.abc import Callable
import os
from pathlib import Path
from typing import Any, Literal, Sequence

from .data import (
    GraphicContent,
    Stats,
    Author,
    Comment,
    AudioContent,
    ImageContent,
    MediaContent,
    VideoContent,
    StickerContent,
)
from ..utils import keep_zh_en_num
from ..config import pconfig as pconfig
from ..download import DOWNLOADER as DOWNLOADER
from ..constants import COMMON_HEADER
from asyncio import Task
from collections.abc import Coroutine

headers = COMMON_HEADER.copy()


def create_author(
    name: str,
    avatar_url: str | None = None,
    description: str | None = None,
    id: str | None = None,
):
    """
    创建作者对象

    :param name: 作者名称
    :param avatar_url: 作者头像 URL
    :param description: 作者描述
    :param id: 作者 ID
    """

    avatar_task = None
    if avatar_url:
        avatar_task = DOWNLOADER.download_img(avatar_url, ext_headers=headers)
    return Author(name=name, id=id, avatar=avatar_task, description=description)


def create_video(
    url_or_task: str | Task[Path] | Callable[[], Coroutine[Any, Any, Path]],
    cover_url: str | None = None,
    duration: float = 0.0,
    video_name: str | None = None,
    need_send: bool = True,
):
    """
    创建视频内容

    :param url_or_task: 视频 URL 或下载任务
    :param cover_url: 封面 URL
    :param duration: 视频时长
    :param video_name: 视频名称
    :param need_send: 是否发送
    """

    # 清理文件名，只保留安全字符
    if video_name:
        # 保留文件名中的后缀

        base_name, ext = os.path.splitext(video_name)
        cleaned_base = keep_zh_en_num(base_name)
        video_name = f"{cleaned_base}{ext}"

    cover_task = None
    if cover_url:
        cover_task = DOWNLOADER.download_img(cover_url, ext_headers=headers)
    if isinstance(url_or_task, str):
        url_or_task = DOWNLOADER.download_video(
            url_or_task, video_name=video_name, ext_headers=headers
        )

    return VideoContent(
        path_task=url_or_task, cover=cover_task, duration=duration, need_send=need_send
    )


def create_videos(
    video_urls: list[str],
):
    """
    创建视频内容列表

    :param video_urls: 视频 URL 列表
    """

    return [create_video(url) for url in video_urls]


def create_image(
    url_or_task: str | Task[Path],
    need_send: bool = True,
):
    """
    创建图片内容

    :param url_or_task: 图片 URL 或下载任务
    :param need_send: 是否发送
    """

    if isinstance(url_or_task, str):
        url_or_task = DOWNLOADER.download_img(url_or_task, ext_headers=headers)

    return ImageContent(path_task=url_or_task, need_send=need_send)


def create_images(
    image_urls: list[str],
):
    """
    创建图片内容列表

    :param image_urls: 图片 URL 列表
    """

    return [create_image(url) for url in image_urls]


def create_audio(
    url_or_task: str | Task[Path],
    duration: float = 0.0,
    audio_name: str | None = None,
    need_send: bool = True,
):
    """
    创建音频内容

    :param url_or_task: 音频 URL 或下载任务
    :param duration: 音频时长
    :param audio_name: 音频名称
    :param need_send: 是否发送
    """

    # 清理文件名，只保留安全字符
    if audio_name:
        # 保留文件名中的后缀

        base_name, ext = os.path.splitext(audio_name)
        cleaned_base = keep_zh_en_num(base_name)
        audio_name = f"{cleaned_base}{ext}"

    if isinstance(url_or_task, str):
        url_or_task = DOWNLOADER.download_audio(
            url_or_task, audio_name=audio_name, ext_headers=headers
        )

    return AudioContent(path_task=url_or_task, duration=duration, need_send=need_send)


def create_graphic(
    image_url: str,
    alt: str | None = None,
    need_send: bool = True,
):
    """
    图片,此图片不参与九宫格

    :param image_url: 图片 URL
    :param alt: 图片描述
    :param need_send: 是否发送
    """

    image_task = DOWNLOADER.download_img(image_url, ext_headers=headers)
    return GraphicContent(path_task=image_task, alt=alt, need_send=need_send)


def create_sticker(
    url: str,
    size: Literal["small", "medium"] = "medium",
    desc: str | None = None,
):
    """
    创建贴纸内容

    :param url: 贴纸图片链接
    :param size: 贴纸大小
        - small: 比文字大一点
        - medium: 文字大小的两倍大一点
    :param desc: 贴纸描述
    """

    image_task = DOWNLOADER.download_img(url, ext_headers=headers)
    return StickerContent(path_task=image_task, size=size, desc=desc)


def create_stats(
    view_count: str | None = None,
    like_count: str | None = None,
    collect_count: str | None = None,
    share_count: str | None = None,
    comment_count: str | None = None,
    extra: dict[str, Any] | None = None,
):
    """
    创建统计信息

    :param view_count: 浏览数
    :param like_count: 点赞数
    :param collect_count: 收藏数
    :param share_count: 分享数
    :param comment_count: 评论数
    :param extra: 额外的信息
    """
    if extra is None:
        extra = {}

    return Stats(
        view_count=view_count,
        like_count=like_count,
        collect_count=collect_count,
        share_count=share_count,
        comment_count=comment_count,
        extra=extra,
    )


def create_comment(
    author: Author,
    content: Sequence[MediaContent | str | None],
    timestamp: int | None = None,
    stats: Stats | None = None,
    location: str | None = None,
    replies: list[Comment] | None = None,
    parent_author: Author | None = None,
):
    """
    创建评论内容

    :param author: 评论作者
    :param content: 评论内容
    :param timestamp: 评论时间戳
    :param stats: 评论统计信息
    :param location: 评论位置
    :param replies: 评论回复
    :param parent_author: 评论的父级作者
    """

    if replies is None:
        replies = []
    return Comment(
        author=author,
        content=content,
        timestamp=timestamp,
        stats=stats or Stats(),
        location=location,
        replies=replies,
        parent_author=parent_author,
    )
