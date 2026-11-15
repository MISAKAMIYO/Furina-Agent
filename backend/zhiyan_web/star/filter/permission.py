from __future__ import annotations
from enum import Enum, auto


class PermissionType(Enum):
    MEMBER = auto()
    ADMIN = auto()


class PermissionTypeFilter:
    def __init__(self, permission_type: PermissionType, raise_error: bool = True) -> None:
        self.permission_type = permission_type
        self.raise_error = raise_error

    def filter(self, event, cfg: dict) -> bool:
        if self.permission_type == PermissionType.ADMIN:
            return event.role == "admin"
        return True