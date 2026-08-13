"""Smoke-test plugin loading and lifecycle callbacks."""

import asyncio

import nonebot

PLUGIN_NAME = "nonebot_plugin_parser_lite"


async def verify() -> None:
    nonebot.init()
    driver = nonebot.get_driver()
    if nonebot.load_plugin(PLUGIN_NAME) is None:
        raise RuntimeError(f"Failed to load {PLUGIN_NAME}")
    await driver._lifespan.startup()
    await driver._lifespan.shutdown()


if __name__ == "__main__":
    asyncio.run(verify())
