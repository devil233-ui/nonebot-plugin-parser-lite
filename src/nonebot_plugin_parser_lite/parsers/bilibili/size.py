from collections.abc import Awaitable, Callable, Sequence


def get_source_stream_groups(
    video_urls: Sequence[str], audio_urls: Sequence[str] | None
) -> tuple[tuple[str, ...], ...]:
    """Return video and optional audio URLs grouped by fallback order."""
    groups: list[tuple[str, ...]] = []
    if video_urls:
        groups.append(tuple(video_urls))
    if audio_urls:
        groups.append(tuple(audio_urls))
    return tuple(groups)


async def probe_source_size(
    urls: Sequence[str], probe: Callable[[str], Awaitable[int | None]]
) -> int | None:
    """Return the first positive size from a stream's fallback URLs."""
    for url in urls:
        try:
            size = await probe(url)
        except Exception:
            continue
        if isinstance(size, int) and size > 0:
            return size
    return None
