# 0 A.D. 普通玩家 MCP：可移植服务器

本包可解压到任意目录，安装后不依赖原开发工作区。它包含 Python MCP 服务器与伴随模组，不包含 Python 解释器、第三方依赖 wheel 或官方游戏；初次安装需要访问 Python 包索引。游戏目标版本为 **0 A.D. 0.28.0**。Windows x64 已检查本机游戏文件；Linux x64（Arch Linux／WSL2）已验证安装、MCP 协议、Bash 启动和 ENet 回环通信，尚未验证 Linux 实际对局。Python 要求 3.11 或更新，位数须与 ENet 库一致。Python 依赖范围为 `mcp>=1.20,<2`；安装脚本使用 `constraints-tested.txt` 固定已测依赖组合（MCP 1.30.0，Windows／Python 3.13.3 与 Linux／Python 3.14.9）。其他环境仍需在目标机器自检。

先读 [RUNBOOK.md](RUNBOOK.md)，也可交给执行代理读取 [RUNBOOK.json](RUNBOOK.json)。Linux 安装、Bash 启动和路径说明见 [LINUX.md](LINUX.md)，使用 `bash install.sh --python /usr/bin/python3` 创建本机环境。模型玩家的提示词、规则和策略技能在单独的 player-kit 包中；它们不由 MCP 自动加载。

游戏内观察及存档恢复仍有 [已知问题](KNOWN_ISSUES.md)。服务器提供会话隔离、独立临时模组及 Linux 原生库配置／自检路径支持，游戏动作仍使用原生规则。

```powershell
python .\verify_bundle.py
.\install.ps1 -PythonExe 'C:\Path\To\Python\python.exe'
.\.venv\Scripts\python.exe .\check_portable.py --check-game
```

以上操作不会加入或启动游戏。安装完成后按 [CONFIGURATION.md](CONFIGURATION.md) 配置客户端。优先直接启动包内 `.venv\Scripts\python.exe -m pyrogenesis_mcp.server`。`run-server.ps1` 是可选的 stdio 包装入口，不打印欢迎词。不要把安装命令或自检命令配置成 MCP 启动命令。

移动整个目录到另一台机器、变更 Python 安装位置或修改目录位置后，应在新位置重新运行安装；虚拟环境本身不是可搬迁运行时。交付的是可重新安装的源码包。若要完全离线部署，需要另外为目标平台准备兼容的 Python、游戏和完整依赖 wheel 仓库。

服务一次只控制一场比赛。关闭本服务会关闭它创建的受控游戏进程；保留会话时不要退出 MCP 客户端。房主手动启动的实例不在本服务的关闭列表中。已运行客户端的伴随模拟脚本不能靠 GUI 热加载更新，应在合适的对局边界重新启动。

多个模型各自启动独立的 MCP 进程，即可分别拥有客户端与玩家席位；安装包可以共用，每个模型的服务进程和连接必须独立。各模型用 `join_lan_game(host, port, nickname="Model-A")`／`nickname="Model-B"` 加入同一个房主及 UDP 端口。旧 `player_name` 参数继续有效；两项同时提供时须相同，都省略时使用 `MCP Player`。昵称限 1..32 字符，非纯空白且无控制字符。

会话启动时自动部署 `mcp_player_<session>` 私有模组，并把桥接文件放入用户模组目录的 `user/moddata/mcp_player/<session>/`；无需手工安装项目模组或选择实例 ID。Windows 在共享资源模板之外，为每个会话的房主／玩家角色分别复制 ZIP，正常退出清理本会话私有运行目录。普通日志使用原生独立日志名，部分 GUI 设置和崩溃文件仍可能共享；这不是操作系统安全沙箱。安装自检不会启动多玩家比赛，多实例实际验收结果见玩家资料包的 `docs/validation.md`。

本包未附项目的独立离线存档分析器、原生游戏源码、游戏资源、崩溃转储、截图、存档、个人 MCP 配置或密钥。SHA-256 清单用于比对完整性，未进行数字签名，不能独立证明发布者身份。
