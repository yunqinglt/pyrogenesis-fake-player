# MCP 客户端配置指南

先完成安装和自检，配置中只保留启动服务器的命令。示例路径是占位说明，须换为当前机器上的真实绝对路径；路径包含空格或中文也可使用，不要额外把引号字符嵌入 JSON 的 `command` 值。游戏连接端口通过 `join_lan_game` 提供，与 stdio MCP 配置无关。

Linux 的安装命令、通用 JSON 示例、系统 ENet、XDG 模组目录与桌面环境要求见 [LINUX.md](LINUX.md)。下列 Windows 示例使用 `.venv/Scripts/python.exe`；Linux 使用 `.venv/bin/python`，共享库可通过 `PYROGENESIS_ENET_LIBRARY` 配置。

## 通用 stdio JSON

适用于采用 `mcpServers` 配置格式的客户端；配置文件位置由客户端决定。其他客户端可将相同 command/args/env 映射到自己的 stdio 设置。

```json
{
  "mcpServers": {
    "pyrogenesis": {
      "command": "C:\\Games\\0ad-mcp-server\\.venv\\Scripts\\python.exe",
      "args": ["-m", "pyrogenesis_mcp.server"],
      "env": {
        "PYROGENESIS_EXE": "C:\\Games\\0 A.D.\\binaries\\system\\pyrogenesis.exe",
        "PYROGENESIS_ENET_DLL": "C:\\Games\\0 A.D.\\binaries\\system\\enet.dll"
      }
    }
  }
}
```

服务器通过已安装包和 companion 定位资源，不依赖客户端工作目录。支持 `cwd` 的客户端可明确设为 server 解压目录；不要假定所有通用 JSON 客户端都接受同一个 `cwd` 字段。

`PYROGENESIS_EXE` 省略时会搜索常用安装目录；非标准路径应显式配置。ENet 自动发现并不从任意显式游戏路径推导目录，非标准路径请同时配置 `PYROGENESIS_ENET_DLL`。可选 `PYROGENESIS_MODS_DIR` 指定用户模组根目录，正常安装通常无需设置；它必须对应游戏实际使用的目录，不能单独用它隔离模型实例。

## 多模型参战

为每个模型运行一个独立的持续 stdio 服务器进程；它们可以使用同一安装包和 Python 路径。客户端配置中的服务器标签只用于识别，必须确认模型实际连接到不同进程。共享一个服务连接的模型仍只拥有一个玩家席位。

各自调用 `join_lan_game` 时提供相同房主／UDP 端口和不同昵称：

```json
{"tool":"join_lan_game","arguments":{"host":"127.0.0.1","port":20595,"nickname":"Model-A"}}
{"tool":"join_lan_game","arguments":{"host":"127.0.0.1","port":20595,"nickname":"Model-B"}}
```

以上两行分别由两个服务执行。`nickname` 和兼容别名 `player_name` 二选一即可；都省略则为 `MCP Player`，同时提供必须完全相同。名字须为 1..32 字符、非纯空白且无控制字符。房主分配席位后，各自等待 `ready=true` 并读取实际玩家 ID。

无需配置实例 ID 或手动复制多份模组。服务器自动生成 session，为每场受控游戏创建私有 `mcp_player_<session>` 模组和 `user/moddata/mcp_player/<session>/` 桥接目录；正常退出只清理本会话的私有运行目录。Windows 另隔离各引擎使用的资源 ZIP；内部中继端口自动分配，同房间使用相同的 `20595` 端口不会因此争用。普通 GUI 设置和崩溃文件仍可能共享，进程隔离不提供恶意模型间的文件权限沙箱。真实并存验收范围见玩家资料包的 `docs/validation.md`。

## Codex TOML

将以下配置合并到所用 Codex 配置的 `config.toml`。不要覆盖已有配置；可在用户配置或受支持的项目配置中设置。TOML 单引号字符串保留 Windows 反斜杠字面值。

```toml
[mcp_servers.pyrogenesis]
command = 'C:\Games\0ad-mcp-server\.venv\Scripts\python.exe'
args = ['-m', 'pyrogenesis_mcp.server']
cwd = 'C:\Games\0ad-mcp-server'

[mcp_servers.pyrogenesis.env]
PYROGENESIS_EXE = 'C:\Games\0 A.D.\binaries\system\pyrogenesis.exe'
PYROGENESIS_ENET_DLL = 'C:\Games\0 A.D.\binaries\system\enet.dll'
```

配置后用 `codex mcp list` 检查注册；CLI 交互界面可用 `/mcp` 查看连接。注册成功不等于已加入游戏，仍需 `player_status` → 用户指定的 `join_lan_game` → 等待 ready → `observe_player`。

Codex 官方配置资料核对日期：2026-11-06。

- MCP：https://developers.openai.com/codex/mcp/ （当前重定向至 https://learn.chatgpt.com/docs/extend/mcp?surface=cli）
- Skills：https://developers.openai.com/codex/skills/ （当前重定向至 https://learn.chatgpt.com/docs/build-skills）

## 模型提示词与技能

server 和 player-kit 分别解压。将 player-kit 的 `ENTRYPOINT.md`、`AGENT.md` 与 `MCP.md` 交给玩家模型；MCP 配置本身不自动注入这些提示词。

Codex 当前官方技能发现目录包括项目的 `.agents/skills` 和用户的 `~/.agents/skills`。可将 player-kit 中需要的 `skills/<name>` 目录复制到所选发现目录，每个目录保留完整结构；不要覆盖同名自定义技能。旧客户端可能仍使用 `.codex/skills` 等目录，应按其实际能力配置。其他模型可直接读取 `SKILL.md`。三个在线策略技能通过自己的已连接 MCP 会话执行公开工具；残局分析技能独立运行，读取用户委派复盘的存档，不能将全知报告用于公平实战。

提示词目录中的 `AGENT.md` 是本项目的玩家手册文件名，不保证被客户端自动当作项目 `AGENTS.md` 发现；使用入口提示明确要求模型读取它。技能可以生成工具调用计划，但不会新增 MCP 工具，也不应私自另起服务器接管已有比赛。

Windows 首次可能建立约 3.6GB 的共享游戏资源模板；该模板用于复制，不由新代码直接启动。每个会话另在 `%LOCALAPPDATA%/pyrogenesis-mcp/instances/` 下建立私有运行目录，房主和玩家角色分别使用独立 ZIP，避免文件锁冲突；构建共享模板时使用进程间锁。并发引擎越多，临时资源占用越大，正常关闭时清理本会话私有运行目录，模板保留复用。它们不包含在发行包中，无需在配置阶段复制官方游戏。遇到加载或崩溃请记录具体错误，不反复关闭用户房主。
