# 执行代理运行说明

目标：在已安装 Python 和 0 A.D. 0.28.0 的机器上安装普通玩家 MCP，先完成隔离自检，再由用户提供房主和玩家任务。结构化等价说明见 [RUNBOOK.json](RUNBOOK.json)。以下命令为 Windows x64；Linux 按 [LINUX.md](LINUX.md) 执行，机器代理先应用 JSON 中的 Linux 平台覆盖项。

1. 将 server ZIP 解压到新的本地目录。先用 `python verify_bundle.py` 校验随包清单。不要在现有游戏目录上覆盖文件。
2. 选择 Python 3.11+ 解释器。在 PowerShell 运行 `& '<bundle>\install.ps1' -PythonExe '<python.exe>'`。安装脚本在包内创建 `.venv` 并使用 `constraints-tested.txt` 普通安装本包，不使用原项目的 editable 安装路径。安装是独立步骤，允许产生普通终端输出；MCP 运行 stdout 则只允许协议消息。
3. 执行 `& '<bundle>\.venv\Scripts\python.exe' '<bundle>\check_portable.py'`。它验证 companion 包数据、stdio 初始化、24 个工具（含 `analyze_terrain_region`）和空闲状态；不会调用启动／加入／关闭游戏工具。自检生成的是另一隔离服务器，不接管已有控制会话。
4. 按 [CONFIGURATION.md](CONFIGURATION.md) 设置当前机器的 `PYROGENESIS_EXE` 和 `PYROGENESIS_ENET_DLL`。再执行自检加 `--check-game`。它只读取文件并加载 ENet 库核对架构，不创建网络主机。运行中的 Windows 房主可能锁定 `public.zip`，此时报告 `game_version_verified=false` 并要求人工确认版本；不要为读取版本强停房主。
5. 将配置片段合并到用户选择的 MCP 客户端。使用当前机器的绝对路径；不要把原开发者路径、环境变量字面字符串或 `<bundle>` 占位符作为实际路径。不要自行覆盖已有服务器配置。
6. 将 player-kit 的 `ENTRYPOINT.md`、`AGENT.md`、`MCP.md` 提供给玩家模型。技能按客户端支持的发现目录另行安装。MCP 的工具说明不能自动代替这些文件。
7. 真正开始玩家任务时，先调用 `player_status`。已有受控连接就复用；若未连接且用户已给房主地址，用 `join_lan_game` 并通过 `nickname` 指定本模型昵称。等待房主分配席位并开始，轮询至 `ready=true`，再 `observe_player`。读取返回的实际 player ID、文明、时间与实体，禁止沿用示例 ID。
8. 以公开 MCP 工具观察和动作；每次提交后用新游戏时间及实体／队列确认结果。不要读取原始桥接文件、敌方存档或内存来取得动态局势。

若 `ready=false`，先读取 `state/reason`。只有 `outcome=won` 或 `outcome=defeated` 才是己方终局；断线、加载中或 `outcome=unknown` 不足以判定战败。

连接协议示例（这是 MCP 工具调用内容，不是直接写入 stdio 的 JSON-RPC 报文）：

```json
{"tool":"player_status","arguments":{}}
{"tool":"join_lan_game","arguments":{"host":"127.0.0.1","port":20595,"nickname":"Model-A"}}
{"tool":"player_status","arguments":{}}
{"tool":"observe_player","arguments":{}}
```

`127.0.0.1:20595` 只是同机示例。端口是游戏 UDP 端口；MCP 使用 stdio，不监听一个所谓的 HTTP MCP 端口。密码房与 XMPP 大厅尚未实现。停止连接或遇到 `ready=false` 时，先检查状态和是否仍在加载，不重复训练／建造。

`player_name` 仍可作为昵称别名；两项同时提供必须完全相同，都省略时使用 `MCP Player`。昵称为 1..32 字符，非纯空白且无控制字符。多个模型分别使用独立的持续 MCP 进程和不同昵称，加入同一房主／UDP 端口，等待房主分配各自席位。共享一个 MCP 进程不会产生多个玩家。

服务器自动隔离每场受控游戏的桥接目录与临时模组；Windows 还为各会话、各引擎角色隔离 ZIP 资源文件，无需手工填写实例 ID 或修改模组目录。正常关闭只清理本会话的私有运行目录；普通 GUI 设置与崩溃文件仍可能共享，不构成操作系统安全沙箱。并存实测范围以玩家资料包 `docs/validation.md` 为准，单实例安装自检不能代替多玩家对局验收。

正常退出必须符合当前玩家任务：不要为了完成自检或手册示例关闭正在试玩的会话。需要结束时可调用当前服务的 `close_game`；它会结束本服务创建的受控游戏。保留对局时保持服务器进程和 MCP 客户端会话存活。

故障判断：找不到游戏时检查可执行文件绝对路径；ENet 加载失败时检查是否同一游戏安装、位数和依赖；MCP 初始化失败时检查命令是否直达包内 Python、依赖是否已安装、stdout 是否被安装日志或 shell 欢迎词污染。JS 加载错误、OOS 或原生崩溃应保留独立日志并说明实际版本；本包不宣称已修复所有加载或存档恢复异常。
