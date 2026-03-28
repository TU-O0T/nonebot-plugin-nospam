from nonebot import get_plugin_config, on_message, on_notice
from nonebot.adapters import Bot, Event
from nonebot.plugin import PluginMetadata

from .config import Config
from .service import NoSpamService

__plugin_meta__: PluginMetadata = PluginMetadata(
    name="群刷屏防护",
    description="防刷屏！nonebot2插件，检测群内重复或相似消息，并自动撤回与禁言刷屏成员",
    usage=(
        "无需指令\n"
        "按配置的群白名单或黑名单自动检测刷屏\n"
        "支持普通消息与群戳一戳事件，命中阈值后自动执行撤回与禁言"
    ),
    type="application",
    homepage="https://github.com/TU-O0T/nonebot-plugin-nospam",
    config=Config,
)

config: Config = get_plugin_config(Config)
nospam_service: NoSpamService = NoSpamService(config)


async def handle_group_event(bot: Bot, event: Event) -> None:
    """将目标事件交给刷屏服务处理"""
    await nospam_service.handle_event(bot, event)


message_matcher = on_message(
    priority=1,
    block=False,
    handlers=[handle_group_event],
)
notice_matcher = on_notice(
    priority=1,
    block=False,
    handlers=[handle_group_event],
)

__all__: tuple[str, ...] = (
    "Config",
    "__plugin_meta__",
    "config",
    "handle_group_event",
    "nospam_service",
)
