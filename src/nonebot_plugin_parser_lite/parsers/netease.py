import re
from typing import ClassVar

from nonebot import logger

from ..data import MediaContent, Platform
from .base import (
    BaseParser,
    MatchWithParams,
    ParseException,
    PlatformEnum,
    handle,
)

class NCMParser(BaseParser):
    # 平台信息
    platform: ClassVar[Platform] = Platform(
        name=PlatformEnum.NETEASE, display_name="网易云音乐"
    )

    def __init__(self):
        super().__init__()
        self.short_url_pattern = re.compile(r"(http:|https:)//163cn\.tv/([a-zA-Z0-9]+)")

    async def parse_ncm(self, ncm_url: str) -> dict:
        """解析网易云音乐链接"""
        # 处理短链接
        if matched := self.short_url_pattern.search(ncm_url):
            ncm_url = matched.group(0)
            ncm_url = await self.get_final_url(ncm_url)

        # 获取网易云歌曲id
        matched = re.search(r"(?:\?|&)id=(\d+)", ncm_url) or re.search(
            r"song/(\d+)", ncm_url
        )

        if not matched:
            raise ParseException(f"无效网易云链接: {ncm_url}")

        ncm_id = matched.group(1)
        logger.info(f"成功提取ID: {ncm_id} 来自 {ncm_url}")

        try:
            # === 1. 杀手锏：获取解灰音频链接 ===
            resp_url = await self.httpx.get(
                "http://127.0.0.1:4000/song/url/v1",
                params={"id": ncm_id, "level": "standard", "unblock": "true"},
                timeout=30.0
            )
            resp_url.raise_for_status()
            data_url = resp_url.json()
            
            if data_url.get("code") != 200 or not data_url.get("data"):
                raise ParseException(f"网易云音频获取失败: {data_url}")
            
            audio_url = data_url["data"][0].get("url")
            if not audio_url:
                raise ParseException("未找到可用的音频链接，可能由于版权限制且后台解灰失败。")
            
            # ✨ 动态获取真实格式、码率和音质级别
            audio_type = data_url["data"][0].get("type", "mp3")
            if not audio_type:
                audio_type = "mp3"
            audio_type = audio_type.lower()
            
            audio_level = data_url["data"][0].get("level", "standard")
            audio_br = data_url["data"][0].get("br", 0)
            br_str = f"({audio_br // 1000}kbps)" if audio_br else ""

            # API 返回的大小是字节
            size_bytes = data_url["data"][0].get("size", 0)
            
            # ✨ 抛光修复：如果解灰引擎没有返回大小，主动向源服务器发个 HEAD 请求看一眼真实体积
            if not size_bytes and audio_url:
                try:
                    head_resp = await self.httpx.head(audio_url, follow_redirects=True, timeout=5.0)
                    size_bytes = int(head_resp.headers.get("Content-Length", 0))
                except Exception as e:
                    logger.warning(f"探测解灰音频大小失败: {e}")

            audio_size = f"{size_bytes / (1024 * 1024):.2f}MB" if size_bytes else "未知大小"

            # === 2. 获取歌曲详情 (标题、歌手、封面) ===
            # 注意：详情接口的参数名是 ids (复数)
            resp_detail = await self.httpx.get(
                "http://127.0.0.1:4000/song/detail",
                params={"ids": ncm_id}
            )
            resp_detail.raise_for_status()
            data_detail = resp_detail.json()
            
            song_info = data_detail.get("songs", [{}])[0]
            title = song_info.get("name", "未知歌曲")
            author = "/".join([ar.get("name", "") for ar in song_info.get("ar", [])])
            cover_url = song_info.get("al", {}).get("picUrl", "")

            # === 3. 获取歌词 ===
            lyric = ""
            try:
                resp_lyric = await self.httpx.get(
                    "http://127.0.0.1:4000/lyric",
                    params={"id": ncm_id}
                )
                lyric_data = resp_lyric.json()
                if "lrc" in lyric_data and "lyric" in lyric_data["lrc"]:
                    lyric = lyric_data["lrc"]["lyric"]
                    logger.info(f"找到歌词，长度: {len(lyric)}字符")
            except Exception as e:
                logger.warning(f"获取歌词失败，忽略: {e}")

            logger.info(f"解析成功: {title} - {author}")
            # ✨ 智能纠错：不信任 API 的 level，用真实的码率和格式判断音质
            if audio_type == "flac" or (audio_br and audio_br >= 700000):
                quality_tag = "无损"
            elif audio_br and audio_br >= 320000:
                quality_tag = "极高"
            else:
                quality_tag = "标准"

            # ✨ 动态拼接完美的 audio_info
            audio_info = f"音质: {quality_tag}{br_str} | 格式: {audio_type.upper()} | 大小: {audio_size}"

            # 成功获取，返回标准字典（注意这里新增了 audio_type 字段带给外层）
            return {
                "title": title,
                "author": author,
                "audio_info": audio_info,
                "cover_url": cover_url,
                "audio_url": audio_url,
                "audio_type": audio_type,
                "mv_info": {},  # 新API暂不请求MV信息
                "lyric": lyric,
            }

        except ParseException:
            raise
        except Exception as e:
            raise ParseException(f"网易云 API 请求异常: {e}")

    @handle("music.163.com", r"https?://[^\s]*?music\.163\.com.*?(?:id=\d+|song/\d+)")
    @handle("163cn.tv", r"https?://[^\s]*?163cn\.tv/[a-zA-Z0-9]+")
    async def _parse_netease(self, searched: MatchWithParams):
        """解析网易云音乐分享链接"""
        share_url = searched.url
        logger.debug(f"触发网易云解析: {share_url}")

        # 解析网易云音乐
        result = await self.parse_ncm(share_url)
        # 构建文本内容
        text = f"{result['audio_info']}"
        if result["lyric"]:
            text += f"\n歌词:\n{result['lyric']}"

        contents: list[MediaContent] = []

        # 创建音频内容
        if result["audio_url"]:
            # ✨ 获取刚才字典里传出来的真实后缀，动态拼接
            ext = result.get("audio_type", "mp3")
            audio_name = f"{result['title']}-{result['author']}.{ext}"
            contents.append(
                self.create_audio(
                    result["audio_url"],
                    0.0,
                    audio_name=audio_name,  # 暂时无法从API获取准确时长
                )
            )

        # 创建封面图片内容

        contents.append(self.create_image(result["cover_url"], need_send=False))

        # 构建额外信息
        extra = {
            "info": result["audio_info"],
            "lyric": text,
            "type": "audio",
            "type_tag": "音乐",
            "type_icon": "fa-music",
        }

        return self.result(
            title=result["title"],
            author=self.create_author(name=result["author"]),
            url=share_url,
            content=contents,
            extra=extra,
        )