# 可移植分包与机器执行

逐项验收及实际使用步骤见 [验收与用法](portable-acceptance.md)。

Linux 安装与启动入口为服务器包内的 `install.sh` 和 `run-server.sh`，详细步骤见 [Linux 指南](../portable/server/LINUX.md)。构建会规范 Bash 的 LF 换行，并在 ZIP 中记录执行权限；默认仍可直接用 `bash` 运行。Linux 自检支持安装前缀内的发行版资源布局、显式 public 路径和系统 ENet 共享库。

server、player-kit 与 skill-kit 三包分别交付。server 仅包含普通玩家 MCP 与伴随模组、安装／启动／自检脚本和配置指南；player-kit 包含玩家提示词、规则文档、三个在线策略技能和一个离线残局分析技能；skill-kit 提供同一组四个技能的独立副本，便于单独安装。残局技能包含标准库解析器和默认案例规则，不依赖 MCP；原始存档与官方模板不随包分发。三包都不依赖原开发目录，均附逐文件 SHA-256 清单。它们不包含 Python 解释器、官方游戏或依赖 wheel 仓库，因此属于可重新安装的源码分发包，并非完全离线运行时。默认安装采用 `constraints-tested.txt` 的已测运行依赖组合；服务器版本支持范围仍保留在精简 `pyproject.toml` 中。

构建使用显式白名单，排除 `.venv`、`.runtime`、开发源码快照、存档、截图、崩溃转储及个人 MCP 配置。复制说明文本中的开发机用户路径替换为通用示例，不修改原文档。服务器的 companion 放入 Python package data，普通 `pip install` 或标准 wheel 安装后仍可找到；不依赖 editable 工作区布局。

在项目根目录：

```powershell
.\.venv\Scripts\python.exe scripts\build_portable.py --check
.\.venv\Scripts\python.exe scripts\build_portable.py --output dist\portable-20261106
```

输出目录必须尚不存在。构建前校验所有必需源文件、四个技能入口和本地 Markdown 链接；ZIP 条目排序及时间固定，相同输入会产生相同压缩包。输出包括三个 ZIP、外部 `SHA256SUMS.txt` 及 `release.json`。包内 `BUNDLE-MANIFEST.json` 可用随包 `verify_bundle.py` 校验；SHA-256 清单没有数字签名。

server 解压后，先看 [服务器入口源稿](../portable/server/README.md)、[执行步骤](../portable/server/RUNBOOK.md)、[结构化执行步骤](../portable/server/RUNBOOK.json) 和 [MCP 配置指南](../portable/server/CONFIGURATION.md)。安装脚本在解压目录创建 `.venv`，普通安装服务器后运行 stdio 自检。自检只初始化协议、列举 24 个工具（含 `analyze_terrain_region`）和查询空闲状态，不启动或加入游戏。`--check-game` 另检查本机游戏／ENet 文件、原生库加载、Windows PE 架构和可读的公开版本元数据；Linux 同时报告图形环境变量是否存在，不影响手动房主。

玩家包入口源稿为 [ENTRYPOINT.md](../portable/player-kit/ENTRYPOINT.md)。MCP 配置不会自动向模型加载玩家手册或技能，需按客户端方式提供。`AGENT.md` 是本项目手册名，不能假定它会自动按 `AGENTS.md` 发现。Codex 配置与技能目录以指南中 2026-11-06 核对的官方资料为准；其他客户端可映射通用 stdio 参数或直接读取技能。

移动源码包后可重新安装；移动已创建的 `.venv` 不保证有效。用户手动房主应保留，已连接的服务应持续运行以保留对局。自检服务器只验证空闲协议，不接管已有比赛。JS 运行错误或存档恢复崩溃不因分包而自动解决。

多个模型参与同一房间时，分别运行独立的持续 MCP 进程，各自启动客户端、获得房主分配的席位；安装包可共用。各自调用 `join_lan_game(host, port, nickname="Model-A")` 等，房主和游戏 UDP 端口可以相同，昵称应区分模型。旧 `player_name` 别名保留；两项同时提供必须相同，都省略时使用 `MCP Player`。单个服务仍只控制一个玩家。

会话隔离由服务器自动管理：用户模组根下 `mcp_player_<session>` 为私有模组，桥接目录为 `user/moddata/mcp_player/<session>/`；Windows 通过进程间锁保护共享模板，再为会话的 host/player 角色分别复制独立 ZIP，避免资源读锁争用。新代码不直接启动共享模板；正常关闭清理本会话私有运行目录。无需用户手工选择实例 ID 或改 `PYROGENESIS_MODS_DIR`。部分 GUI 设置、崩溃文件仍可能共享，不能把这种隔离视为本机恶意模型间的安全沙箱。并存实测覆盖范围与未完成项以 [验证记录](validation.md) 为准。

2026-11-06 的隔离验证（当时版本）：将 server ZIP 解压到含空格和中文的新临时目录，在全新虚拟环境中按约束正常构建 wheel 并安装；当时通过的是 23 工具。2026-11-07 本轮重新构建后应以 24 工具自检为准。已验证安装包从独立 `site-packages` 导入，独立离线分析器未被带入；原生文件检查确认 Python 3.13.3、游戏与 ENet 都为 Windows x64，公开模组版本 0.28.0，ENet 1.3.18。此验证没有加入或启动游戏，不替代真实对局验证。

同日补充 Linux 验证（当时版本）：Arch Linux／WSL2 x64、Python 3.14.9，从独立 ZIP 在中文与空格路径全新安装，直接 Python 与 Bash 包装入口都完成真实 stdio 握手／23 工具／空闲状态，ENet 1.3.18 加载和 UDP 回环／中继通过。当前源码新增地形工具后，重新分发包必须以 24 工具自检为准。随后 Windows 两个 MCP 同局的独立观察、操作、退出和同名重进也已通过；最新回归计数与实际边界见 [验收页](portable-acceptance.md) 和 [实测记录](validation.md)。Linux 图形游戏和实际联机尚未验收，发行清单单独记录测试范围。
