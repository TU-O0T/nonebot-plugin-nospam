from __future__ import annotations

from time import monotonic
from typing import TYPE_CHECKING, Final

from nonebot.log import logger

from .normalize import extract_role

if TYPE_CHECKING:
    from nonebot.adapters import Bot

    from .models import EventContext, GroupState, SpamRecord

ADMIN_ROLES: Final[frozenset[str]] = frozenset({"admin", "owner"})
ROLE_REFRESH_INTERVAL: Final[float] = 300.0


async def activate_group(bot: Bot, group_id: int, state: GroupState) -> bool:
    """激活群聊"""
    now = monotonic()
    if (
        state.activated
        and state.last_role_check_at is not None
        and now - state.last_role_check_at < ROLE_REFRESH_INTERVAL
    ):
        return state.enabled

    try:
        bot_user_id = int(str(bot.self_id))
    except ValueError:
        logger.warning(
            "防刷屏 跳过群 {}，因为机器人 self_id {} 不是整数",
            group_id,
            bot.self_id,
        )
        return False

    was_activated = state.activated
    previous_role = state.bot_role
    previous_enabled = state.enabled
    state.last_role_check_at = now

    role = await fetch_group_role(bot=bot, group_id=group_id, user_id=bot_user_id)
    if role is None:
        return state.enabled if state.activated else False

    state.activated = True
    state.bot_role = role
    state.enabled = role in ADMIN_ROLES

    if not was_activated and state.enabled:
        logger.info(
            "防刷屏 已在群 {} 激活，机器人权限为 {}",
            group_id,
            role,
        )
    elif not was_activated:
        logger.info(
            "防刷屏 忽略群 {}，因为机器人 {} 没有管理权限",
            group_id,
            role,
        )
    elif previous_role != role or previous_enabled != state.enabled:
        if state.enabled:
            logger.info(
                "防刷屏 群 {} 的机器人权限已更新为 {}，继续处理消息",
                group_id,
                role,
            )
        else:
            logger.info(
                "防刷屏 群 {} 的机器人权限已更新为 {}，暂停处理消息",
                group_id,
                role,
            )

    return state.enabled


async def fetch_group_role(bot: Bot, group_id: int, user_id: int) -> str | None:
    """查询指定成员在群内的权限"""
    try:
        member = await bot.get_group_member_info(
            group_id=group_id,
            user_id=user_id,
            no_cache=False,
        )
    except Exception as exception:  # noqa: BLE001
        logger.opt(exception=exception).warning(
            "防刷屏 查询群 {} 成员权限失败",
            group_id,
        )
        return None

    role = extract_role(member)
    if role is None:
        logger.warning(
            "防刷屏 无法确定机器人在群 {} 中的权限",
            group_id,
        )
        return None

    return role


async def punish(
    bot: Bot,
    context: EventContext,
    mute_duration: int,
    matched_count: int,
    matched_records: list[SpamRecord],
) -> None:
    """执行首次命中阈值后的处罚"""
    recalled_count, recallable_count = await recall_records(
        bot,
        group_id=context.group_id,
        matched_records=matched_records,
    )
    muted = await mute_user(bot, context, mute_duration)

    logger.warning(
        "防刷屏 在群 {} 检测到用户 {} 刷屏 (命中={}, 事件={}, 撤回={}/{}, 禁言={})",
        context.group_id,
        context.user_id,
        matched_count,
        context.event_name,
        recalled_count,
        recallable_count,
        muted,
    )


async def handle_delayed_event(bot: Bot, context: EventContext) -> None:
    """处理处罚后的延迟到达事件"""
    recalled = await recall_event(bot, context)
    logger.info(
        "防刷屏 已处理群 {} 中用户 {} 的延迟事件 (事件={}, 撤回={})",
        context.group_id,
        context.user_id,
        context.event_name,
        recalled,
    )


async def recall_event(bot: Bot, context: EventContext) -> bool:
    """按适配器能力撤回事件对应的消息"""
    return await _recall_message(
        bot=bot,
        group_id=context.group_id,
        message_id=context.message_id,
        message_seq=context.message_seq,
    )


async def recall_records(
    bot: Bot,
    group_id: int,
    matched_records: list[SpamRecord],
) -> tuple[int, int]:
    """批量撤回命中窗口内可撤回的消息"""
    recalled_count = 0
    recallable_count = 0
    seen_message_ids: set[int] = set()
    seen_message_seqs: set[int] = set()

    for record in matched_records:
        if record.message_id is None and record.message_seq is None:
            continue
        if record.message_id is not None:
            if record.message_id in seen_message_ids:
                continue
            seen_message_ids.add(record.message_id)
        if record.message_seq is not None:
            if record.message_seq in seen_message_seqs:
                continue
            seen_message_seqs.add(record.message_seq)

        recallable_count += 1
        if await _recall_message(
            bot=bot,
            group_id=group_id,
            message_id=record.message_id,
            message_seq=record.message_seq,
        ):
            recalled_count += 1

    return recalled_count, recallable_count


async def _recall_message(
    bot: Bot,
    group_id: int,
    message_id: int | None,
    message_seq: int | None,
) -> bool:
    """按消息标识执行撤回"""
    if message_id is not None:
        try:
            await bot.delete_msg(message_id=message_id)
        except Exception as exception:  # noqa: BLE001
            logger.opt(exception=exception).warning(
                "防刷屏 撤回 OneBot 消息 {} 失败，群 {}",
                message_id,
                group_id,
            )
            return False
        return True

    if message_seq is not None:
        try:
            await bot.recall_group_message(
                group_id=group_id,
                message_seq=message_seq,
            )
        except Exception as exception:  # noqa: BLE001
            logger.opt(exception=exception).warning(
                "防刷屏 撤回 Milky 消息 {} 失败，群 {}",
                message_seq,
                group_id,
            )
            return False
        return True

    return False


async def mute_user(bot: Bot, context: EventContext, duration: int) -> bool:
    """按适配器能力禁言目标用户"""
    if duration <= 0:
        return False

    module_name = type(bot).__module__
    if ".onebot.v11." in module_name:
        return await _call_mute_api(
            bot=bot,
            context=context,
            duration=duration,
            api_name="set_group_ban",
        )

    if ".milky." in module_name:
        return await _call_mute_api(
            bot=bot,
            context=context,
            duration=duration,
            api_name="set_group_member_mute",
        )

    if await _call_mute_api(
        bot=bot,
        context=context,
        duration=duration,
        api_name="set_group_ban",
    ):
        return True

    return await _call_mute_api(
        bot=bot,
        context=context,
        duration=duration,
        api_name="set_group_member_mute",
    )


async def _call_mute_api(
    bot: Bot,
    context: EventContext,
    duration: int,
    api_name: str,
) -> bool:
    payload: dict[str, int] = {
        "group_id": context.group_id,
        "user_id": context.user_id,
        "duration": duration,
    }

    try:
        await bot.call_api(api_name, **payload)
    except Exception as exception:  # noqa: BLE001
        logger.opt(exception=exception).warning(
            "防刷屏 通过 {} 禁言群 {} 的用户 {} 失败",
            api_name,
            context.group_id,
            context.user_id,
        )
        return False

    return True
