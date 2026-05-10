import datetime
import traceback
import uuid
from collections.abc import AsyncGenerator
from io import BytesIO
from itertools import chain
from pathlib import Path
from typing import Any, Awaitable, ClassVar

import aiofiles
import qrcode
from nonebot import logger
from nonebot_plugin_htmlrender import template_to_pic

from ..config import _nickname, pconfig
from ..exception import DownloadException, DownloadLimitException, DurationLimitException, SizeLimitException
from ..helper import ForwardNodeInner, UniHelper, UniMessage
from ..parsers.data import (
    AudioContent,
    GraphicContent,
    ImageContent,
    LivePhotoContent,
    MediaContent,
    ParseResult,
    StickerContent,
    VideoContent,
)

PLACEHOLDER_IMAGE = (
    "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"
)


async def safe_src(obj: Any, method: str = "get_path") -> str:
    """
    通用安全资源获取过滤器

    用法：
        {{ cont | safe_src }}                    # 默认调用 get_path()
        {{ cont | safe_src("get_base") }}        # 调用 get_base()
        {{ cont | safe_src("get_cover_path") }}  # 调用 get_cover_path()
        {{ author | safe_src("get_avatar_path") }} # 调用 get_avatar_path()
    """
    try:
        if not hasattr(obj, method):
            logger.warning(f"对象 {type(obj).__name__} 不存在方法 '{method}'")
            return PLACEHOLDER_IMAGE

        method_attr = getattr(obj, method)

        if not callable(method_attr):
            logger.warning(f"{type(obj).__name__} 的属性 '{method}' 不是可调用对象")
            return PLACEHOLDER_IMAGE

        call_result: Path | Awaitable[Path] = method_attr()  # type: ignore[assignment]

        src = await call_result if isinstance(call_result, Awaitable) else call_result
        return src.as_uri() if src else PLACEHOLDER_IMAGE
    except Exception as e:
        logger.warning(f"safe_src({method}) 处理 {type(obj).__name__} 时失败: {e}")
        return PLACEHOLDER_IMAGE

async def get_actual_size(obj: Any) -> str:
    """使用 HEAD 请求秒取媒体真实体积，完美解决卡顿问题"""
    try:
        import httpx
        # 确保对象有 path_task 且包含 url
        if hasattr(obj, "path_task") and hasattr(obj.path_task, "url"):
            url = obj.path_task.url
            if url:
                headers = {
                    "Referer": "https://www.bilibili.com",
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                }
                async with httpx.AsyncClient() as client:
                    # 第一梯队：尝试最高效的 HEAD 请求
                    resp = await client.head(url, headers=headers, follow_redirects=True, timeout=5.0)
                    content_length = resp.headers.get("Content-Length")
                    
                    # 兜底梯队：如果 CDN 拒绝 HEAD 请求或不返回体积，降级用流式 GET 骗取 Header 后立刻切断
                    if not content_length or resp.status_code == 403:
                        async with client.stream("GET", url, headers=headers, follow_redirects=True) as stream_resp:
                            content_length = stream_resp.headers.get("Content-Length")

                    if content_length and content_length.isdigit():
                        size_mb = int(content_length) / (1024 * 1024)
                        return f"{size_mb:.1f}MB"
    except Exception as e:
        logger.warning(f"获取媒体真实体积失败: {e}")
    return "未知"

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
        except Exception:
            logger.error(f"获取图片路径失败: {traceback.format_exc()}")
            image_seg = None

        # 尝试直接发送图片
        msg = UniMessage(image_seg or "图片渲染失败")
        if self.append_url:
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
                    f"设定的最大上传大小为 {pconfig.max_size}MB\n当前解析到的媒体大小为 {e.size}MB\n媒体太大了~"
                ))
            except DurationLimitException as e:
                msgs.append(UniMessage(
                    f"设定的最大时长为 {pconfig.duration_maximum}s\n当前解析到的媒体时长为 {e.duration}s\n媒体太长了~"
                ))
            except DownloadLimitException as e:
                msgs.append(UniMessage(str(e)))
            except DownloadException:
                failed = 1
            return ("media", msgs, failed)

        for cont in media_contents:
            tasks.append(asyncio.create_task(wrap_media(cont)))

        # 任务2：包装图文/纯文字的组装与发送逻辑为独立协程
        async def wrap_forward(res: ParseResult):
            msgs = []
            ordered_segs = await self.__build_forward_segs(res)
            if ordered_segs:
                is_pure_text = all(isinstance(seg, str) for seg in ordered_segs)
                total_text_len = sum(len(seg) for seg in ordered_segs if isinstance(seg, str))

                # 判定是否合并转发：总字数>300，或配置强制，或图文混合>4
                need_forward = False
                if total_text_len > 300:
                    need_forward = True
                elif not is_pure_text and (pconfig.need_forward_contents or len(ordered_segs) > 4):
                    need_forward = True

                if not need_forward:
                    if is_pure_text:
                        msgs.append(UniMessage("\n".join(ordered_segs)))
                    else:
                        msgs.append(UniMessage(ordered_segs))
                else:
                    batch_size = 4 if total_text_len > 1500 else 99
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
            raise DownloadException(message)

    async def __handle_immediate_media(
        self, cont: MediaContent
    ) -> AsyncGenerator[UniMessage[Any], None]:
        """
        处理需要立即发送的音视频媒体，返回对应的消息段

        :raises ZeroSizeException: 资源大小为 0 时抛出
        :raises SizeLimitException: 资源大小超过配置的最大限制时抛出
        :raises DownloadException: 重试多次仍失败时抛出
        """
        if not isinstance(cont, (VideoContent, AudioContent)):
            return

        path = await cont.get_path()
        if (
            isinstance(cont, VideoContent)
            and pconfig.need_upload_video
            or not isinstance(cont, VideoContent)
            and isinstance(cont, AudioContent)
            and pconfig.need_upload_audio
        ):
            yield UniMessage(UniHelper.file_seg(path))
        elif isinstance(cont, VideoContent):
            yield UniMessage(UniHelper.video_seg(path))
        elif isinstance(cont, AudioContent):
            yield UniMessage(UniHelper.record_seg(path))

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
                            nodes.append(UniHelper.img_seg(path))
                        return

                    # 图片
                    if isinstance(cont, ImageContent):
                        path = await cont.get_path()
                        nodes.append(UniHelper.img_seg(path))
                        return

                    # 图文：图片 + 可选文字说明
                    if isinstance(cont, GraphicContent):
                        path = await cont.get_path()
                        seg: ForwardNodeInner = UniHelper.img_seg(path)
                        if cont.alt:
                            seg = seg + cont.alt
                        nodes.append(seg)
                        return

                    # Live Photo
                    if isinstance(cont, LivePhotoContent):
                        if pconfig.live_photo:
                            live_path = await cont.get_live()
                            nodes.append(UniHelper.video_seg(live_path))
                        else:
                            base_path = await cont.get_base()
                            live_path = await cont.get_path()
                            nodes.append(UniHelper.img_seg(base_path))
                            nodes.append(UniHelper.video_seg(live_path))
                        return
                except Exception as e:
                    # 统一当作媒体构建失败处理
                    logger.warning(f"构建转发媒体片段失败: {type(cont).__name__}: {e}")
                    nodes.append(f"[媒体加载失败：{type(cont).__name__}]")

            # 按 content 顺序遍历
            for item in pr.content:
                if isinstance(item, str):
                    # 文本：缓冲，遇到媒体或结束时 flush
                    if item:
                        text_buffer.append(item)
                elif isinstance(item, StickerContent):
                    text_buffer.append(item.desc or "[表情]")
                elif isinstance(item, MediaContent) and item.need_send:
                    # 媒体：先输出之前的文本，再输出媒体段
                    await flush_text()
                    await append_media(item)
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

    @property
    def append_url(self) -> bool:
        return pconfig.append_url

    @property
    def append_qrcode(self) -> bool:
        return pconfig.append_qrcode

    async def render_image(self, result: ParseResult) -> bytes:
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
                # 其他平台使用各自的模板
                file_name = f"{platform_name}.html.jinja"
                if (self.templates_dir / file_name).exists():
                    template_name = file_name

        # from jinja2 import FileSystemLoader, Environment

        # # 创建一个包加载器对象
        # env = Environment(
        #     loader=FileSystemLoader(self.templates_dir),
        #     enable_async=True,
        # )
        # env.filters["safe_src"] = safe_src
        # template = env.get_template(template_name)
        # # 渲染
        # with open(
        #     f"{self.templates_dir.parent.parent}/{datetime.datetime.now().strftime('%Y-%m-%d-%H%M%S')}.html",
        #     "w",
        #     encoding="utf8",
        # ) as f:  # noqa: E501
        #     f.write(
        #         await template.render_async(result=template_data)
        #     )

        return await template_to_pic(
            template_path=str(self.templates_dir),
            template_name=template_name,
            templates={
                "result": template_data,
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

        import httpx
        from nonebot import logger

        async def _get_url_size(target_url: str, tag: str) -> int:
            if not target_url or not isinstance(target_url, str) or not target_url.startswith("http"):
                logger.warning(f"[体积测算] {tag} URL无效或为空: {target_url}")
                return 0
                
            headers = {
                "Referer": "https://www.bilibili.com",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "*/*",
                "Accept-Encoding": "identity"
            }
            logger.info(f"[体积测算] 开始测算 {tag}，URL前60字符: {target_url[:60]}...")
            
            try:
                async with httpx.AsyncClient(verify=False) as client:
                    resp = await client.head(target_url, headers=headers, follow_redirects=True, timeout=5.0)
                    cl = resp.headers.get("Content-Length")
                    logger.info(f"[体积测算] {tag} HEAD状态码: {resp.status_code}, Content-Length: {cl}")
                    
                    if not cl or resp.status_code in (403, 405):
                        logger.info(f"[体积测算] {tag} HEAD失败，尝试流式 GET 骗取 Header...")
                        async with client.stream("GET", target_url, headers=headers, follow_redirects=True) as sr:
                            cl = sr.headers.get("Content-Length")
                            logger.info(f"[体积测算] {tag} GET状态码: {sr.status_code}, Content-Length: {cl}")
                            
                    if cl and cl.isdigit():
                        size = int(cl)
                        logger.info(f"[体积测算] {tag} 成功拿到体积: {size} Bytes")
                        return size
                    else:
                        logger.warning(f"[体积测算] {tag} 最终未能提取到有效的 Content-Length")
            except Exception as e:
                logger.error(f"[体积测算] {tag} 获取体积时发生网络异常: {e}")
            return 0

        for cont in result.content:
            if isinstance(cont, VideoContent):
                # 如果其它平台（比如 B 站）自己已经提前算好了真实体积，直接跳过通用测算！
                if getattr(cont, "actual_size", "未知") != "未知":
                    logger.info(f"[渲染预处理] 已携带真实体积 {cont.actual_size}，跳过测算")
                    continue
                    
                cont.actual_size = "未知"
                try:
                    total_bytes = 0
                    logger.info(f"[渲染预处理] 解析视频对象, path_task: {cont.path_task}")
                    
                    # 1. 拿主视频流大小
                    main_url = getattr(cont.path_task, "url", None)
                    total_bytes += await _get_url_size(main_url, "主视频")
                    
                    # 2. 拿可能存在的音频流大小
                    kwargs = getattr(cont.path_task, "kwargs", {})
                    logger.info(f"[渲染预处理] path_task kwargs: {kwargs}")
                    
                    if kwargs and "audio_url" in kwargs:
                        total_bytes += await _get_url_size(kwargs.get("audio_url"), "独立音频")
                    elif hasattr(cont, "audio_url"): # 兜底某些可能直接挂载属性的写法
                        total_bytes += await _get_url_size(getattr(cont, "audio_url"), "独立音频属性")
                    
                    if total_bytes > 0:
                        cont.actual_size = f"{total_bytes / 1048576:.1f}MB"
                        logger.info(f"[渲染预处理] 计算完毕，总体积为: {cont.actual_size}")
                    else:
                        logger.warning("[渲染预处理] 总体积计算为 0，保留 '未知'")
                        
                except Exception as e:
                    logger.error(f"[渲染预处理] 计算视频总体积严重异常: {e}")

        data: dict[str, Any] = {
            "title": result.title,
            "formatted_datetime": result.formatted_datetime,
            "extra_info": result.extra_info,
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
            "rendering_time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "bot_name": _nickname,
        }

        if result.repost:
            data["repost"] = await self.resolve_parse_result(result.repost)

        # 添加二维码支持
        if pconfig.append_qrcode and result.url:
            # 生成二维码
            qr = qrcode.QRCode(
                version=1,
                error_correction=1,
                box_size=10,
                border=4,
            )
            qr.add_data(result.url)
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")

            # 将二维码转换为 base64 编码
            buffer = BytesIO()
            img.save(buffer, format="PNG")  # type: ignore
            buffer.seek(0)
            import base64

            img_base64 = base64.b64encode(buffer.getvalue()).decode("utf-8")

            # 添加 base64 编码的图片数据到模板数据
            data["qr_code_path"] = f"data:image/png;base64,{img_base64}"

        return data

    async def cache_or_render_image(self, result: ParseResult):
        """获取缓存图片

        :param result: 解析结果
        """
        if result.render_image is None:
            image_raw = await self.render_image(result)
            image_path = await self.save_img(image_raw)
            result.render_image = image_path
            if pconfig.use_base64:
                return UniHelper.img_seg(image_raw)
        if result.render_image.stat().st_size >= 5 * 1024 * 1024:
            return UniHelper.file_seg(result.render_image)

        return UniHelper.img_seg(result.render_image)

    @classmethod
    async def save_img(cls, raw: bytes) -> Path:
        """保存图片

        :param raw: 图片字节
        """

        file_name = f"{uuid.uuid4().hex}.jpeg"
        image_path = pconfig.cache_dir / file_name
        async with aiofiles.open(image_path, "wb+") as f:
            await f.write(raw)
        return image_path


RENDERER = Renderer()
