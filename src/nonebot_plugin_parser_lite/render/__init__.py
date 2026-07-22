import base64
from collections.abc import AsyncGenerator, Awaitable, Callable
from datetime import datetime
from io import BytesIO
from itertools import chain
from typing import Any, ClassVar, Literal, cast
import uuid

from anyio import Path
from nonebot import logger
from nonebot_plugin_htmlrender import template_to_pic
import qrcode

from ..config import _nickname, gconfig, pconfig
from ..data import (
    AudioContent,
    GraphicContent,
    ImageContent,
    LinkContent,
    LivePhotoContent,
    MediaContent,
    ParseResult,
    StickerContent,
    VideoContent,
)
from ..exception import (
    DownloadException,
    DurationLimitException,
    SizeLimitException,
)
from ..helper import ForwardNodeInner, UniHelper, UniMessage
from ..utils.cache import CacheManager

PLACEHOLDER_IMAGE = (
    "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"
)
SPLIT_THRESHOLD = pconfig.forward_text_threshold
"""单段文本拆分阈值"""
MAX_FORWARD_TEXT_LEN = 30000
"""单个 forward 文本总长上限"""
MAX_FORWARD_NODES = 90
"""单个 forward 节点数上限"""

IS_DEBUG = gconfig.log_level in ["DEBUG", "TRACE", 10, 5]

Theme = Literal["light", "dark"]


def get_theme() -> Theme:
    """根据配置的白天时间范围返回当前主题"""
    start, end = pconfig.day_range_minutes
    now = datetime.now()
    current = now.hour * 60 + now.minute
    if start == end:
        # 为什么会有极夜
        in_day = False
    elif start < end:
        in_day = start <= current < end
    else:
        in_day = current >= start or current < end
    return "light" if in_day else "dark"


def split_text_by_length_with_punct(text: str, max_len: int) -> list[str]:
    """按长度切分文本，优先在标点符号处断句。

    规则：
    1. 遍历文本，当前段长度超过 max_len 时：
       - 尝试在当前段中最后一个标点符号后断句；
       - 若找不到合适标点，则在 max_len 处硬切。
    2. 支持中英文常用标点。

    :param text: 原始文本
    :param max_len: 每段最大长度
    :return: 切分后的文本段列表
    """
    if max_len <= 0 or len(text) <= max_len:
        return [text]

    # 常见句末/停顿标点（中英文）
    puncts = "。！？!?；;，,、…"
    result: list[str] = []
    start = 0
    length = len(text)

    while start < length:
        # 预算本段的理论结束位置
        end = min(start + max_len, length)
        segment = text[start:end]

        if end == length:
            # 已到末尾，直接收尾
            result.append(segment)
            break

        cut_pos = next(
            (i + 1 for i in range(len(segment) - 1, -1, -1) if segment[i] in puncts),
            -1,
        )
        if cut_pos <= 0:
            # 没找到合适标点，直接在 max_len 处切
            result.append(segment)
            start = end
        else:
            # 在标点后断句
            result.append(segment[:cut_pos])
            start += cut_pos

    return [seg for seg in result if seg]


async def safe_src(
    obj: Any, method: str = "get_path", *, return_none_on_fail: bool = False
) -> str | None:
    """
    通用安全资源获取过滤器

    用法：
    ```
        # 默认调用 get_path()
        {{ cont | safe_src }}
        # 调用 get_base()
        {{ cont | safe_src("get_base") }}
        # 调用 get_cover_path()
        {{ cont | safe_src("get_cover_path") }}
        #调用 get_avatar_path(), 在获取失败时返回`None`而不是空白图片
        {{ author | safe_src("get_avatar_path", return_none_on_fail=True) }}
    ```
    """
    try:
        if not hasattr(obj, method):
            logger.warning(f"对象 {type(obj).__name__} 不存在方法 '{method}'")
            return None if return_none_on_fail else PLACEHOLDER_IMAGE
        attr = getattr(obj, method)
        if not callable(attr):
            logger.warning(f"{type(obj).__name__} 的属性 '{method}' 不是可调用对象")
            return None if return_none_on_fail else PLACEHOLDER_IMAGE
        method_attr = cast(Callable[[], Path | Awaitable[Path]], attr)
        call_result = method_attr()
        src = await call_result if isinstance(call_result, Awaitable) else call_result
        return src.as_uri()
    except Exception as e:
        logger.warning(f"safe_src({method}) 处理 {type(obj).__name__} 时失败: {e!r}")
        return None if return_none_on_fail else PLACEHOLDER_IMAGE


class Renderer:
    """统一的渲染器，将解析结果转换为消息"""

    templates_dir: ClassVar[Path] = Path(__file__).parent / "templates"
    """模板目录"""

    async def render_messages(self, result: ParseResult) -> UniMessage[Any]:
        """渲染消息

        :param result: 解析结果
        """
        # 尝试获取图片路径，以便在直接发送失败时使用文件发送
        try:
            image_seg = await self.cache_or_render_image(result)
        except Exception as e:
            logger.exception(f"获取图片路径失败: {e!r}")
            image_seg = None

        # 尝试直接发送图片
        msg = UniMessage(image_seg or "图片渲染失败")
        if pconfig.append_url:
            urls = (result.display_url, result.repost_display_url)
            msg += "\n".join(url for url in urls if url)
        return msg

    async def send_content(
        self, result: ParseResult
    ) -> AsyncGenerator[UniMessage[Any], None]:
        """发送媒体内容消息。
        修改：使用 asyncio.as_completed 让音视频下载与图文构建并发执行，谁先完成谁先发。
        """
        import asyncio
        failed_count = 0
        repost_medias = result.repost.content if result.repost else []
        media_contents = [
            cont
            for cont in chain(result.content, repost_medias)
            if isinstance(cont, MediaContent) and cont.need_send
        ]

        tasks = []

        # 任务1：包装音视频下载逻辑为独立协程
        async def wrap_media(cont: MediaContent):
            msgs = []
            failed = 0
            try:
                async for msg in self.__handle_immediate_media(cont):
                    msgs.append(msg)
            except SizeLimitException as e:
                msgs.append(UniMessage(
                    f"设定的最大上传大小为 {pconfig.max_size}MB\n"
                    f"当前解析到的媒体大小为 {e.size}MB\n"
                    "媒体太大了~"
                ))
            except DurationLimitException as e:
                msgs.append(UniMessage(
                    f"设定的最大时长为 {pconfig.duration_maximum}s\n"
                    f"当前解析到的媒体时长为 {e.duration}s\n"
                    "媒体太长了~"
                ))
            except Exception as e:
                # 终极兜底：捕获 httpx.ReadError 等一切漏网的未知网络异常
                import traceback
                logger.error(
                    f"{cont.__class__.__name__} 下载过程中发生异常:\n{traceback.format_exc()}"
                )
                failed = 1
            return ("media", msgs, failed)

        for cont in media_contents:
            tasks.append(asyncio.create_task(wrap_media(cont)))

        # 任务2：包装图文/纯文字的组装与发送逻辑为独立协程（使用咱们更干脆的动态装箱逻辑 + 配置项读取）
        async def wrap_forward(res: ParseResult):
            msgs = []
            ordered_segs = await self.__build_forward_segs(res)
            if ordered_segs:
                is_pure_text = all(isinstance(seg, str) for seg in ordered_segs)
                total_text_len = sum(len(seg) for seg in ordered_segs if isinstance(seg, str))

                # 判定是否合并转发：总字数超标，或配置强制，或图文混合超标
                need_forward = False
                if total_text_len > pconfig.forward_text_threshold:
                    need_forward = True
                elif not is_pure_text and (pconfig.need_forward_contents or len(ordered_segs) > pconfig.forward_node_threshold):
                    need_forward = True

                if not need_forward:
                    if is_pure_text:
                        msgs.append(UniMessage("\n".join(ordered_segs)))
                    else:
                        msgs.append(UniMessage(ordered_segs))
                else:
                    batch_size = (
                        pconfig.forward_small_batch_size 
                        if total_text_len > pconfig.forward_long_text_threshold 
                        else pconfig.forward_large_batch_size
                    )
                    for i in range(0, len(ordered_segs), batch_size):
                        batch_segs = ordered_segs[i:i + batch_size]
                        msgs.append(UniMessage(UniHelper.construct_forward_message(batch_segs)))
            return ("forward", msgs, 0)

        tasks.append(asyncio.create_task(wrap_forward(result)))

        # 并发等待：谁先处理完就立即 yield 发送谁
        for coro in asyncio.as_completed(tasks):
            task_type, msgs, failed = await coro
            failed_count += failed
            for i, msg in enumerate(msgs):
                yield msg
                # 仅针对分批的合并转发，在发送间增加 1 秒延迟防风控
                if task_type == "forward" and len(msgs) > 1 and i < len(msgs) - 1:
                    await asyncio.sleep(1.0)

        # 汇总下载失败信息
        if failed_count > 0:
            message = f"{failed_count} 项媒体下载失败"
            yield UniMessage(message)
            logger.warning(message)

    async def __handle_immediate_media(
        self, cont: MediaContent
    ) -> AsyncGenerator[UniMessage[Any], None]:
        """
        处理需要立即发送的音视频媒体，返回对应的消息段

        :raise ZeroSizeException: 资源大小为 0 时抛出
        :raise SizeLimitException: 资源大小超过配置的最大限制时抛出
        :raise DurationLimitException: 媒体时长超过配置的最大限制时抛出
        :raise DownloadException: 重试多次仍失败时抛出
        """
        if not isinstance(cont, (VideoContent, AudioContent)):
            return
        if cont.duration > pconfig.duration_maximum:
            raise DurationLimitException(cont.duration)

        path = await cont.get_path()
        if (isinstance(cont, VideoContent) and pconfig.need_upload_video) or (
            not isinstance(cont, VideoContent)
            and isinstance(cont, AudioContent)
            and pconfig.need_upload_audio
        ):
            yield UniMessage(await UniHelper.file_seg(path))
        elif isinstance(cont, VideoContent):
            yield UniMessage(
                await UniHelper.video_seg(
                    file=path, thumbnail=await cont.get_cover_path()
                )
            )
        elif isinstance(cont, AudioContent):
            yield UniMessage(await UniHelper.record_seg(path))

    async def __build_forward_segs(
        self,
        result: ParseResult,
    ) -> list[ForwardNodeInner]:
        """根据当前内容和转发内容构造有序的转发段列表（文本 + 媒体，保持顺序）

        规则：
        - 主帖：
          - 文本片段按顺序聚合，输出 "作者：文本" 节点
          - 媒体片段（Image/Graphic/LivePhoto/Video 封面等）按出现顺序插入对应消息段
        - 如有转发：
          - 插入一条说明
          - 然后对转发 ParseResult 做同样处理
        """

        async def build_nodes(pr: ParseResult) -> list[ForwardNodeInner]:
            author_name = pr.author.name
            nodes: list[ForwardNodeInner] = []
            text_buffer: list[str] = []

            if pr.title:
                text_buffer.append(f"【{pr.title}】\n")

            async def flush_text() -> None:
                nonlocal text_buffer
                if text_buffer:
                    text = "\n".join(text_buffer).strip()
                    if text:
                        chunk_size = 1000
                        is_first = True
                        while len(text) > chunk_size:
                            break_point = text.rfind("\n", 0, chunk_size)
                            if break_point == -1:
                                for p in ("。", "！", "？", ".", "!", "?"):
                                    pos = text.rfind(p, 0, chunk_size)
                                    if pos > break_point:
                                        break_point = pos
                            if break_point == -1:
                                for p in ("；", ";", "，", ","):
                                    pos = text.rfind(p, 0, chunk_size)
                                    if pos > break_point:
                                        break_point = pos
                            if break_point == -1:
                                break_point = chunk_size - 1
                            
                            chunk = text[:break_point + 1].strip()
                            if chunk:
                                if is_first and len(nodes) == 0:
                                    nodes.append(f"{author_name}：\n{chunk}")
                                else:
                                    nodes.append(chunk)
                            text = text[break_point + 1:].strip()
                            is_first = False
                            
                        if text:
                            if is_first and len(nodes) == 0:
                                nodes.append(f"{author_name}：\n{text}")
                            else:
                                nodes.append(text)
                    text_buffer = []

            async def append_media(cont: MediaContent) -> None:
                """将单个媒体内容转换为若干 ForwardNodeInner，并追加到 nodes"""
                try:
                    # 视频：使用封面图作为转发节点
                    if isinstance(cont, VideoContent):
                        path = await cont.get_cover_path()
                        if path:
                            nodes.append(await UniHelper.img_seg(file=path))
                        return

                    # 图片
                    if isinstance(cont, ImageContent):
                        path = await cont.get_path()
                        nodes.append(await UniHelper.img_seg(path))
                        return

                    # 图文：图片 + 可选文字说明
                    if isinstance(cont, GraphicContent):
                        path = await cont.get_path()
                        seg: ForwardNodeInner = await UniHelper.img_seg(path)
                        if cont.alt:
                            seg = seg + cont.alt
                        nodes.append(seg)
                        return

                    # Live Photo
                    if isinstance(cont, LivePhotoContent):
                        if pconfig.live_photo:
                            live_path = await cont.get_live()
                            nodes.append(
                                await UniHelper.video_seg(
                                    file=live_path, thumbnail=await cont.get_base()
                                )
                            )
                        else:
                            base_path = await cont.get_base()
                            live_path = await cont.get_path()
                            nodes.append(await UniHelper.img_seg(base_path))
                            nodes.append(
                                await UniHelper.video_seg(
                                    file=live_path, thumbnail=base_path
                                )
                            )
                        return
                except Exception as e:
                    # 统一当作媒体构建失败处理
                    logger.warning(f"构建转发媒体片段失败: {type(cont).__name__}: {e}")
                    nodes.append(f"[媒体加载失败：{type(cont).__name__}]")

            # 按 content 顺序遍历
            for item in pr.content:
                if isinstance(item, str):
                    # 文本：缓冲，遇到媒体或结束时 flush
                    if text := item.strip():
                        text_buffer.append(text)
                elif isinstance(item, StickerContent):
                    text_buffer.append(item.desc or "[表情]")
                elif isinstance(item, MediaContent) and item.need_send:
                    # 媒体：先输出之前的文本，再输出媒体段
                    await flush_text()
                    await append_media(item)
                elif isinstance(item, LinkContent):
                    text_buffer.append(item.url)
                else:
                    # 其他类型暂不处理
                    continue

            # 收尾文本
            await flush_text()
            return nodes

        ordered: list[ForwardNodeInner] = []
        # 1. 主帖节点
        ordered.extend(await build_nodes(result))
        # 2. 转发内容
        repost = result.repost
        if not repost:
            return ordered
        # 2.1 转发说明
        ordered.append(">>>>>原帖<<<<<")
        # 2.2 原帖节点
        ordered.extend(await build_nodes(repost))
        return ordered

    async def render_image(self, result: ParseResult, *, theme: Theme) -> bytes:
        """使用 HTML 绘制通用社交媒体帖子卡片"""
        # 准备模板数据
        template_data = await self.resolve_parse_result(result)

        # 处理模板针对
        template_name = "default.html.jinja"
        if result.platform:
            # 音乐平台使用音乐模板
            music_platforms = ["kugou", "netease", "kuwo", "qsmusic"]
            platform_name = result.platform.name.lower()

            if platform_name in music_platforms:
                template_name = "music.html.jinja"
            else:
                file_name = f"{platform_name}.html.jinja"
                if await (self.templates_dir / file_name).exists():
                    template_name = file_name

        if IS_DEBUG:
            from jinja2 import Environment, FileSystemLoader

            env = Environment(
                loader=FileSystemLoader(self.templates_dir),
                enable_async=True,
            )
            env.filters["safe_src"] = safe_src
            template = env.get_template(template_name)
            render_path = (
                self.templates_dir.parent.parent
                / f"{datetime.now().strftime('%Y-%m-%d-%H%M%S')}.html"
            )
            await render_path.write_text(
                await template.render_async(result=template_data, theme=theme),
                encoding="utf8",
            )
            logger.info(f"已生成调试 HTML: {render_path}")

        return await template_to_pic(
            template_path=str(self.templates_dir),
            template_name=template_name,
            templates={
                "result": template_data,
                "theme": theme,
            },
            pages={
                "viewport": {"width": 620, "height": 100},
                "base_url": f"file://{self.templates_dir}",
            },
            filters={"safe_src": safe_src},
            type="jpeg",
            quality=85,
        )

    async def resolve_parse_result(self, result: ParseResult) -> dict[str, Any]:
        """解析 ParseResult 为模板可用的字典数据"""
        
        # --- 极速双轨（音视频）体积计算模块 ---
        import httpx
        from nonebot import logger

        async def _get_url_size(target_url: str) -> int:
            if not target_url or not isinstance(target_url, str) or not target_url.startswith("http"):
                return 0
                
            # ✨ 动态分配 Referer，突破小红书 146B 防盗链假报错
            referer = ""
            if "bilibili.com" in target_url or "hdslb.com" in target_url:
                referer = "https://www.bilibili.com"
            elif "xhscdn.com" in target_url or "xiaohongshu.com" in target_url:
                referer = "https://www.xiaohongshu.com"
                
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "*/*",
                "Accept-Encoding": "identity"
            }
            if referer:
                headers["Referer"] = referer
            
            try:
                async with httpx.AsyncClient(verify=False) as client:
                    resp = await client.head(target_url, headers=headers, follow_redirects=True, timeout=4.0)
                    cl = resp.headers.get("Content-Length")
                    
                    # 严格校验状态码，避免把 403 错误页当成视频体积
                    if resp.status_code not in (200, 206) or not cl:
                        async with client.stream("GET", target_url, headers=headers, follow_redirects=True) as sr:
                            if sr.status_code in (200, 206):
                                cl = sr.headers.get("Content-Length")
                            else:
                                return 0
                            
                    if cl and cl.isdigit():
                        return int(cl)
            except Exception as e:
                logger.warning(f"获取体积失败: {e}")
            return 0

        # ✨ 保留你非常优秀的双轨计算逻辑！
        for cont in result.content:
            if isinstance(cont, VideoContent):
                try:
                    total_bytes = 0
                    
                    # 1. 拿主视频流大小
                    main_url = getattr(cont.path_task, "url", None)
                    total_bytes += await _get_url_size(main_url)
                    
                    # 2. 拿可能存在的音频流大小（完美适配 B 站等音视频分离的平台）
                    kwargs = getattr(cont.path_task, "kwargs", {})
                    if kwargs and "audio_url" in kwargs:
                        total_bytes += await _get_url_size(kwargs.get("audio_url"))
                    elif hasattr(cont, "audio_url"):
                        total_bytes += await _get_url_size(getattr(cont, "audio_url"))
                    
                    if total_bytes > 0:
                        cont._size_bytes = total_bytes
                        
                except Exception as e:
                    logger.error(f"计算视频总体积异常: {e}")

        # --- 下方为上游极其干净的数据打包逻辑 ---
        data: dict[str, Any] = {
            "title": result.title,
            "formatted_datetime": result.formatted_datetime,
            "extra": result.extra,
            "platform": {
                "display_name": result.platform.display_name,
                "name": result.platform.name,
                "logo_path": await safe_src(result.platform, "get_logo_path"),
            },
            "content": result.content,
            "cover_path": await safe_src(result, "get_cover_path"),
            "stats": result.stats,
            "comments": result.comments[: pconfig.max_comments],
            "author": {
                "name": result.author.name,
                "id": result.author.id,
                "avatar_path": await safe_src(result.author, "get_avatar_path"),
            },
            "ai_summary": result.ai_summary,
            "rendering_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "bot_name": _nickname,
        }

        if result.repost:
            data["repost"] = await self.resolve_parse_result(result.repost)

        if pconfig.append_qrcode:
            qr = qrcode.QRCode(
                version=1,
                error_correction=1,
                box_size=10,
                border=1,
            )
            qr.add_data(result.url)
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")
            buffer = BytesIO()
            img.save(buffer, format="PNG")  # pyright: ignore[reportCallIssue]
            buffer.seek(0)
            img_base64 = base64.b64encode(buffer.getvalue()).decode("utf-8")
            data["qrcode_path"] = f"data:image/png;base64,{img_base64}"

        return data
        
    async def cache_or_render_image(self, result: ParseResult):
        """获取缓存图片（支持跨重启复用）

        以当前主题和解析结果 URL 为 key，在 cache_dir 下生成稳定文件名：
        - 若文件已存在：直接使用，不再重新渲染
        - 若不存在：渲染并写入该文件
        """
        theme = get_theme()
        cache_key = f"{theme}:{result.url}"
        file_name = f"{uuid.uuid5(uuid.NAMESPACE_URL, cache_key)}.jpeg"
        cache_dir = await CacheManager.ensure_dir(CacheManager.RENDER)
        image_path = cache_dir / file_name
        if await image_path.exists():
            result.render_image = image_path
        else:
            image_raw = await self.render_image(result, theme=theme)
            await image_path.write_bytes(image_raw)
            result.render_image = image_path
            if pconfig.use_base64:
                return await UniHelper.img_seg(image_raw)
        if (await image_path.stat()).st_size >= 5 * 1024 * 1024:
            return await UniHelper.file_seg(image_path)

        return await UniHelper.img_seg(image_path)


RENDERER = Renderer()