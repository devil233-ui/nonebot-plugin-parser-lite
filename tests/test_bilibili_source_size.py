import asyncio
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

SIZE_MODULE = (
    Path(__file__).parents[1]
    / "src/nonebot_plugin_parser_lite/parsers/bilibili/size.py"
)
spec = spec_from_file_location("_parser_lite_bilibili_size_test", SIZE_MODULE)
assert spec is not None
assert spec.loader is not None
size_module = module_from_spec(spec)
spec.loader.exec_module(size_module)

probe_source_size = size_module.probe_source_size


def test_probe_source_size_uses_first_successful_fallback():
    calls = []

    async def probe(url):
        calls.append(url)
        return {"video-primary": None, "video-backup": 320}.get(url)

    assert (
        asyncio.run(probe_source_size(("video-primary", "video-backup"), probe)) == 320
    )
    assert calls == ["video-primary", "video-backup"]


def test_probe_source_size_ignores_probe_errors():
    async def probe(url):
        if url == "video-primary":
            raise RuntimeError("HTTP 403")
        return 320

    assert (
        asyncio.run(probe_source_size(("video-primary", "video-backup"), probe)) == 320
    )
