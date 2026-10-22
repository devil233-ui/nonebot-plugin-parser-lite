"""本地增强配置（与上游 ``Config`` 隔离）。

上游持续在 ``config.py`` 的字段区、属性区末尾追加内容；本地若也插在同一处，
每次 ``git pull --rebase`` 都会在这两段产生冲突。这里用 mixin 承载本地专有
配置项，``config.py`` 只需两行接入（import + 类头继承），上游追加再多也碰不到
本地代码。

新增本地配置项请写在本文件，不要写回 ``config.py``。
"""

from __future__ import annotations

from pydantic import BaseModel


class LocalConfigMixin(BaseModel):
    """本地专有的配置字段与只读属性（通过继承并入 ``Config``）。"""

    plite_ytb_ck: str | None = None
    """youtube cookies，Cookie 字符串会转换为 Netscape 文件"""
    plite_forward_node_threshold: int = 4
    """触发合并转发的图文混合节点数阈值"""
    plite_forward_long_text_threshold: int = 1500
    """判断为超长文本的字数阈值"""
    plite_forward_small_batch_size: int = 4
    """超长文本情况下的单次转发节点数"""
    plite_forward_large_batch_size: int = 99
    """普通情况下的单次转发节点数"""
    plite_netease_local_api: str | None = None
    """网易云本地 API 地址；配置后优先使用，失败时回退上游接口"""

    @property
    def ytb_ck(self) -> str | None:
        """youtube cookies"""
        return self.plite_ytb_ck

    @property
    def forward_node_threshold(self) -> int:
        """触发合并转发的图文混合节点数阈值"""
        return self.plite_forward_node_threshold

    @property
    def forward_long_text_threshold(self) -> int:
        """判断为超长文本的字数阈值"""
        return self.plite_forward_long_text_threshold

    @property
    def forward_small_batch_size(self) -> int:
        """超长文本情况下（即字为主）的单次转发节点装箱量"""
        return self.plite_forward_small_batch_size

    @property
    def forward_large_batch_size(self) -> int:
        """普通情况下（即图为主）的单次转发节点装箱量"""
        return self.plite_forward_large_batch_size
