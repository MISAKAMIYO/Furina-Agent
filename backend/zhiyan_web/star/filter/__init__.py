from __future__ import annotations
import abc
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..platform.astr_message_event import AstrMessageEvent


class HandlerFilter(abc.ABC):
    @abc.abstractmethod
    def filter(self, event: AstrMessageEvent, cfg: dict) -> bool:
        raise NotImplementedError