from itertools import chain
import asyncio
from dataclasses import dataclass
import re
from typing import ClassVar, TypeVar

from nonebot import get_driver, logger, on_regex
from nonebot.adapters import Bot, Event
from nonebot.permission import SUPERUSER
from nonebot.rule import Rule, to_me
from nonebot_plugin_alconna import (
    Alconna,
    Args,
    Match,
    UniMessage,
    on_alconna,
)
from nonebot_plugin_alconna.uniseg import reply_fetch
from nonebot_plugin_uninfo import Uninfo

from ..config import pconfig
from ..download import DOWNLOADER
from ..helper import UniHelper
from ..parsers import BaseParser, BilibiliParser, ParseResult
from ..render import RENDERER
from ..utils.common import LimitedSizeDict
from ..utils.ffmpeg import FFmpeg
from .rule import Searched, SearchResult, on_keyword_regex

T = TypeVar("T", bound=BaseParser)
_ENABLED_PARSER_CLASSES: list[type[BaseParser]] = []
_PARSER_INSTANCES: dict[type[BaseParser], BaseParser] = {}
_KEYWORD_CLASS_MAP: dict[str, type[BaseParser]] = {}
_ALL_PARSERS: list[BaseParser] = []
_RESULT_CACHE = LimitedSizeDict[str, ParseResult](max_size=50)


class LazyManager:
    TIMEOUT_SECONDS: ClassVar[int] = pconfig.lazy_download_timeout

    @dataclass
    class Session:
        result: ParseResult
        task: asyncio.Task[None]

    SESSIONS: ClassVar[dict[str, "LazyManager.Session"]] = {}

    @classmethod
    def add(cls, user_id: str, parse_result: ParseResult) -> None:
        cls.remove(user_id)
        task: asyncio.Task[None] = asyncio.create_task(cls._timeout_handler(user_id))
        session: LazyManager.Session = cls.Session(result=parse_result, task=task)
        cls.SESSIONS[user_id] = session

    @classmethod
    def get(cls, user_id: str) -> ParseResult:
        session = cls.SESSIONS.get(user_id)
        assert session is not None, "LazyManager.get: session should exist"
        return session.result

    @classmethod
    def has(cls, user_id: str) -> bool:
        return user_id in cls.SESSIONS

    @classmethod
    def remove(cls, user_id: str, *, current_task: asyncio.Task | None = None) -> None:
        session = cls.SESSIONS.pop(user_id, None)
        if session is None:
            return
        if session.task is not current_task and not session.task.done():
            session.task.cancel()

    @classmethod
    async def _timeout_handler(cls, user_id: str) -> None:
        self_task = asyncio.current_task()
        await asyncio.sleep(cls.TIMEOUT_SECONDS)
        if user_id not in cls.SESSIONS:
            return
        cls.remove(user_id, current_task=self_task)


def _ensure_parser_instance(parser_cls: type[BaseParser]) -> BaseParser:
    parser = _PARSER_INSTANCES.get(parser_cls)
    if parser is not None:
        return parser
    parser = parser_cls()
    _PARSER_INSTANCES[parser_cls] = parser
    _ALL_PARSERS.append(parser)
    return parser


def get_parser(keyword: str) -> BaseParser:
    parser_cls = _KEYWORD_CLASS_MAP.get(keyword)
    if parser_cls is None:
        raise KeyError(f"未找到关键字 {keyword!r} 对应的 parser")
    return _ensure_parser_instance(parser_cls)


def get_parser_by_type(parser_type: type[T]) -> T:
    for cls, inst in _PARSER_INSTANCES.items():
        if issubclass(cls, parser_type):
            return inst  # type: ignore[return-value]
    for cls in _ENABLED_PARSER_CLASSES:
        if issubclass(cls, parser_type):
            return _ensure_parser_instance(cls)  # type: ignore[return-value]
    raise ValueError(f"未找到类型为 {parser_type.__name__} 的 parser 实例")


def _get_enabled_parser_classes() -> list[type[BaseParser]]:
    disabled_platforms = set(pconfig.disabled_platforms)
    all_subclass = BaseParser.get_all_subclass()
    return [
        _cls for _cls in all_subclass if _cls.platform.name not in disabled_platforms
    ]


def clear_result_cache():
    _RESULT_CACHE.clear()

async def _send_parse_result(session: Uninfo, result: ParseResult) -> None:
    summary_msg = await RENDERER.render_messages(result)
    try:
        await summary_msg.send()
    except Exception as e:
        logger.error(f"发送摘要消息失败: {e}")

    # 纯文字也交由 renderer 处理（触发长文拆分与合并转发）
    is_pure_text = all(
        isinstance(c, str)
        for c in chain(result.content, result.repost.content if result.repost else [])
    )
    if is_pure_text:
        async for content_msg in RENDERER.send_content(result):
            try:
                await content_msg.send()
            except Exception as e:
                logger.error(f"发送纯文本/长文消息失败: {e}")
        return

    if pconfig.lazy_download:
        download_cmd = ", ".join(pconfig.download_command)
        try:
            await UniMessage(
                f"请在{LazyManager.TIMEOUT_SECONDS}秒内发送以下命令之一来获取媒体资源: "
                f"\n{download_cmd}"
            ).send()
            LazyManager.add(session.user.id, result)
        except Exception as e:
            logger.error(f"发送懒加载提示失败: {e}")
        return

    async for content_msg in RENDERER.send_content(result):
        try:
            await content_msg.send()
        except Exception as e:
            err_str = str(e)
            # ✨ 核心修正：精准识别 NapCat 大文件上传超时假报错
            if "rich media transfer failed" in err_str or "retcode=1200" in err_str:
                logger.warning("触发 NapCat 大文件上传超时假报错，后台实际通常已发送成功，忽略此错误。")
            else:
                logger.error(f"媒体发送至群聊失败: {e}")
                try:
                    await UniMessage("⚠️ 该媒体发送失败，可能体积过大或被QQ风控限制。").send()
                except Exception:
                    pass

driver = get_driver()


@driver.on_startup
def register_parser_matcher() -> None:
    global _ENABLED_PARSER_CLASSES, _KEYWORD_CLASS_MAP

    enabled_classes = _get_enabled_parser_classes()
    _ENABLED_PARSER_CLASSES = enabled_classes

    enabled_platforms: list[str] = []
    keyword_class_map: dict[str, type[BaseParser]] = {}
    for parser_cls in enabled_classes:
        enabled_platforms.append(parser_cls.platform.display_name)
        for keyword, _, _ in parser_cls._key_patterns:
            keyword_class_map[keyword] = parser_cls

    _KEYWORD_CLASS_MAP = keyword_class_map
    logger.info(f"启用平台: {', '.join(sorted(enabled_platforms))}")

    patterns = [pattern for cls_ in enabled_classes for pattern in cls_._key_patterns]
    matcher = on_keyword_regex(*patterns)
    matcher.append_handler(parser_handler)


@driver.on_shutdown
async def close_httpx() -> None:
    if not _ALL_PARSERS:
        return
    await asyncio.gather(*(parser.aclose() for parser in _ALL_PARSERS))


@UniHelper.with_reaction
async def parser_handler(
    session: Uninfo,
    sr: SearchResult = Searched(),
):

    """统一的解析处理器"""
    cache_key = sr.searched.cache_key

    # 1. 从缓存获取或重新解析
    result = _RESULT_CACHE.get(cache_key)
    if result is None:
        parser = get_parser(sr.keyword)
        result = await parser.parse(sr.keyword, sr.searched)
        logger.debug(f"解析结果: {result!r}")
        _RESULT_CACHE[cache_key] = result
    else:
        logger.debug(f"命中缓存: {cache_key}, 结果: {result!r}")

    await _send_parse_result(session, result)

# --- 强力魔改：bm 拦截器与 blogin ---

@on_regex(r"^\s*bm(?:\s+|$)", flags=re.IGNORECASE, priority=0, block=True).handle()
@UniHelper.with_reaction
async def _(bot: Bot, event: Event):
    try:
        _bilip = get_parser_by_type(BilibiliParser)
    except ValueError:
        await UniMessage("B站解析器未启用").finish()

    text = event.get_plaintext().strip()
    text = re.sub(r"^\s*bm\s*", "", text, flags=re.IGNORECASE).strip()

    # 吸收上游的新特性：如果直接发送了 bm，检查它是不是回复了某条消息
    if not text:
        reply = await reply_fetch(event, bot)
        if reply and reply.msg:
            text = str(reply.msg)

    if not text:
        await UniMessage("请发送要下载的音频内容、链接或回复带有链接的消息").finish()

    # 1. 提取分 P
    page_idx = -1
    p_match = re.search(r"[?&]p=(\d+)|[?]=(\d+)|\s(\d+)$", text)
    if p_match:
        page_val = p_match.group(1) or p_match.group(2) or p_match.group(3)
        page_idx = int(page_val) - 1

    # 2. 尝试提取短链并解析为长链
    b23_match = re.search(r"(https?://(?:b23\.tv|bilibili\.com/s)/[A-Za-z0-9]+)", text)
    if b23_match:
        try:
            import httpx
            async with httpx.AsyncClient(verify=False) as client:
                resp = await client.get(b23_match.group(1), follow_redirects=True)
                resolved_url = str(resp.url)
                text += f" {resolved_url}"
                
                if page_idx == -1:
                    p_match_2 = re.search(r"[?&]p=(\d+)", resolved_url)
                    if p_match_2:
                        page_idx = int(p_match_2.group(1)) - 1
        except Exception as e:
            logger.warning(f"短链解析失败: {e}")

    page_idx = max(0, page_idx)

    # 3. 提取 BV 号
    bvid_match = re.search(r"(BV[A-Za-z0-9]{10})", text)
    if not bvid_match:
        await UniMessage("未在内容中找到 BV 号或有效 B 站链接").finish()

    bvid = bvid_match.group(1)

    _, audio_url = await _bilip.extract_download_urls(
        bvid=bvid, page_index=page_idx
    )
    if not audio_url:
        await UniMessage("未找到可下载的音频").finish()

    ext_headers = {
        "Referer": "https://www.bilibili.com",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }

    audio_path = await DOWNLOADER.download_audio(
        url=audio_url, 
        audio_name=f"{bvid}-{page_idx + 1}.m4s",
        ext_headers=ext_headers
    )
    
    # 吸收上游优化的兼容性补丁：自动转码 m4s 到 mp3
    converted_path = await FFmpeg.convert_audio_to_mp3(audio_path)
    
    if pconfig.need_upload_audio:
        await UniMessage(await UniHelper.file_seg(converted_path)).send()
    else:
        await UniMessage(await UniHelper.record_seg(converted_path)).send()


@on_alconna(Alconna("blogin"), block=True, permission=SUPERUSER, rule=to_me()).handle()
async def _():
    try:
        parser = get_parser_by_type(BilibiliParser)
    except ValueError:
        await UniMessage("B站解析器未启用").finish()
        
    qrcode = await parser.login_with_qrcode()
    await UniMessage(await UniHelper.img_seg(qrcode)).send()
    async for msg in parser.check_qr_state():
        await UniMessage(msg).send()


if pconfig.lazy_download:
    async def has_lazy(session: Uninfo) -> bool:
        return LazyManager.has(session.user.id)

    lazy_matcher = on_alconna(
        Alconna(pconfig.download_command[0]),
        block=True,
        aliases=set(pconfig.download_command[1:]),
        rule=Rule(has_lazy),
    )

    @lazy_matcher.handle()
    @UniHelper.with_reaction
    async def _(session: Uninfo):
        """懒下载命令：发送上次解析结果中的媒体内容。"""
        user_id = session.user.id
        result = LazyManager.get(user_id)
        try:
            async for message in RENDERER.send_content(result):
                await message.send()
        finally:
            LazyManager.remove(user_id)