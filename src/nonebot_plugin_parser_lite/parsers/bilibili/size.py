from collections.abc import Awaitable, Callable


async def probe_source_size(
    urls: tuple[str, ...],
    probe: Callable[[str], Awaitable[int | None]],
) -> int | None:
    for url in urls:
        try:
            if size := await probe(url):
                return size
        except Exception:
            continue
