import re

from ...utils.format import replace_placeholder_to_sticker
from ..data import MediaContent

COOLAPK_PATTERN = re.compile(r"\[(?P<name>[^]]+)\]")


def format_sticker(text: str) -> list[MediaContent | str]:
    return replace_placeholder_to_sticker(
        text,
        COOLAPK_PATTERN,
        "coolapk",
    )
