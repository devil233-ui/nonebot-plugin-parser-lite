import asyncio
import json

from nonebot import get_driver
from nonebot.adapters import Bot, Event
from nonebot.permission import SUPERUSER
from nonebot.rule import to_me
from nonebot_plugin_alconna import Alconna, Args, Match, UniMessage, on_alconna
from nonebot_plugin_uninfo import ADMIN, Uninfo

from ..config import pconfig

_DISABLED_TARGETS_PATH = pconfig.data_dir / "disabled_targets.json"
_DISABLED_TARGETS_SET: set[str] = set()
_DISABLED_TARGETS_LOCK = asyncio.Lock()


async def _write_disabled_targets(targets: set[str]) -> None:
    temp_path = _DISABLED_TARGETS_PATH.with_suffix(".json.tmp")
    await temp_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        await temp_path.write_text(
            json.dumps(sorted(targets), ensure_ascii=False),
            encoding="utf-8",
        )
        await temp_path.replace(_DISABLED_TARGETS_PATH)
    finally:
        await temp_path.unlink(missing_ok=True)


async def set_target_enabled(target_key: str, enabled: bool) -> bool:
    """原子更新开关，返回状态是否发生变化"""
    async with _DISABLED_TARGETS_LOCK:
        if (target_key not in _DISABLED_TARGETS_SET) == enabled:
            return False
        updated = _DISABLED_TARGETS_SET.copy()
        if enabled:
            updated.discard(target_key)
        else:
            updated.add(target_key)
        await _write_disabled_targets(updated)
        _DISABLED_TARGETS_SET.clear()
        _DISABLED_TARGETS_SET.update(updated)
        return True


def _make_user_key(session: Uninfo, target: str | None = None) -> str:
    return f"{session.scope}_USER_{target or session.user.id}"


def _make_target_key(session: Uninfo, target: str | None = None) -> str:
    scene = session.scene
    if scene.is_private:
        return _make_user_key(session, target)
    target_id = target or scene.id
    parts = [str(session.scope), scene.type.name]
    if scene.parent and not scene.is_group:
        parts.append(scene.parent.id)
    parts.append(target_id)
    return "_".join(parts)


@get_driver().on_startup
async def init_disabled_targets() -> None:
    """初始化关闭解析的名单"""
    async with _DISABLED_TARGETS_LOCK:
        if await _DISABLED_TARGETS_PATH.exists():
            targets = json.loads(
                await _DISABLED_TARGETS_PATH.read_text(encoding="utf-8")
            )
            _DISABLED_TARGETS_SET.clear()
            _DISABLED_TARGETS_SET.update(targets)
        else:
            await _write_disabled_targets(_DISABLED_TARGETS_SET)


# Rule
def is_enabled(session: Uninfo) -> bool:
    return (
        _make_user_key(session) not in _DISABLED_TARGETS_SET
        and _make_target_key(session) not in _DISABLED_TARGETS_SET
    )


async def _toggle_target(
    bot: Bot,
    event: Event,
    session: Uninfo,
    target: Match[str],
    enabled: bool,
) -> None:
    target_id = (
        target.result if target.available and await SUPERUSER(bot, event) else None
    )
    target_key = _make_target_key(session, target_id)
    changed = await set_target_enabled(target_key, enabled=enabled)
    action = "开启" if enabled else "关闭"
    message = UniMessage(f"{target_key} 解析已{action}")
    if not changed:
        message += f"，无需重复{action}"
    await message.send(reply_to=True)


@on_alconna(
    Alconna("开启解析", Args["target?", str]),
    rule=to_me(),
    permission=SUPERUSER | ADMIN(),
    block=True,
).handle()
async def handle_enable(bot: Bot, event: Event, session: Uninfo, target: Match[str]):
    """开启当前群/用户解析；超级管理员可指定其他群/用户"""
    await _toggle_target(bot, event, session, target, True)


@on_alconna(
    Alconna("关闭解析", Args["target?", str]),
    rule=to_me(),
    permission=SUPERUSER | ADMIN(),
    block=True,
).handle()
async def handle_disable(bot: Bot, event: Event, session: Uninfo, target: Match[str]):
    """关闭当前群/用户解析；超级管理员可指定其他群/用户"""
    await _toggle_target(bot, event, session, target, False)
