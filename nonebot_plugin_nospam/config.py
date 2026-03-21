from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, NonNegativeInt, PositiveInt


class Config(BaseModel):
    nospam_list_mode: Literal["whitelist", "blacklist"] = "whitelist"
    """过滤模式"""

    nospam_groups: set[int] = Field(default_factory=set)
    """群白名单或黑名单"""

    nospam_window_seconds: PositiveInt = 15
    """滑动窗口时长，单位为秒"""

    nospam_threshold: int = Field(default=4, ge=2)
    """刷屏的触发阈值"""

    nospam_similarity_threshold: float = Field(default=0.9, ge=0.0, le=1.0)
    """模糊匹配的相似度阈值"""

    nospam_mute_duration: NonNegativeInt = 600
    """禁言时长，设为 0 时仅撤回不禁言"""

    nospam_ignore_self: bool = True
    """是否忽略机器人自身发出的事件"""

    def should_filter_group(self, group_id: int) -> bool:
        """判断目标群是否需要启用过滤"""
        if self.nospam_list_mode == "whitelist":
            return group_id in self.nospam_groups
        return group_id not in self.nospam_groups
