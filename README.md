<div align="center">
    <a href="https://v2.nonebot.dev/store">
    <img src="https://raw.githubusercontent.com/fllesser/nonebot-plugin-template/refs/heads/resource/.docs/NoneBotPlugin.svg" width="310" alt="logo"></a>

# NoneBot-Plugin-NoSpam
_✨ NoneBot2 智能 QQ 群刷屏防护插件 ✨_

<p align="center">
  <img src="https://img.shields.io/badge/python-3.10+-blue.svg" alt="python">
  <img src="https://img.shields.io/badge/nonebot2-2.3.0+-red.svg" alt="nonebot2">
  <a href="https://github.com/astral-sh/uv">
  <img src="https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json" alt="uv">
  </a>
  <img src="https://img.shields.io/badge/license-MIT-green.svg" alt="license">
</p>

</div>

## 📖 介绍

**nonebot-plugin-nospam** 是一款专为 QQ 群设计的全自动、高智能的防刷屏 NoneBot2 插件。

除了简单的重复文字刷屏外，还可以进行**文本模糊匹配**和**图像感知哈希**，可以识别“相似图片/文本”等刷屏方式。检测到刷屏后，插件会自动撤回刷屏消息并进行禁言处理（如果配置）。

## 🚀 核心特性

- **🧩 适配器兼容**：理论支持 `OneBot V11` 与 `Milky` 适配器。Milky暂未测试。
- **🧠 智能文本检测**：通过文本结构归一化和 Levenshtein 模糊算法相似度计算，防范形变与掺杂无意义字符的文字刷屏。
- **👁️ 视觉反垃圾**：通过图像感知计算，提取得出平均哈希、差异哈希、图像边缘与区域签名，识别出经过压缩或尺寸变化后的重复图片同时，防止大体结构相同但内容不同的图片被拦截。
- **👉 互动事件拦截**：支持“群戳一戳”等触发事件的连续刷屏拦截。
- **⚡ 全自动惩罚措施**：自由配置滑动窗口期触发阈值，自动禁言成员并**批量撤回**窗口期内的相关联刷屏消息。
- **🛡️ 动态权限感知**：无需重启！机器人在群内被提为管理员或降级时，插件能动态感知自身权限状态，决定接管还是挂起对应群的检测任务，避免产生不必要的 API 调用，降低风控风险。

## 📥 安装配置

### 安装方式

**使用 `nb-cli` 安装（骗你的，还没上架）**
```shell
nb plugin install nonebot-plugin-nospam
```

**使用 `pip` 安装**
```shell
pip install nonebot-plugin-nospam
```
> 如果使用 `pip` 安装，请记得在 NoneBot 项目的 `pyproject.toml` 或 `bot.py`~~（真的还有人在用这玩意吗？）~~ 中手动加载插件。

### ⚙️ 环境配置

在项目的 `.env.*` 配置文件中添加下方必要配置项：

| 配置项 | 类型 | 默认值 | 描述 |
| ------ | ---- | ------ | ---- |
| `nospam_list_mode` | `string` | `"whitelist"` | 过滤策略，可选 `"whitelist"` (白名单) 或 `"blacklist"` (黑名单)。 |
| `nospam_groups` | `set[int]`| `[]` | 设置白名单或黑名单涉及的群号列表。如 `[12345678, 87654321]` |
| `nospam_window_seconds`| `int` | `15` | 滑动检测窗口时长（单位：秒）。即统计这 N 秒内同一个人发送的消息。 |
| `nospam_threshold` | `int` | `4` | 刷屏触发阈值限制（最小为2）。当在同一窗口期内相似消息条数达到此值时执行处罚。 |
| `nospam_similarity_threshold` | `float` | `0.9` | 文本模糊匹配的**重合度相似度阈值**（范围 `0.0 ~ 1.0`）。 |
| `nospam_mute_duration` | `int` | `600` | 命中刷屏后自动**禁言时长**（单位：秒）。如果设为 `0` 则仅撤回刷屏内容而不禁言。 |
| `nospam_ignore_self` | `bool` | `true` | 是否忽略机器人自身发出的消息和各类事件。 |

**配置文件示例:**
```dotenv
nospam_list_mode="whitelist"
nospam_groups='["111111111", "222222222"]'
nospam_window_seconds=15
nospam_threshold=4
nospam_similarity_threshold=0.85
nospam_mute_duration=600
nospam_ignore_self=true
```

## 🎮 使用方法

**没有任何触发指令**

只需在配置中添加（白名单模式）或避免排除群组（黑名单模式），并确保机器人在对应的 QQ 群内拥有 **管理员** 或 **群主** 权限。

## 📄 许可协议

本项目使用 [MIT](https://opensource.org/licenses/MIT) 许可证开源。
