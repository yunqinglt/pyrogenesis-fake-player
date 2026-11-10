# Linux 安装、启动与验收

目标为 Linux 上的 0 A.D. 0.28.0、Python 3.11+ 和 ENet 1.3 共享库。脚本不使用 sudo、不安装系统软件、不启动或加入游戏。首次安装需要联网访问 Python 包索引。`constraints-tested.txt` 已验证 Windows/Python 3.13.3 与 Linux/Python 3.14.9，Windows 专属约束已加平台条件；Linux 的实测范围以本页验证记录为准。

先通过发行版的软件管理方式准备 Python、venv/pip、0 A.D. 及系统 ENet；部分发行版将 venv 单独分包。游戏版本必须核对为 0.28.0，不能仅凭包名判断兼容。Python 和 ENet 必须使用同一架构。安装脚本不捆绑这些依赖。

## 安装与文件检查

将 server ZIP 解压到当前用户可写的新目录，以下假设为 `/home/player/0ad portable/server`。不要复制 Windows 的 `.venv`；在 Linux 重新安装。命令均支持路径中的空格与中文。

```bash
cd '/home/player/0ad portable/server'
python3 verify_bundle.py
bash install.sh --python /usr/bin/python3
.venv/bin/python check_portable.py
```

`--python` 可省略，默认使用 PATH 中的 `python3`。`bash install.sh --help` 查看用法。安装在本包内创建 `.venv`，普通安装服务器和 companion 数据，随后完成隔离 MCP stdio 自检。已有不完整的 `.venv` 或指向其他目录的符号链接会被拒绝；脚本不自动删除环境。移动安装位置或换机器后，应重新解压到新目录并安装。

原生文件检查示例：以下游戏、ENet 和 public 路径须换成本机实际文件。

```bash
export PYROGENESIS_EXE=/usr/bin/pyrogenesis
export PYROGENESIS_ENET_LIBRARY=/usr/lib/libenet.so.7
export PYROGENESIS_PUBLIC_MOD=/usr/share/0ad/data/mods/public
.venv/bin/python check_portable.py --check-game
```

游戏会先按 `PYROGENESIS_EXE` 查找，否则搜索 PATH 中的 `pyrogenesis`、`0ad`。部分发行版安装到 `/usr/games`，应给出绝对路径；优先选择实际引擎可执行文件。ENet 默认用系统动态库查找机制；显式配置 `PYROGENESIS_ENET_LIBRARY` 时可避免选择错误的库。它优先于兼容变量 `PYROGENESIS_ENET_DLL` 和 `ENET_DLL`，Windows 旧配置继续有效。

版本检查在所选程序的安装前缀下尝试 `data/mods/public`、`share/0ad/data/mods/public`、`share/0ad/mods/public` 和 `share/games/0ad[/data]/mods/public`。非标准布局可用 `PYROGENESIS_PUBLIC_MOD` 指定 public 目录、`public.zip` 或 `mod.json`；此变量只用于自检，不改变游戏加载资源的位置。源码编译版应由自身编译配置定位资源。Flatpak/Snap/AppImage 的沙箱、启动封装与数据目录未做适配验证，不能直接套用系统安装路径。

期望结果：`ok=true`、`stdio.tool_count=24`、`stdio.idle_status.running=false`；原生检查还应有 `native_files.game_version_verified=true` 和 `enet_linked_version=1.3.*`。找不到版本资料会明确返回未验证，已读到不兼容版本则失败。加载 ENet 时会由系统动态链接器检查 Python 能否加载该库。此检查不启动游戏，也不验证渲染、联网或对局动作。

## MCP 客户端配置

在采用 `mcpServers` 格式的客户端中合并以下设置。使用实际绝对路径；客户端若采用其他格式，映射相同 command/args/env 即可。支持继承当前桌面会话环境的客户端通常无需另外填写显示变量。

```json
{
  "mcpServers": {
    "pyrogenesis": {
      "command": "/home/player/0ad portable/server/.venv/bin/python",
      "args": ["-m", "pyrogenesis_mcp.server"],
      "env": {
        "PYROGENESIS_EXE": "/usr/bin/pyrogenesis",
        "PYROGENESIS_ENET_LIBRARY": "/usr/lib/libenet.so.7"
      }
    }
  }
}
```

可选 Bash 入口是 `bash '/home/player/0ad portable/server/run-server.sh'`。脚本用 `exec` 交接服务器进程，不输出欢迎词，stdout 仅用于 MCP。若解压器保留 ZIP 内的执行权限，也可直接执行 `./run-server.sh`；未保留权限时使用 `bash` 无需 chmod。不要将安装或自检放进 MCP 启动命令。

游戏仍会启动图形客户端。必须运行在能显示并持续更新游戏的桌面会话中；远程 SSH、服务进程或容器需要自行准备可用的显示环境。`DISPLAY`/`WAYLAND_DISPLAY` 等变量仅存在并不能证明显示服务可用。WSL 安装、自检与 Windows 房间连接是不同的验收项；跨系统连接时 `127.0.0.1` 是否可用取决于网络配置，房主地址以实际可达地址为准。

Linux 用户模组默认写入 `~/.local/share/0ad/mods`，设置 `XDG_DATA_HOME` 后为其下的 `0ad/mods`；设置时使用绝对路径，并让 MCP 与其启动的游戏继承相同值。通常不设置 `PYROGENESIS_MODS_DIR`：它只覆盖 MCP 的写入位置，任意改目录不保证游戏能从那里加载模组。

多个模型各自运行一个独立的持续 MCP 进程，由各自进程启动客户端并占据玩家席位；可以共用本包安装和同一游戏。加入同一房间时使用相同 `host`／`port`，分别指定 `nickname="Model-A"`、`nickname="Model-B"`。`player_name` 是兼容别名，两项同时提供必须相同；昵称最多 32 字符，非纯空白且无控制字符，都省略时为 `MCP Player`。

同一 Linux 用户无需手工配置实例 ID 或为每个模型设置 `PYROGENESIS_MODS_DIR`。服务为每场受控游戏自动创建 `mods/mcp_player_<session>` 私有模组和 `mods/user/moddata/mcp_player/<session>/` 桥接目录，正常关闭只清理自身私有运行目录。Linux 可共享只读游戏安装，内部中继端口各自分配。普通 GUI 配置与崩溃文件仍可能共享；这是命令、观察和所属进程隔离，不是恶意进程间的权限沙箱。多实例实际验证的平台和覆盖项以玩家资料包 `docs/validation.md` 为准，不能从此前的安装自检推导 Linux 同局已通过。

安装自检不会覆盖客户端配置，也不会关闭用户手动创建的房主。正式游戏仍先读玩家文档、调用 `player_status`，再按用户任务正常加入。停止 MCP 服务器会关闭它创建的受控游戏，保留比赛时保持服务器和客户端会话运行。

## 验证记录

2026-11-06，在 Arch Linux／WSL2 x64 中使用 Python 3.14.9，将服务器 ZIP 解压到含中文与空格的临时目录，真实执行 `install.sh`：正常构建 wheel 并安装至独立 site-packages，companion 7 个关键文件、MCP 1.30.0 初始化、23 工具、空闲状态和 `pip check` 通过。另由 `run-server.sh` 从不同工作目录完成真实握手与状态查询；没有打包或导入独立存档分析器。Bash 语法检查与 ShellCheck 0.11.0 通过，ENet 1.3.18 加载及原生 UDP 回环／中继测试通过。该测试使用临时解包的 Linux Python 和 ENet，通过 `LD_LIBRARY_PATH` 定位其依赖；不要求部署用户采用这种临时布局。

Linux 真实图形游戏启动、加入 Windows 房间和实际单位操作尚未验证，不能由协议或回环通过推导。已知游戏桥接／存档恢复问题见 [KNOWN_ISSUES.md](KNOWN_ISSUES.md)。
