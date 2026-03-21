from __future__ import annotations

from collections import deque
from difflib import SequenceMatcher
from time import monotonic
from typing import TYPE_CHECKING, Final

from .models import EventContext, GroupState, PenaltyRecord, SpamRecord
from .moderation import activate_group, handle_delayed_event, punish
from .normalize import normalize_event
from .vision import images_are_same

if TYPE_CHECKING:
    from nonebot.adapters import Bot, Event

    from .config import Config
    from .types import GroupKey


class NoSpamService:
    """刷屏检测服务"""

    delayed_grace_seconds: Final[float] = 3.0
    delayed_event_time_tolerance: Final[int] = 2

    def __init__(self, config: Config) -> None:
        self.config = config
        self._groups: dict[GroupKey, GroupState] = {}

    async def handle_event(self, bot: Bot, event: Event) -> None:
        """处理单条事件"""
        context = await normalize_event(event)
        if context is None:
            return

        if self.config.nospam_ignore_self and str(context.user_id) == str(bot.self_id):
            return

        if not self.config.should_filter_group(context.group_id):
            return

        group_key = self._make_group_key(bot, context.group_id)
        state = self._groups.setdefault(group_key, GroupState())

        should_follow_up = False
        should_punish = False
        matched_count = 0
        matched_records: list[SpamRecord] = []
        async with state.lock:
            if not await activate_group(bot, context.group_id, state):
                return

            now = monotonic()
            penalty = state.penalties.get(context.user_id)
            if penalty is not None and self._is_delayed_follow_up(
                context=context,
                penalty=penalty,
                now=now,
            ):
                should_follow_up = True
            else:
                if penalty is not None and penalty.delayed_until <= now:
                    state.penalties.pop(context.user_id, None)
                matched_count, matched_records = self._remember_event(
                    state,
                    context,
                    now,
                )
                should_punish = matched_count >= self.config.nospam_threshold
                if should_punish:
                    state.records.pop(context.user_id, None)
                    state.penalties[context.user_id] = PenaltyRecord(
                        delayed_until=now + self.delayed_grace_seconds,
                        exact_key=context.exact_key,
                        fuzzy_key=context.fuzzy_key,
                        structure_key=context.structure_key,
                        text_content=context.text_content,
                        source_event_time=context.event_time,
                        image_segment_count=context.image_segment_count,
                        image_fingerprints=context.image_fingerprints,
                    )

        if should_punish:
            await punish(
                bot=bot,
                context=context,
                mute_duration=int(self.config.nospam_mute_duration),
                matched_count=matched_count,
                matched_records=matched_records,
            )
        elif should_follow_up:
            await handle_delayed_event(bot, context)

    def _make_group_key(self, bot: Bot, group_id: int) -> GroupKey:
        return (type(bot).__module__, str(bot.self_id), group_id)

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
            message_seq=context.message_seq,
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
            image_related=(
                context.image_segment_count > 0 or record.image_segment_count > 0
            ),
        )

    def _images_are_similar(
        self,
        context: EventContext,
        record: SpamRecord,
    ) -> bool:
        if context.image_segment_count == 0 and record.image_segment_count == 0:
            return True

        if context.image_segment_count != record.image_segment_count:
            return False

        if (
            len(context.image_fingerprints) != context.image_segment_count
            or len(record.image_fingerprints) != record.image_segment_count
        ):
            return context.exact_key == record.exact_key

        return all(
            images_are_same(left, right)
            for left, right in zip(
                context.image_fingerprints,
                record.image_fingerprints,
                strict=True,
            )
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
            image_related=(
                context.image_segment_count > 0 or penalty.image_segment_count > 0
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
        if context.image_segment_count == 0 and penalty.image_segment_count == 0:
            return True

        if context.image_segment_count != penalty.image_segment_count:
            return False

        if (
            len(context.image_fingerprints) != context.image_segment_count
            or len(penalty.image_fingerprints) != penalty.image_segment_count
        ):
            return context.exact_key == penalty.exact_key

        return all(
            images_are_same(left, right)
            for left, right in zip(
                context.image_fingerprints,
                penalty.image_fingerprints,
                strict=True,
            )
        )
