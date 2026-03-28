from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from time import monotonic
from typing import TYPE_CHECKING, Final, Literal

from .models import (
    EventContext,
    GroupState,
    ImageFingerprint,
    PenaltyRecord,
    SpamRecord,
)
from .moderation import activate_group, handle_delayed_event, punish
from .normalize import normalize_event
from .vision import images_are_same

if TYPE_CHECKING:
    from nonebot.adapters import Bot, Event

    from .config import Config
    from .types import GroupKey


@dataclass(slots=True)
class _HandleResult:
    action: Literal["ignore", "follow_up", "punish"] = "ignore"
    matched_count: int = 0
    matched_records: list[SpamRecord] = field(default_factory=list)


class NoSpamService:
    """刷屏检测服务"""

    delayed_grace_seconds: Final[float] = 3.0
    delayed_event_time_tolerance: Final[int] = 2

    def __init__(self, config: Config) -> None:
        self.config = config
        self._groups: dict[GroupKey, GroupState] = {}

    async def handle_event(self, bot: Bot, event: Event) -> None:
        """处理单条事件"""
        context = await normalize_event(bot, event)
        if context is None:
            return

        if self._should_ignore_context(bot, context):
            return

        group_key = self._make_group_key(bot, context.group_id)
        state = self._groups.setdefault(group_key, GroupState())
        result = await self._evaluate_context(bot, context, state)

        if result.action == "punish":
            await punish(
                bot=bot,
                context=context,
                mute_duration=int(self.config.nospam_mute_duration),
                matched_count=result.matched_count,
                matched_records=result.matched_records,
            )
        elif result.action == "follow_up":
            await handle_delayed_event(bot, context)

    def _should_ignore_context(self, bot: Bot, context: EventContext) -> bool:
        if self.config.nospam_ignore_self and str(context.user_id) == str(bot.self_id):
            return True

        return not self.config.should_filter_group(context.group_id)

    async def _evaluate_context(
        self,
        bot: Bot,
        context: EventContext,
        state: GroupState,
    ) -> _HandleResult:
        result = _HandleResult()

        async with state.lock:
            if not await activate_group(bot, context.group_id, state):
                return result

            now = monotonic()
            penalty = state.penalties.get(context.user_id)
            if penalty is not None and self._is_delayed_follow_up(
                context=context,
                penalty=penalty,
                now=now,
            ):
                result.action = "follow_up"
                return result

            if penalty is not None and penalty.delayed_until <= now:
                state.penalties.pop(context.user_id, None)

            matched_count, matched_records = self._remember_event(
                state,
                context,
                now,
            )
            if matched_count < self.config.nospam_threshold:
                return result

            state.records.pop(context.user_id, None)
            state.penalties[context.user_id] = self._build_penalty_record(
                context,
                now,
            )
            result.action = "punish"
            result.matched_count = matched_count
            result.matched_records = matched_records
            return result

    def _make_group_key(self, bot: Bot, group_id: int) -> GroupKey:
        return (type(bot).__module__, str(bot.self_id), group_id)

    def _build_penalty_record(
        self,
        context: EventContext,
        now: float,
    ) -> PenaltyRecord:
        return PenaltyRecord(
            delayed_until=now + self.delayed_grace_seconds,
            exact_key=context.exact_key,
            fuzzy_key=context.fuzzy_key,
            structure_key=context.structure_key,
            text_content=context.text_content,
            source_event_time=context.event_time,
            image_segment_count=context.image_segment_count,
            image_fingerprints=context.image_fingerprints,
        )

    def _remember_event(
        self,
        state: GroupState,
        context: EventContext,
        now: float,
    ) -> tuple[int, list[SpamRecord]]:
        records = state.records.setdefault(context.user_id, deque())
        window_seconds = self.config.nospam_window_seconds

        while records and now - records[0].created_at > window_seconds:
            records.popleft()

        matched_records = [
            record
            for record in records
            if self._is_similar(context=context, record=record)
        ]
        current_record = SpamRecord(
            created_at=now,
            exact_key=context.exact_key,
            fuzzy_key=context.fuzzy_key,
            structure_key=context.structure_key,
            text_content=context.text_content,
            message_id=context.message_id,
            image_segment_count=context.image_segment_count,
            image_fingerprints=context.image_fingerprints,
        )
        records.append(current_record)
        matched_records.append(current_record)
        return len(matched_records), matched_records

    def _is_similar(self, context: EventContext, record: SpamRecord) -> bool:
        if not self._images_are_similar(context=context, record=record):
            return False

        if context.exact_key == record.exact_key:
            return True

        return self._has_text_similarity(
            left_structure=context.structure_key,
            left_text=context.text_content,
            right_structure=record.structure_key,
            right_text=record.text_content,
            image_related=self._is_image_related(
                context.image_segment_count,
                record.image_segment_count,
            ),
        )

    def _images_are_similar(
        self,
        context: EventContext,
        record: SpamRecord,
    ) -> bool:
        return self._image_fingerprints_match(
            left=(
                context.image_segment_count,
                context.image_fingerprints,
                context.exact_key,
            ),
            right=(
                record.image_segment_count,
                record.image_fingerprints,
                record.exact_key,
            ),
        )

    def _is_delayed_follow_up(
        self,
        context: EventContext,
        penalty: PenaltyRecord,
        now: float,
    ) -> bool:
        if penalty.delayed_until <= now:
            return False

        if not self._is_penalty_similar(context=context, penalty=penalty):
            return False

        return self._is_penalty_event_time_allowed(
            context=context,
            penalty=penalty,
        )

    def _is_penalty_similar(
        self,
        context: EventContext,
        penalty: PenaltyRecord,
    ) -> bool:
        if context.exact_key == penalty.exact_key:
            return True

        if not self._penalty_images_are_similar(context=context, penalty=penalty):
            return False

        return self._has_text_similarity(
            left_structure=context.structure_key,
            left_text=context.text_content,
            right_structure=penalty.structure_key,
            right_text=penalty.text_content,
            image_related=self._is_image_related(
                context.image_segment_count,
                penalty.image_segment_count,
            ),
        )

    def _is_penalty_event_time_allowed(
        self,
        context: EventContext,
        penalty: PenaltyRecord,
    ) -> bool:
        if context.event_time is None or penalty.source_event_time is None:
            return False

        return (
            context.event_time
            <= penalty.source_event_time + self.delayed_event_time_tolerance
        )

    def _has_text_similarity(
        self,
        left_structure: tuple[str, ...],
        left_text: str | None,
        right_structure: tuple[str, ...],
        right_text: str | None,
        *,
        image_related: bool,
    ) -> bool:
        if left_structure != right_structure:
            return False

        if left_text is None or right_text is None:
            return image_related

        similarity = SequenceMatcher(
            a=left_text,
            b=right_text,
        ).ratio()
        return similarity >= self.config.nospam_similarity_threshold

    def _penalty_images_are_similar(
        self,
        context: EventContext,
        penalty: PenaltyRecord,
    ) -> bool:
        return self._image_fingerprints_match(
            left=(
                context.image_segment_count,
                context.image_fingerprints,
                context.exact_key,
            ),
            right=(
                penalty.image_segment_count,
                penalty.image_fingerprints,
                penalty.exact_key,
            ),
        )

    def _image_fingerprints_match(
        self,
        *,
        left: tuple[int, tuple[ImageFingerprint, ...], str],
        right: tuple[int, tuple[ImageFingerprint, ...], str],
    ) -> bool:
        left_count, left_fingerprints, left_exact_key = left
        right_count, right_fingerprints, right_exact_key = right

        if left_count == 0 and right_count == 0:
            return True

        if left_count != right_count:
            return False

        if (
            len(left_fingerprints) != left_count
            or len(right_fingerprints) != right_count
        ):
            return left_exact_key == right_exact_key

        return all(
            images_are_same(left, right)
            for left, right in zip(
                left_fingerprints,
                right_fingerprints,
                strict=True,
            )
        )

    def _is_image_related(self, left_count: int, right_count: int) -> bool:
        return left_count > 0 or right_count > 0
