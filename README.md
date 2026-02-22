<div align="center">
<a href="https://v2.nonebot.dev/store">
    <img src="https://raw.githubusercontent.com/fllesser/nonebot-plugin-template/refs/heads/resource/.docs/NoneBotPlugin.svg" width="310" alt="logo">
</a>

## ✨ [Nonebot2](https://github.com/nonebot/nonebot2) 链接分享自动解析插件 ✨

</div>

## 📖 介绍

| 平台       | 触发的消息形态                    | 视频 | 图集          | 音频   |
| ---------- | --------------------------------- | ---- | ------------- | ------ |
| B 站       | av 号/BV 号/链接/短链/卡片/小程序 | ✅​  | ✅​           | ✅​    |
| 抖音       | 链接(分享链接，兼容电脑端链接)    | ✅​  | ✅​           | ❌️     |
| 微博       | 链接(博文，视频，show, 文章)      | ✅​  | ✅​           | ❌️     |
| 小红书     | 链接(含短链)/卡片                 | ✅​  | ✅​           | ❌️     |
| 快手       | 链接(包含标准链接和短链)          | ✅​  | ✅​           | ❌️     |
| acfun      | 链接                              | ✅​  | ❌️            | ❌️     |
| tiktok     | 链接                              | ✅​  | ❌️            | ❌️     |
| twitter    | 链接                              | ✅​  | ✅​           | ❌️     |
| 酷狗音乐   | 链接(分享链接，歌曲链接)          | ❌️   | ❌️            | ✅​    |
| 网易云音乐 | 链接(分享链接，短链接)            | ❌️   | ❌️            | ✅​    |
| 汽水音乐   | 链接(分享链接)                    | ❌️   | ❌️            | ✅​    |
| 酷我音乐   | 链接(分享链接)                    | ❌️   | ❌️            | ✅​    |
| 百度贴吧   | 链接                              | 没写 | ✅​(图文帖子) | 不存在 |
| TapTap     | 链接(游戏详情、帖子链接)          | ✅   | ✅            | ❌️     |
| 今日头条   | 链接(视频链接)                    | ✅   | ❌️            | ❌️     |

支持的链接，可参考 [测试链接](https://github.com/fllesser/nonebot-plugin-parser/blob/master/tests/others/test_urls.md)

## 💿 安装

把插件文件夹放到插件加载目录

## TODO

- [ ] 把所有网络请求换成 `curl_cffi`

## ⚙️ 配置

<details>
<summary>配置项</summary>

```bash
# [可选] nonebot2 内置配置，若服务器上传带宽太低，建议调高，防止超时
API_TIMEOUT=30.0

# [可选] B 站 cookie, 必须含有 SESSDATA 项，可附加 B 站 AI 总结功能
# 如果需要长期使用此凭据则不应该在浏览器登录账户导致 cookie 被刷新，建议注册个小号获取
# 各项获取方式 https://nemo2011.github.io/bilibili-api/#/get-credential
# ac_time_value 相对特殊，仅用于刷新 Cookies
# B站网页打开开发者工具，进入控制台，输入 window.localStorage.ac_time_value 即可获取其值。
parser_bili_ck="SESSDATA=xxxxxxxxxx;ac_time_value=131231241231241"

# [可选] 允许的 B 站视频编码，越靠前的编码优先级越高
# 可选 "avc"(H.264，体积较大), "hev"(HEVC), "av01"(AV1)
# 后两项在不同设备可能有兼容性问题，如需完全避免，可只填一项，如 '["avc"]'
parser_bili_video_codes=["avc", "av01", "hev"]

# [可选] B 站视频清晰度
# 360p(16), 480p(32), 720p(64), 1080p(80), 1080p+(112), 1080p_60(116), 4k(120)
parser_bili_video_quality=80

# [可选] 小红书 Cookie, 部分链接无法解析，可填
parser_xhs_ck=""

# [可选] 代理, 仅作用于 tiktok 解析
# 推特解析会自动读取环境变量中的 http_proxy / https_proxy(代理软件通常会自动设置)
parser_proxy=None

# [可选] 音频解析，是否需要上传群文件
parser_need_upload=False

# [可选] 音频解析，是否发送歌词
parser_send_lyrics=True

# [可选] 音频解析，是否合并发送消息
parser_combine_message=True

# [可选] 音频解析，是否优先使用高质量音质
parser_prefer_high_quality=True

# [可选] 音频解析，超时时间，单位：秒
parser_audio_timeout=30.0

# [可选] 视频，图片，音频是否使用 base64 发送
# 注意：编解码和传输 base64 会占用更多的内存,性能和带宽, 甚至可能会使 websocket 连接崩溃
# 因此该配置项仅推荐 nonebot 和 协议端不在同一机器的用户配置
parser_use_base64=False

# [可选] 视频最大解析时长，单位：秒
parser_duration_maximum=480

# [可选] 音视频下载最大文件大小，单位 MB，超过该配置将阻断下载
parser_max_size=90

# [可选] 全局禁止的解析
# 示例 parser_disabled_platforms=["bilibili", "douyin"] 表示禁止了哔哩哔哩和抖音
# 可选值: ["bilibili", "douyin", "kuaishou", "twitter", "acfun", "tiktok", "weibo", "xiaohongshu"]
parser_disabled_platforms=["twitter"]

# [可选] 是否在解析结果中附加原始URL
parser_append_url=False

# [可选] 是否需要转发媒体内容(超过 4 项时始终使用合并转发)
parser_need_forward_contents=True
```

</details>

## 🎉 使用

|   指令   |         权限          | 需要@ | 范围 |       说明        |
| :------: | :-------------------: | :---: | :--: | :---------------: |
| 开启解析 | SUPERUSER/OWNER/ADMIN |  是   | 群聊 |     开启解析      |
| 关闭解析 | SUPERUSER/OWNER/ADMIN |  是   | 群聊 |     关闭解析      |
|    bm    |           -           |  否   | 群聊 |   下载 B 站音频   |
|  blogin  |       SUPERUSER       |  否   | 私聊 | 扫码获取 B 站凭证 |

## 🧩 扩展

> [!IMPORTANT]
> 插件自 `v2.2.0` 版本开始支持自定义解析器，通过继承 `BaseParser` 类并实现 `platform`, `handle` 即可
>
> 若插件需要支持富文本内容
>
> 请将富文本列表传入 `self.result` 的 `contents` 字段(对应的纯文本内容[应包含占位符]应传入 `text` 字段)
>
> 并在 **自定义** 模板中使用 `{{ result.content | safe}}` 展示
>
> 默认兜底模板 **不支持** 富文本内容

<details>
<summary>完整示例</summary>

```python
from re import Match
from typing import ClassVar

from httpx import AsyncClient
from .base import BaseParser, Platform, handle

class ExampleParser(BaseParser):
    """示例视频网站解析器"""

    platform: ClassVar[Platform] = Platform(name="example", display_name="示例网站")

    @handle("ex.short", r"ex\.short/\w+)")
    async def _parse_short_link(self, searched: Match[str]):
        """解析短链"""
        url = f"https://{searched.group(0)}"
        # 重定向再解析，请确保重定向链接的 handle 存在
        # 比如 url 重定向到 example.com/... 就会调用 _parse 解析
        return await self.parse_with_redirect(url)

    @handle("example.com", r"example\.com/video/(?P<video_id>\w+)")
    @handle("exam.ple", r"exam\.ple/(?P<video_id>\w+)")
    async def _parse(self, searched: Match[str]):
        # 1. 提取视频 ID
        video_id = searched.group("video_id")

        # 2. 请求 API 获取视频信息
        async with AsyncClient(headers=self.headers, timeout=self.timeout) as client:
            resp = await client.get(f"https://api.example.com/video/{video_id}")
            resp.raise_for_status()
            data = resp.json()

        # 3. 提取数据
        title = data["title"]
        author_name = data["author"]["name"]
        avatar_url = data["author"]["avatar"]
        video_url = data["video_url"]
        cover_url = data["cover_url"]
        duration = data["duration"]
        timestamp = data["publish_time"]
        description = data.get("description", "")

        # 4. 视频内容
        author = self.create_author(author_name, avatar_url)
        video = self.create_video_content(video_url, cover_url, duration)

        # 5. 图集内容
        image_urls = data.get("images")
        images = self.create_image_contents(image_urls)

        # 6. 返回解析结果
        return self.result(
            title=title,
            text=description,
            author=author,
            contents=[video, *images],
            timestamp=timestamp,
            url=f"https://example.com/video/{video_id}",
        )

```

</details>
<details>
<summary>辅助函数</summary>

> 构建作者信息

```python
author = self.create_author(
    name="作者名",
    avatar_url="https://example.com/avatar.jpg",   # 可选，会自动下载
    description="个性签名"                          # 可选
)
```

> 构建视频内容

```python
# 方式1：传入 URL，自动下载
video = self.create_video_content(
    url_or_task="https://example.com/video.mp4",
    cover_url="https://example.com/cover.jpg",  # 可选
    duration=120.5                               # 可选，单位：秒
)

# 方式2：传入已创建的下载任务
from nonebot_plugin_parser.download import DOWNLOADER
video_task = DOWNLOADER.download_video(url, ext_headers=self.headers)
video = self.create_video_content(
    url_or_task=video_task,
    cover_url=cover_url,
    duration=duration
)
```

> 构建图集内容

```python
# 并发下载图集内容
images = self.create_image_contents([
    "https://example.com/img1.jpg",
    "https://example.com/img2.jpg",
])
```

> 构建图文内容(适用于类似 Bilibili 动态图文混排)

```python
graphics = self.create_graphics_content(
    image_url="https://example.com/image.jpg",
    text="图片前的文字说明",  # 可选
    alt="图片描述"            # 可选，居中显示
)
```

> 创建动图内容（GIF)，平台一般只提供视频（后续插件会做自动转为 gif 的处理)

```python
dynamics = self.create_dynamic_contents([
    "https://example.com/dynamic1.mp4",
    "https://example.com/dynamic2.mp4",
])
```

> 重定向 url

```python
real_url = await self.get_redirect_url(
    url="https://short.url/abc",
    headers=self.headers  # 可选
)
```

> 创建贴纸表情

```python
stickers = self.create_sticker_contents(
    url="http://xx",
    size="small"  # 可选
    # small比文字大一点
    # medium是文字大小两倍大一点

)
```

</details>

## 🎉 致谢

- [nonebot-plugin-resolver](https://github.com/zhiyu1998/nonebot-plugin-resolver) 本插件的上游的上游的上游
- [nonebot-plugin-parser](https://github.com/fllesser/nonebot-plugin-parser) 本插件的上游的上游(时不时从此版本同步一些功能)
- [nonebot-plugin-parser-m](https://github.com/LoCCai/nonebot-plugin-parser-m) 本插件的上游(由此版本修改)
- [parse-video-py](https://github.com/wujunwei928/parse-video-py) Python短视频去水印爬虫
- [Spider_XHS](https://github.com/cv-cat/Spider_XHS) 小红书爬虫数据采集，小红书全域运营解决方案
- [aiotieba](https://github.com/lumina37/aiotieba) 贴吧接口合集✨可用于工具箱/吧务管理/数据采集
- [xhs](https://github.com/ReaJason/xhs) 基于小红书 Web 端进行的请求封装。
- [xhshow](https://github.com/Cloxl/xhshow) 小红书xs纯算 小红书x-s x-s-common xsc 等字段 纯算逆向
