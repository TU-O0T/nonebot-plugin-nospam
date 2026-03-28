from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections import deque

    from nonebot_plugin_alconna.uniseg import Target


@dataclass(slots=True)
class ImageFingerprint:
    """图片视觉指纹"""

    sha256: str
    width: int
    height: int
    aspect_ratio_milli: int
    average_hash: str
    difference_hash: str
    block_signature: tuple[int, ...]
    edge_signature: tuple[int, ...]


@dataclass(slots=True, init=False)
class SpamRecord:
    """单条刷屏记录"""

    created_at: float
    exact_key: str
    fuzzy_key: str
    structure_key: tuple[str, ...]
    text_content: str | None = None
    message_id: str | None = None
    image_segment_count: int = 0
    image_fingerprints: tuple[ImageFingerprint, ...] = ()

    def __init__(  # noqa: PLR0913
        self,
        created_at: float,
        exact_key: str,
        fuzzy_key: str,
        structure_key: tuple[str, ...],
        text_content: str | None = None,
        message_id: str | None = None,
        image_segment_count: int = 0,
        image_fingerprints: tuple[ImageFingerprint, ...] = (),
    ) -> None:
        self.created_at = created_at
        self.exact_key = exact_key
        self.fuzzy_key = fuzzy_key
        self.structure_key = structure_key
        self.text_content = text_content
        self.message_id = message_id
        self.image_segment_count = image_segment_count
        self.image_fingerprints = image_fingerprints


@dataclass(slots=True, init=False)
class PenaltyRecord:
    """单次处罚后的延迟事件判定记录"""

    delayed_until: float
    exact_key: str
    fuzzy_key: str
    structure_key: tuple[str, ...]
    text_content: str | None = None
    source_event_time: int | None = None
    image_segment_count: int = 0
    image_fingerprints: tuple[ImageFingerprint, ...] = ()

    def __init__(  # noqa: PLR0913
        self,
        delayed_until: float,
        exact_key: str,
        fuzzy_key: str,
        structure_key: tuple[str, ...],
        text_content: str | None = None,
        source_event_time: int | None = None,
        image_segment_count: int = 0,
        image_fingerprints: tuple[ImageFingerprint, ...] = (),
    ) -> None:
        self.delayed_until = delayed_until
        self.exact_key = exact_key
        self.fuzzy_key = fuzzy_key
        self.structure_key = structure_key
        self.text_content = text_content
        self.source_event_time = source_event_time
        self.image_segment_count = image_segment_count
        self.image_fingerprints = image_fingerprints


@dataclass(slots=True, init=False)
class EventContext:
    """归一化后的事件上下文"""

    group_id: int
    user_id: int
    exact_key: str
    fuzzy_key: str
    structure_key: tuple[str, ...]
    text_content: str | None
    event_name: str
    message_id: str | None = None
    target: Target | None = None
    event_time: int | None = None
    image_segment_count: int = 0
    image_fingerprints: tuple[ImageFingerprint, ...] = ()

    def __init__(  # noqa: PLR0913
        self,
        group_id: int,
        user_id: int,
        exact_key: str,
        fuzzy_key: str,
        structure_key: tuple[str, ...],
        text_content: str | None,
        event_name: str,
        message_id: str | None = None,
        target: Target | None = None,
        event_time: int | None = None,
        image_segment_count: int = 0,
        image_fingerprints: tuple[ImageFingerprint, ...] = (),
    ) -> None:
        self.group_id = group_id
        self.user_id = user_id
        self.exact_key = exact_key
        self.fuzzy_key = fuzzy_key
        self.structure_key = structure_key
        self.text_content = text_content
        self.event_name = event_name
        self.message_id = message_id
        self.target = target
        self.event_time = event_time
        self.image_segment_count = image_segment_count
        self.image_fingerprints = image_fingerprints


@dataclass(slots=True)
class GroupState:
    """单个群的运行时状态"""

    activated: bool = False
    enabled: bool = False
    bot_role: str | None = None
    last_role_check_at: float | None = None
    penalties: dict[int, PenaltyRecord] = field(default_factory=dict)
    records: dict[int, deque[SpamRecord]] = field(default_factory=dict)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
