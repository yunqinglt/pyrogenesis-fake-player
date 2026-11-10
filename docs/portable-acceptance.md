# 可移植交付验收与用法

核对日期：2026-11-07。交付为可重新安装的源码／文档包；不是附带 Python、游戏和全部依赖的离线运行时。三个 ZIP 分别解压后根目录为 `server/`、`player-kit/` 和 `skill-kit/`。

Linux 端已增加 Bash 安装／启动入口、共享库与游戏数据路径检查，以及机器执行分支。命令和验证边界见 [Linux 指南](../portable/server/LINUX.md)；下表中的原有真机原生依赖结果为 Windows，不能视为 Linux 实战通过。

| 验收项 | 结果与证据 |
| --- | --- |
| 服务器脱离原工作区 | 通过：含中文和空格的临时目录，全新虚拟环境正常构建 wheel 并安装；从独立 `site-packages` 导入 |
| 伴随模组可安装 | 通过：作为 package data 打包，7 个关键文件自检通过 |
| MCP 协议与工具 | 通过：真实 stdio 初始化、列举 24 个工具（含 `analyze_terrain_region`）、空闲 `player_status`；未启动游戏 |
| 目标机原生依赖 | 本机通过：Python 3.13.3、0 A.D. 0.28.0、ENet 1.3.18 均 x64；其他机器仍须运行自检 |
| Linux 安装与入口 | Arch Linux／WSL2 x64、Python 3.14.9：中文与空格目录内真实安装、companion、MCP 24 工具／空闲状态、Bash 包装入口及 pip check 通过；Bash 语法和 ShellCheck 0.11.0 通过 |
| Linux 原生通信 | ENet 1.3.18 加载、UDP 回环与中继测试通过；Linux 图形游戏启动、跨系统房间连接及对局动作未验证 |
| Windows 双 MCP 同局 | 通过：两个独立 MCP 进程以不同昵称加入用户同一房间，各自观察和移动；交叉控制均拒绝；退出一方后另一方继续操作；同名重新加入恢复原玩家席位。最终保留两个连接，详见 [实测记录](validation.md) |
| 提示词与游戏文档 | 通过：包含入口、系统提示词、任务模板、AGENT.md、MCP.md、动作／属性／半自动化／策略说明，内部文件链接已检查 |
| 三个在线策略技能可迁移 | 通过：独立 SKILL.md，无原机器路径依赖；附属脚本只依赖 Python 标准库；三个入口均通过 quick_validate |
| 独立残局分析技能 | 2026-11-07 补充：SKILL.md 通过 quick_validate；两种资料包包含同一解析器，隔离 Python（无项目路径和第三方 site-packages）可完整生成三种报告并拒绝覆盖。红海、下努比亚两份真实人对 Petra 存档完整解码，玩家分类核对通过 |
| 技能决策边界 | 合成场景及历史公开观察验证通过：队列／数量／人口不确定性、跨基地排除、类别筛选、忙碌／驻军排除和 200 人分批；未在当前房间执行新技能 |
| 发行包完整性 | 通过：固定条目顺序／时间，可重复 ZIP；逐文件 SHA-256 清单、校验器及外部 SHA256SUMS；拒绝覆盖既有输出 |
| 私人及非必要数据 | 未打包虚拟环境、存档、截图、崩溃转储、游戏资源、个人 MCP 配置；文档副本用户名已替换 |
| 全项目回归 | 本轮 Windows：待本地重跑后填写；聚焦地形／协议回归已通过。历史 Windows：757 passed、6 skipped；Linux：762 passed、1 skipped；两者均 5 subtests passed。浏览器契约 10/10 PASS |
| 游戏内已知异常 | 未修复：历史影像混入己方枚举的 JS 错误、部分存档恢复的原生崩溃；详见各包 KNOWN_ISSUES.md（独立技能包不含该文件，见玩家／服务器包） |

## 安装服务器

目标机器先安装 64 位 Python 3.11+ 和 0 A.D. 0.28.0。首次安装需要联网下载 Python 依赖。以下假设 server 已解压到 `D:\0ad-portable\server`；换成当前机器的真实位置。

```powershell
Set-Location 'D:\0ad-portable\server'
python .\verify_bundle.py
.\install.ps1 -PythonExe 'C:\Path\To\Python\python.exe'
$env:PYROGENESIS_EXE = 'C:\Path\To\0 A.D.\binaries\system\pyrogenesis.exe'
$env:PYROGENESIS_ENET_DLL = 'C:\Path\To\0 A.D.\binaries\system\enet.dll'
.\.venv\Scripts\python.exe .\check_portable.py --check-game
```

自检输出应有 `ok=true`、`stdio.tool_count=24` 和 `stdio.idle_status.running=false`。运行中的房主可能锁住公开资源包，版本检查会说明无法读取；保留房主，人工核对版本。机器可读步骤见服务器包的 `RUNBOOK.json`。

MCP 客户端启动参数为：`command` 指向上述 `.venv\Scripts\python.exe`，`args=["-m","pyrogenesis_mcp.server"]`，环境中配置这两个游戏路径。安装和自检命令不作为 MCP 启动命令。通用 JSON／Codex TOML 的完整例子见 [配置指南源稿](../portable/server/CONFIGURATION.md)。移动目录或换机器后重新安装虚拟环境。

## 提供提示词与文档

给玩家模型读取 player-kit 内的 `ENTRYPOINT.md`、`AGENT.md` 和 `MCP.md`，也可使用 `prompts/player-system.md` 与 `prompts/task-template.md`。MCP 连接不会自动加载这些文件；本项目的 `AGENT.md` 也不保证被客户端当作 `AGENTS.md` 自动发现。

开始任务时明确房主、UDP 端口和本轮目标。例如：

```text
读取玩家包 ENTRYPOINT.md、AGENT.md、MCP.md，作为普通玩家完成本轮任务。
房主 127.0.0.1，端口 20595；先检查已有连接，再按需要加入。
实体 ID 以本局公开观察为准，结束后保留连接。
```

`127.0.0.1` 仅适用于同机房主。真正进入后按 `player_status → 必要时 join_lan_game → ready=true → observe_player`；自检所用的隔离服务器不能接管另一个既有会话。

多个模型各自运行一个持续 MCP 进程，各占一个房间席位，可以共用同一安装包。分别调用 `join_lan_game(host="127.0.0.1", port=20595, nickname="Model-A")` 与 `nickname="Model-B"`。`player_name` 为兼容别名；两者同时提供必须相同。服务器自动隔离会话、模组和桥接目录，Windows 自动为各游戏进程准备独立资源 ZIP，无需手动复制多份 companion。当前真实共存验收为 Windows 两个 MCP 客户端，未推定更大规模或 Linux 对局已通过。

## 安装与调用技能

skill-kit 的 `skills/` 与 player-kit 中的技能内容相同，只需安装一份。将需要的技能目录分别复制到模型客户端支持的技能发现目录，保留完整目录结构；当前有三个在线策略技能和一个独立残局分析技能。已存在同名自定义技能时先比较，不直接覆盖。无技能发现机制的模型可直接读取对应 `SKILL.md`。

通过自然语言或技能名给出参数即可，例如：

```text
使用 $0ad-train-near-civic：市政中心ID=<本局ID>，template=<本局训练模板>，count=30。
使用 $0ad-build-resource-storehouse：在当前可见的资源区附近规划一座仓库，优先空闲建造者。
使用 $0ad-rally-idle：category=citizen，x=600，z=1100。
使用 $0ad-analyze-save：离线分析这两份 .0adsave，焦点玩家1，为每份生成新报告目录，并比较我的经济、布局和策略倾向。
```

训练默认限同一基地 200 世界单位内的最近 4 座可用生产者，参数可调整；`count` 是本次追加数量。集结类别为 `all/citizen/civilian/cavalry/champion`，分别为所有、公民兵、平民、骑兵、冠军。仓库技能会先比较现有存放点；已有合适仓库时可建议复用，用户明确要求额外新建则继续选址。

两个计划脚本可选：在相应技能目录执行 `python scripts/plan_training.py request.json` 或 `python scripts/plan_idle_rally.py request.json`。输入为 `{observation:本次公开observe_player完整结果, request:参数}`；输出仅是计划。模型通过同一已连接 MCP 会话复核、提交、观察结果，不另起控制服务，不把 accepted 当作完成。

残局技能的入口为 `python "<技能目录>/scripts/analyze_save.py" "<存档>.0adsave" --output "<新报告目录>" --player 1 --templates "<0.28.0公开模组目录或ZIP>"`。Python 3.11+ 即可运行，不需要安装服务器；Linux 显式提供实际 public 路径。输出为 `report.json`、`report.md`、`map.html`。报告标为 `offline-save-omniscient`，保留所有玩家数据；`--player` 只选择焦点，不恢复迷雾，也不能作为在线策略脚本的观察输入。
