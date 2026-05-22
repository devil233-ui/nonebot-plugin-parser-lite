import re
from typing import ClassVar

from ...utils.format import replace_placeholder_to_sticker
from ..base import (
    BaseParser,
    MatchWithParams,
    ParseException,
    Platform,
    PlatformEnum,
    handle,
    pconfig,
)
from ..cookie import ck2dict
from .explore import REDNOTE_PATTERN, NoteDetailMap
from .explore import decoder as exploreDecoder

INITIAL_STATE = re.compile(
    pattern=r"window\.__INITIAL_STATE__=(.*?)</script>",
    flags=re.DOTALL,
)


class RedNoteParser(BaseParser):
    # 平台信息
    platform: ClassVar[Platform] = Platform(
        name=PlatformEnum.REDNOTE, display_name="小红书"
    )
    # 小红书笔记详情页对真实浏览器仍有速率限制，达到限制后需要时间恢复
    # 暂时不知ck能否缓解此问题

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

    @handle("xhslink.com", r"xhslink\.com/[A-Za-z0-9._?%&+=/#@-]+")
    async def _parse_short_link(self, searched: MatchWithParams):
        url = f"https://{searched.url}"
        return await self.parse_with_redirect(url, self.ios_headers)

    # https://www.xiaohongshu.com/explore/691e68a8000000001e02bcda?xsec_token=CBwYRkYkdf7BHsEy2bVC9-ZYDHXJDjIRl6QI8xzqm-gEg
    @handle(
        "xiaohongshu.com",
        # 坚决不使用 params={"xsec_token": {}}，防止上游缺陷正则搞破坏
        r"(?P<type>explore|search_result|discovery/item)/(?P<note_id>[0-9a-zA-Z]+)",
    )
    async def _parse_common(self, searched: MatchWithParams):
        note_id = searched["note_id"]
        
        # 终极暴力提取：绕过框架，直接从完整字符串中把 token 抠出来！
        import re
        full_text = getattr(searched, "string", str(searched))
        xsec_match = re.search(r"xsec_token=([^&\s]+)", full_text)
        
        if not xsec_match:
            raise ParseException("缺少 xsec_token, 无法解析小红书链接")
            
        xsec_token = xsec_match.group(1)

        url = f"https://www.xiaohongshu.com/explore/{note_id}?xsec_token={xsec_token}&xsec_source=pc_share"

        response = await self.httpx.get(
            url,
            headers=self.headers,
            cookies=ck2dict(pconfig.xhs_ck) if pconfig.xhs_ck else None,
        )
        response.raise_for_status()
        html = response.text

        if matched := INITIAL_STATE.search(html):
            raw = matched[1].replace("undefined", "null")
        else:
            raise ParseException("小红书分享链接失效或内容已删除")
        init_state = exploreDecoder.decode(raw)
        note_data = init_state.note.noteDetailMap[init_state.note.currentNoteId]

        return self._build_result(note_data)

    def _build_result(self, note_data: NoteDetailMap):
        """从 note_data 构建最终解析结果"""
        note_detail = note_data.note

        contents = replace_placeholder_to_sticker(
            note_detail.desc, REDNOTE_PATTERN, "rednote"
        )
        contents.extend(note_detail.medias)

        author = self.create_author(
            name=note_detail.nickname,
            avatar_url=note_detail.avatar_url,
        )

        return self.result(
            title=note_detail.title,
            author=author,
            stats=self.create_stats(
                like_count=note_detail.interactInfo.likedCount,
                comment_count=note_detail.interactInfo.commentCount,
                share_count=note_detail.interactInfo.shareCount,
                collect_count=note_detail.interactInfo.collectedCount,
            ),
            content=contents,
            timestamp=note_detail.lastUpdateTime // 1000,
            url=f"https://www.xiaohongshu.com/discovery/item/{note_detail.noteId}?xsec_token={note_detail.xsecToken}",
        )
