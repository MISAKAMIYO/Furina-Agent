from __future__ import annotations
import re

from . import HandlerFilter


class RegexFilter(HandlerFilter):
    def __init__(self, regex: str | re.Pattern) -> None:
        self.regex = re.compile(regex) if isinstance(regex, str) else regex

    def filter(self, event, cfg: dict) -> bool:
        return bool(self.regex.search(event.message_str))