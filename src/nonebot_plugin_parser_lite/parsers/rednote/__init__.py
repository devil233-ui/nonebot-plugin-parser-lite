import re
from typing import ClassVar, Final

import ujson

from ...data import Comment
from ..base import (
    BaseParser,
    MatchWithParams,
    ParseException,
    Platform,
    PlatformEnum,
    handle,
    pconfig,
)
from .discovery import decoder as discoveryDecoder

INITIAL_STATE = re.compile(
    pattern=r"window\.__INITIAL_STATE__=(.*?)</script>",
    flags=re.DOTALL,
)


REDNOTE_PAGE_HEADERS: Final[dict[str, str]] = {
    "accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,"
        "image/webp,image/apng,*/*;q=0.8,"
        "application/signed-exchange;v=b3;q=0.7"
    ),
    "accept-language": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6",
    "referer": "https://www.xiaohongshu.com/",
}


def _normalize_pc_note(raw: str) -> str | None:
    """把新版 PC 结构（note.noteDetailMap）转成旧的 noteData 结构。"""
    try:
        payload = ujson.loads(raw)
    except Exception:
        return None
    detail_map = (payload.get("note") or {}).get("noteDetailMap") or {}
    if not isinstance(detail_map, dict) or not detail_map:
        return None
    entry = list(detail_map.values())[-1]
    if not isinstance(entry, dict):
        return None

    note = entry.get("note") or {}
    user = note.get("user") or {}
    interact = note.get("interactInfo") or {}
    images = [i for i in (note.get("imageList") or []) if isinstance(i, dict)]
    video = note.get("video")
    if not (
        isinstance(video, dict)
        and isinstance(video.get("capa"), dict)
        and isinstance(video.get("consumer"), dict)
    ):
        video = None
    comments = [c for c in (entry.get("comments") or []) if isinstance(c, dict)]

    return ujson.dumps(
        {
            "noteData": {
                "data": {
                    "noteData": {
                        "title": note.get("title") or "",
                        "desc": note.get("desc") or "",
                        "user": {
                            "nickName": user.get("nickname")
                            or user.get("nickName")
                            or "",
                            "avatar": user.get("avatar") or user.get("image") or "",
                            "userId": user.get("userId") or "",
                        },
                        "lastUpdateTime": note.get("lastUpdateTime")
                        or note.get("time")
                        or 0,
                        "interactInfo": {
                            key: str(interact.get(key) or "")
                            for key in (
                                "likedCount",
                                "collectedCount",
                                "commentCount",
                                "shareCount",
                            )
                        },
                        "cover": {
                            "fileId": (images[0].get("fileId") if images else "")
                            or ""
                        },
                        "imageList": [
                            {
                                "fileId": img.get("fileId") or "",
                                "livePhoto": bool(img.get("livePhoto")),
                                "stream": img.get("stream") or {},
                            }
                            for img in images
                        ],
                        "video": video,
                    },
                    "commentData": {
                        "comments": [_normalize_pc_comment(c) for c in comments]
                    },
                }
            }
        }
    )


def _normalize_pc_comment(item: dict) -> dict:
    user = item.get("user") or {}
    pictures = [p for p in (item.get("pictures") or []) if isinstance(p, dict)]
    subs = [s for s in (item.get("subComments") or []) if isinstance(s, dict)]
    return {
        "user": {
            "nickname": user.get("nickname") or user.get("nickName") or "",
            "image": user.get("image") or user.get("avatar") or "",
            "userId": user.get("userId") or "",
        },
        "time": item.get("time") or item.get("createTime") or 0,
        "likeViewCount": str(
            item.get("likeViewCount") or item.get("likeCount") or "0"
        ),
        "content": item.get("content") or "",
        "ipLocation": item.get("ipLocation") or "",
        "pictures": [
            {"originUrl": p.get("originUrl") or ""} for p in pictures
        ],
        "subComments": [_normalize_pc_comment(s) for s in subs],
    }


class RedNoteParser(BaseParser):
    platform: ClassVar[Platform] = Platform(
        name=PlatformEnum.REDNOTE, display_name="小红书"
    )

    def __init__(self):
        super().__init__()
        self.ios_headers.update(
            {
                "origin": "https://www.xiaohongshu.com",
                "x-requested-with": "XMLHttpRequest",
                "sec-fetch-site": "same-origin",
                "sec-fetch-mode": "cors",
                "sec-fetch-dest": "empty",
            }
        )

    @handle("xhslink.cn", r"xhslink\.cn/[A-Za-z0-9._?%&+=/#@-]+")
    @handle("xhslink.com", r"xhslink\.com/[A-Za-z0-9._?%&+=/#@-]+")
    async def _parse_short_link(self, searched: MatchWithParams):
        url = f"https://{searched.url}"
        return await self.parse_with_redirect(url, self.ios_headers)

    # https://www.xiaohongshu.com/explore/6a33df40000000001101fce5?xsec_token=ABoRHhYoIhXV24zUz64kWg8vu6u8D4zLUjIFRBf8fcf54=
    # https://www.xiaohongshu.com/explore/6a355d090000000021018c66?xsec_token=CBC_w7YSZbQP_uKutDlmX7iqXuEzWJJ8x8_nfNNfFWoHU=
    @handle(
        "xiaohongshu.com",
        r"(?P<type>explore|search_result|discovery/item)/(?P<note_id>[0-9a-zA-Z]+)",
        params={"xsec_token": {}},
    )
    async def _parse_common(self, searched: MatchWithParams):
        # parse_type = searched["type"]
        note_id = searched["note_id"]
        xsec_token = searched["xsec_token"]

        url = f"https://www.xiaohongshu.com/discovery/item/{note_id}?xsec_token={xsec_token}&xsec_source=pc_share"

        response = await self.httpx.get(
            url,
            headers={**self.headers, **REDNOTE_PAGE_HEADERS},
        )
        response.raise_for_status()
        html = response.text

        if matched := INITIAL_STATE.search(html):
            raw = (
                matched[1]
                .replace("undefined", "null")
                .replace("new Map([])", "[]")
            )
        else:
            raise ParseException("小红书分享链接失效或内容已删除")

        # 新版页面把笔记放在 note.noteDetailMap 下，先转成旧结构
        try:
            init_state = discoveryDecoder.decode(_normalize_pc_note(raw) or raw)
        except Exception as exc:
            raise ParseException("小红书页面结构变化，暂无法解析") from exc
        note_detail = init_state.noteData.data.noteData
        comment_data = init_state.noteData.data.commentData
        author = self.create_author(
            name=note_detail.nickname,
            avatar_url=note_detail.avatar_url,
            avatar_cache_key=f"rednote:{note_detail.user.userId}",
            id=note_detail.user.userId,
        )
        comment_list: list[Comment] = []

        if pconfig.max_comments:
            for c in comment_data.comments:
                comment = self.create_comment(
                    author=self.create_author(
                        name=c.user.nickname,
                        avatar_url=c.user.image,
                        location=c.ipLocation,
                        avatar_cache_key=f"rednote:{c.user.userId}",
                        id=c.user.userId,
                    ),
                    content=c.content,
                    timestamp=c.time // 1000,
                    stats=self.create_stats(
                        like_count=c.likeViewCount,
                        comment_count=str(len(c.subComments)),
                    ),
                )

                for sub in c.subComments:
                    comment.replies.append(
                        self.create_comment(
                            author=self.create_author(
                                name=sub.user.nickname,
                                avatar_url=sub.user.image,
                                avatar_cache_key=f"rednote:{sub.user.userId}",
                                id=sub.user.userId,
                            ),
                            content=sub.content,
                            timestamp=sub.time // 1000,
                            stats=self.create_stats(
                                like_count=sub.likeViewCount,
                            ),
                        )
                    )

                comment_list.append(comment)

        return self.result(
            title=note_detail.title,
            author=author,
            stats=self.create_stats(
                like_count=note_detail.interactInfo.likedCount,
                comment_count=note_detail.interactInfo.commentCount,
                share_count=note_detail.interactInfo.shareCount,
                collect_count=note_detail.interactInfo.collectedCount,
            ),
            comments=comment_list,
            content=note_detail.content,
            timestamp=note_detail.lastUpdateTime // 1000,
            url=url,
        )
