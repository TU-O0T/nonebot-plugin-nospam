from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Final

from nonebot_plugin_alconna.uniseg import (
    Segment,
    Text,
    UniMessage,
    get_message_id,
    get_target,
)

from .models import EventContext, ImageFingerprint
from .vision import build_image_visual_payload

if TYPE_CHECKING:
    from nonebot.adapters import Bot, Event

    from .types import NormalizedList, NormalizedMap, NormalizedValue

NOTICE_KEYWORDS: Final[tuple[str, str]] = ("poke", "nudge")
TEXTUAL_KEYS: Final[frozenset[str]] = frozenset(
    {
        "text",
        "summary",
        "title",
        "content",
        "display_action",
        "display_suffix",
        "new_group_name",
        "comment",
        "nickname",
        "card",
        "special_title",
    }
)
VOLATILE_KEYS: Final[frozenset[str]] = frozenset(
    {
        "_data",
        "_raw",
        "avatar_url",
        "base64",
        "cache",
        "proxy",
        "temp_url",
        "thumb_temp_url",
        "thumb_url",
        "timeout",
        "uri",
        "url",
    }
)


async def normalize_event(bot: Bot, event: Event) -> EventContext | None:
    """归一化消息事件或群提醒事件"""
    event_type = event.get_type()
    if event_type == "message":
        return await _normalize_message_event(bot, event)
    if event_type == "notice":
        return _normalize_notice_event(event)
    return None


def extract_role(member: object) -> str | None:
    """提取群成员权限"""
    if isinstance(member, Mapping):
        role_value = member.get("role")
    else:
        role_value = getattr(member, "role", None)

    if not isinstance(role_value, str):
        return None

    normalized = role_value.casefold()
    return normalized or None


async def _normalize_message_event(bot: Bot, event: Event) -> EventContext | None:
    group_id = _extract_group_id(event)
    user_id = _extract_user_id(event)
    if group_id is None or user_id is None:
        return None

    try:
        message = UniMessage.of(event.get_message(), bot=bot)
    except Exception:  # noqa: BLE001
        return None

    (
        exact_segments,
        fuzzy_segments,
        image_segment_count,
        image_fingerprints,
    ) = await _normalize_message_segments(message, bot=bot, event=event)

    exact_payload: NormalizedMap = {
        "kind": "message",
        "segments": exact_segments,
    }
    fuzzy_payload: NormalizedMap = {
        "kind": "message",
        "segments": fuzzy_segments,
    }

    return EventContext(
        group_id=group_id,
        user_id=user_id,
        exact_key=_dump_payload(exact_payload),
        fuzzy_key=_dump_payload(fuzzy_payload),
        structure_key=tuple(segment.type.casefold() for segment in message),
        text_content=_extract_message_text_content(message),
        event_name=event.get_event_name(),
        message_id=_safe_get_message_id(bot, event),
        target=_safe_get_target(bot, event),
        event_time=_extract_event_time(event),
        image_segment_count=image_segment_count,
        image_fingerprints=image_fingerprints,
    )


def _normalize_notice_event(event: Event) -> EventContext | None:
    event_name = event.get_event_name().lower()
    if not any(keyword in event_name for keyword in NOTICE_KEYWORDS):
        return None

    group_id = _extract_group_id(event)
    user_id = _extract_user_id(event)
    if group_id is None or user_id is None:
        return None

    data = getattr(event, "data", None)
    exact_action, exact_suffix = _normalize_notice_display_fields(data, fuzzy=False)
    fuzzy_action, fuzzy_suffix = _normalize_notice_display_fields(data, fuzzy=True)
    exact_payload: NormalizedMap = {
        "kind": "notice",
        "type": "nudge",
        "target_id": _coerce_int(
            getattr(event, "target_id", None) or getattr(data, "receiver_id", None)
        ),
        "display_action": exact_action,
        "display_suffix": exact_suffix,
    }
    fuzzy_payload: NormalizedMap = {
        "kind": "notice",
        "type": "nudge",
        "target_id": exact_payload["target_id"],
        "display_action": fuzzy_action,
        "display_suffix": fuzzy_suffix,
    }

    return EventContext(
        group_id=group_id,
        user_id=user_id,
        exact_key=_dump_payload(exact_payload),
        fuzzy_key=_dump_payload(fuzzy_payload),
        structure_key=("notice", "nudge"),
        text_content=_join_text_parts([fuzzy_action, fuzzy_suffix]),
        event_name=event.get_event_name(),
        event_time=_extract_event_time(event),
    )


def _extract_group_id(event: Event) -> int | None:
    direct_group_id = _coerce_int(getattr(event, "group_id", None))
    if direct_group_id is not None:
        return direct_group_id

    data = getattr(event, "data", None)
    if data is None:
        return None

    group_id = _coerce_int(getattr(data, "group_id", None))
    if group_id is not None:
        return group_id

    if getattr(data, "message_scene", None) == "group":
        return _coerce_int(getattr(data, "peer_id", None))

    return None


def _extract_user_id(event: Event) -> int | None:
    direct_user_id = _coerce_int(getattr(event, "user_id", None))
    if direct_user_id is not None:
        return direct_user_id

    data = getattr(event, "data", None)
    if data is None:
        return None

    for key in ("sender_id", "user_id"):
        value = _coerce_int(getattr(data, key, None))
        if value is not None:
            return value

    return None


def _extract_event_time(event: Event) -> int | None:
    direct_event_time = _coerce_int(getattr(event, "time", None))
    if direct_event_time is not None:
        return direct_event_time

    data = getattr(event, "data", None)
    if data is None:
        return None

    return _coerce_int(getattr(data, "time", None))


def _normalize_segment(segment: Segment, *, fuzzy: bool) -> NormalizedMap:
    normalized: NormalizedMap = {
        "type": segment.type.casefold(),
        "data": _normalize_mapping(segment.data, fuzzy=fuzzy),
    }
    if segment.children:
        normalized["children"] = [
            _normalize_segment(child, fuzzy=fuzzy) for child in segment.children
        ]
    return normalized


async def _normalize_message_segments(
    message: UniMessage,
    *,
    bot: Bot,
    event: Event,
) -> tuple[
    NormalizedList,
    NormalizedList,
    int,
    tuple[ImageFingerprint, ...],
]:
    exact_segments: NormalizedList = []
    fuzzy_segments: NormalizedList = []
    image_segment_count = 0
    image_fingerprints: list[ImageFingerprint] = []

    for segment in message:
        if segment.type.casefold() == "image":
            image_segment_count += 1
            (
                exact_segment,
                fuzzy_segment,
                fingerprint,
            ) = await build_image_visual_payload(segment, bot=bot, event=event)
            if fingerprint is None:
                exact_segments.append(_normalize_segment(segment, fuzzy=False))
                fuzzy_segments.append(_normalize_segment(segment, fuzzy=True))
            else:
                exact_segments.append(exact_segment)
                fuzzy_segments.append(fuzzy_segment)
                image_fingerprints.append(fingerprint)
            continue

        exact_segments.append(_normalize_segment(segment, fuzzy=False))
        fuzzy_segments.append(_normalize_segment(segment, fuzzy=True))

    return (
        exact_segments,
        fuzzy_segments,
        image_segment_count,
        tuple(image_fingerprints),
    )


def _normalize_mapping(
    data: Mapping[str, object],
    *,
    fuzzy: bool,
) -> NormalizedMap:
    normalized: NormalizedMap = {}
    for key, value in sorted(data.items()):
        if key.startswith("_") or key in VOLATILE_KEYS:
            continue

        normalized_value = _normalize_scalar(value, key=key, fuzzy=fuzzy)
        if normalized_value is None or normalized_value in ({}, []):
            continue
        normalized[key] = normalized_value
    return normalized


def _normalize_scalar(
    value: object,
    *,
    key: str,
    fuzzy: bool,
) -> NormalizedValue | None:
    if value is None:
        return None

    normalized: NormalizedValue | None
    if isinstance(value, Segment):
        normalized = _normalize_segment(value, fuzzy=fuzzy)
    elif isinstance(value, UniMessage):
        normalized = [_normalize_segment(segment, fuzzy=fuzzy) for segment in value]
    elif isinstance(value, Mapping):
        normalized = _normalize_nested_mapping(value, fuzzy=fuzzy)
    elif isinstance(value, (Sequence, set)) and not isinstance(value, str):
        normalized = _normalize_iterable(value, key=key, fuzzy=fuzzy)
    elif isinstance(value, str):
        normalized = _normalize_text(value, fuzzy=fuzzy and key in TEXTUAL_KEYS)
    elif isinstance(value, bool | int | float):
        normalized = value
    elif callable(model_dump := getattr(value, "model_dump", None)):
        normalized = _normalize_scalar(model_dump(), key=key, fuzzy=fuzzy)
    elif hasattr(value, "__dict__"):
        normalized = _normalize_scalar(vars(value), key=key, fuzzy=fuzzy)
    else:
        normalized = _normalize_text(str(value), fuzzy=fuzzy and key in TEXTUAL_KEYS)

    return normalized


def _normalize_nested_mapping(
    value: Mapping[str, object],
    *,
    fuzzy: bool,
) -> NormalizedMap:
    return {
        inner_key: normalized_item
        for inner_key, inner_value in sorted(value.items())
        if (
            normalized_item := _normalize_scalar(
                inner_value,
                key=inner_key,
                fuzzy=fuzzy,
            )
        )
        is not None
    }


def _normalize_iterable(
    value: Sequence[object] | set[object],
    *,
    key: str,
    fuzzy: bool,
) -> list[NormalizedValue]:
    return [
        normalized_item
        for item in value
        if (
            normalized_item := _normalize_scalar(
                item,
                key=key,
                fuzzy=fuzzy,
            )
        )
        is not None
    ]


def _normalize_text(value: str, *, fuzzy: bool) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold().strip()
    normalized = re.sub(r"\s+", " ", normalized)
    if not fuzzy:
        return normalized

    fuzzy_normalized = re.sub(r"[\W_]+", "", normalized, flags=re.UNICODE)
    return fuzzy_normalized or normalized


def _extract_message_text_content(message: UniMessage) -> str | None:
    return _join_text_parts([segment.text for segment in message.select(Text)])


def _normalize_notice_display_fields(
    data: object,
    *,
    fuzzy: bool,
) -> tuple[NormalizedValue | None, NormalizedValue | None]:
    return (
        _normalize_scalar(
            getattr(data, "display_action", None),
            key="display_action",
            fuzzy=fuzzy,
        ),
        _normalize_scalar(
            getattr(data, "display_suffix", None),
            key="display_suffix",
            fuzzy=fuzzy,
        ),
    )


def _collect_text_parts(value: object) -> list[str]:
    if value is None:
        return []

    if isinstance(value, Mapping):
        parts: list[str] = []
        for key, item in sorted(value.items()):
            if key in TEXTUAL_KEYS:
                normalized = _normalize_scalar(item, key=key, fuzzy=True)
                if isinstance(normalized, str) and normalized:
                    parts.append(normalized)
            else:
                parts.extend(_collect_text_parts(item))
        return parts

    if isinstance(value, (Sequence, set)) and not isinstance(value, str):
        parts: list[str] = []
        for item in value:
            parts.extend(_collect_text_parts(item))
        return parts

    return []


def _join_text_parts(parts: Sequence[object]) -> str | None:
    normalized_parts = [
        part
        for part in parts
        if isinstance(part, str) and part
    ]
    if not normalized_parts:
        return None
    return "\x1f".join(normalized_parts)


def _dump_payload(payload: NormalizedMap) -> str:
    return json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _coerce_int(value: object | None) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _safe_get_message_id(bot: Bot, event: Event) -> str | None:
    try:
        return get_message_id(event=event, bot=bot)
    except Exception:  # noqa: BLE001
        return None


def _safe_get_target(bot: Bot, event: Event):
    try:
        return get_target(event=event, bot=bot)
    except Exception:  # noqa: BLE001
        return None
