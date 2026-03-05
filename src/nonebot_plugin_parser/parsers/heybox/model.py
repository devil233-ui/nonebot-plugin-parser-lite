import json
import re
from typing import Literal

from msgspec import Struct, field

from ..creator import create_image, create_sticker, create_video
from ..data import MediaContent
from ...utils.format import replace_placeholder_to_sticker


HEYBOX_PATTERN = re.compile(r"\[(?P<name>[^]]+)\]")


def size_resolver(name: str) -> Literal["small", "medium"]:
    return "medium" if "bigemoji" in name else "small"


class User(Struct):
    avatar: str
    username: str
    userid: str | int

    @property
    def avatar_url(self) -> str:
        return self.avatar


class Img(Struct):
    url: str


class CommentItem(Struct):
    is_cy: int
    """是否插眼"""
    create_at: int
    text: str
    ip_location: str
    child_num: int
    """评论数"""
    up: int
    """点赞数"""
    user: User
    imgs: list[Img] = field(default_factory=list)

    @property
    def content(self) -> list[MediaContent | str]:
        content = replace_placeholder_to_sticker(
            self.text, HEYBOX_PATTERN, "heybox", size_resolver
        )
        for img in self.imgs:
            content.append(create_image(url=img.url + "\\"))
        if self.is_cy:
            content.append(
                create_sticker(
                    url="https://emoji.awkchan.top/assets/heybox/cy.png",
                    size="small",
                    desc="插眼",
                )
            )
        return content


class CommentData(Struct):
    comment: list[CommentItem]
    """第一个是主评论，后面都是回复"""


class Link(Struct):
    has_video: int
    """是否有视频，无视频则text为json，否则为str"""
    title: str
    description: str
    """纯文本内容"""
    text: str
    """可能的富文本内容"""
    ip_location: str
    click: int
    """浏览数"""
    comment_num: int
    """评论数"""
    create_at: int
    """创建时间"""
    favour_count: int
    """收藏数"""
    link_award_num: int
    """点赞数"""
    forward_num: int
    """转发数"""
    user: User
    video_url: str | None = None
    video_thumb: str | None = None

    @property
    def content(self) -> list[MediaContent | str]:
        """格式化的富文本内容"""
        content: list[MediaContent | str] = []
        try:
            parts = json.loads(self.text)
            for part in parts:
                if part["type"] == "text":
                    content.extend(
                        replace_placeholder_to_sticker(
                            self.text, HEYBOX_PATTERN, "heybox", size_resolver
                        )
                    )
                elif part["type"] == "img":
                    content.append(create_image(url=part["url"] + "\\"))
        except (json.JSONDecodeError, TypeError):
            content.append(self.text)
        if self.has_video and self.video_url and self.video_thumb:
            content.append(
                create_video(url_or_task=self.video_url, cover_url=self.video_thumb)
            )
        return content


class BaseResult(Struct):
    comments: list[CommentData]
    link: Link
