# Pyrogenesis Fair Player MCP

适用于本机 **0 A.D. 0.28.0** 的普通玩家 MCP。Python 负责 ENet 中继、协议研究和 MCP 工具；官方 Pyrogenesis 客户端负责地图加载、确定性仿真和同步校验。模型通过伴随模组观察己方计分板、单位、建筑与正常视野内的地形及对象，并发送原生玩家命令。

当前实现使用官方引擎完成真实对局，未在 Python 中重新实现完整游戏仿真。协议研究和公平性边界见 [docs/protocol.md](docs/protocol.md)。

本项目原创代码和文档按 GNU GPL v3 或更高版本授权，完整条款见 [LICENSE](LICENSE)。项目还包含少量 0 A.D. v0.28.0 源码副本，位于 docs/source_snapshot/ 和 research/network/；这些文件保留上游版权和许可声明，许可清单见 [源码快照清单](docs/source_snapshot/manifest.json)。

## 项目结构

- src/：MCP 游戏客户端、ENet 中继和离线存档分析器。
- mod/：随游戏客户端加载的伴随模组及契约测试。
- portable/：服务器、玩家资料和技能包的可移植模板。
- skills/：可复用的玩家策略与存档分析技能。
- docs/、AGENT.md、MCP.md：架构、验证记录和操作手册。
- tests/、scripts/：自动化测试、检查和演示入口。
- research/：网络协议与引擎接口研究资料。

给其他模型试玩时，请同时提供 [AGENT.md](AGENT.md)（规则、操作习惯与策略起点）和 [MCP.md](MCP.md)（完整工具手册与能力限制）。

可移植交付分为 MCP 服务器包、模型玩家资料包与独立技能包：玩家资料包含游戏文档、提示词和同一组策略技能。安装、机器执行与客户端配置见 [可移植分包指南](docs/portable-distribution.md)，逐项验收及用法见 [验收清单](docs/portable-acceptance.md)。游戏引擎需另行安装；分包不依赖本工作区的绝对路径或虚拟环境。

三个策略技能分别用于 [就近批量训练](skills/0ad-train-near-civic/SKILL.md)、[资源区仓库选址](skills/0ad-build-resource-storehouse/SKILL.md) 和 [分类集结空闲单位](skills/0ad-rally-idle/SKILL.md)。技能使用现有公开 MCP 工具；训练和集结附带只生成调用计划的 Python 辅助脚本，不另起游戏会话。

另有独立的 [残局分析技能](skills/0ad-analyze-save/SKILL.md)，用于用户提供的存档复盘与多档策略比较。分发的 player-kit 和 skill-kit 均携带标准库解析器；无需安装 MCP，也不包含存档或官方游戏模板。

文档包含半自动化协作规则：模型负责目标与中断条件，原生单位 AI 负责执行；避免重复覆盖采集、交货、施工、交战和持续路线。

用户提供的 `.0adsave` 可用独立的 [离线存档分析器](docs/save-analysis.md) 生成结构化局势、中文报告和交互对象图，不依赖 MCP 或运行中的游戏。分析标为离线全知，不能用于公平对战的实时决策。

## 启动

需要 Python 3.11 或更高版本，并单独安装 0 A.D. 0.28.0 游戏客户端。首次设置时，在项目根目录创建环境并安装开发依赖：

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

MCP 服务使用标准输入输出：

```powershell
.\.venv\Scripts\python.exe -m pyrogenesis_mcp.server
```

在 MCP 客户端中，将启动程序设为本项目 `.venv\Scripts\python.exe` 的绝对路径，启动参数设为 `-m pyrogenesis_mcp.server`，工作目录设为本项目根目录。

默认自动发现已安装的游戏和 `enet.dll`。如安装在其他位置，可设置 `PYROGENESIS_EXE` 和 `PYROGENESIS_ENET_DLL`。伴随模组自动安装到游戏的用户模组目录。

## 工具

| 工具 | 行为 |
| --- | --- |
| `start_local_game(networked=true)` | 启动本地主机、Python ENet 中继和受控玩家，座位由主机分配；`false` 启动单机玩家 1 对沙盒 AI |
| `join_lan_game(host, port=20595, nickname=null, player_name=null)` | 经中继加入无密码 LAN 房间；建议给每个模型不同昵称，`player_name` 为兼容别名 |
| `player_status()` | 查看加载和连接状态，等待 `ready=true`；终局以 `outcome=won|defeated` 判断，`ready=false` 或断线本身不表示战败 |
| `observe_player()` | 返回己方实体 properties、资源、人口和实时计分板，并带 `outcome`／`gameOver`；`unknown` 或非终局状态不能当作胜负 |
| `describe_options(entity_id, kind="research", ids=null)` | 读取己方对象及其研究／训练／建造选项的原生名称、说明、成本、前提与效果 |
| `observe_map_region(x_min, z_min, x_max, z_max, cell_size=16)` | 按探索/视野过滤的区域地形、可见对象与静态历史快照 |
| `analyze_terrain_region(x_min, z_min, x_max, z_max, footprint_width, footprint_depth, cell_size=8)` | 对上述公开地形采样计算紧凑高度、坡度、可见性与建筑占地起伏摘要 |
| `move_units(entity_ids, x, z, queued=false)` | 向世界坐标发出普通移动命令 |
| `stop_units(entity_ids)` | 停止己方单位 |
| `order_units(entity_ids, orders, queued=false)` | 顺序下达建造、攻击、占领、采集、驻军、炮位、治疗、守卫、贸易、交回资源、拾宝、巡逻或沿途交战任务 |
| `set_rally_point(entity_ids, orders, queued=false)` | 设置／追加建筑或容器集结点，为新生／部分卸载单位安排默认任务 |
| `clear_rally_point(entity_ids)` | 清除集结点队列；已有单位继续原任务 |
| `abandon_targets(entity_ids)` | 解除守卫绑定，停止并清除己方单位的当前目标和排队任务 |
| `set_unit_stance(entity_ids, stance)` | 设置追击、原地防御或逃跑姿态 |
| `set_unit_packing(entity_ids, packed, queued=false)` | 显式折叠或展开己方攻城器 |
| `cancel_unit_packing(entity_ids)` | 取消正在进行的折叠／展开 |
| `unload_garrison(holder_id, entity_ids=null)` | 卸出容器内指定或全部己方驻军 |
| `leave_turret(entity_ids)` | 让可弹出的己方乘员离开炮位 |
| `train_units(building_id, template, count=1)` | 在己方已完成建筑排队训练 1..100 个可训练对象 |
| `research_technology(building_id, technology)` | 在己方已完成建筑排队研究科技，先验证研究前提 |
| `cancel_production(building_id, queue_id)` | 取消当前研究队列条目或整批训练 |
| `delete_own_units(entity_ids)` | 使用原生删除命令杀死己方单位 |
| `delete_own_buildings(entity_ids)` | 使用原生删除命令摧毁己方建筑或取消地基 |
| `close_game()` | 关闭当前 MCP 自己启动的进程和中继 |

一次 MCP 服务只控制一场游戏。隐藏对手信息、原始网络包、同步哈希、任意脚本和任意模拟命令均不作为 MCP 工具输出。引擎和桥接文件属于可信组件；此边界不限制另有本机文件、进程权限的代理。XMPP 大厅发现、认证及密码房间尚未实现。

计分板字段、任务格式、姿态和研究示例见 [玩家与命令](docs/player-actions.md)。命令确认表示已提交原生队列；建筑放置、研究、训练的最终结果需再次观察。当前不构建阵型。

共 24 个公开工具。规则查询直接读取运行中版本的公开模板并使用游戏本地化；同名配对科技保留不同 ID、上下分支、效果和组前提。结果标明规则完整性与成本范围；单位／建筑单模板成本不等于最终批量成本和训练时间。详见 [MCP.md](MCP.md)。

房间规则向所有玩家公开全图时，地图查询遵循该规则；己方经济接口仍只返回己方数据。房间规则未开启全图而玩家单独使用揭图功能时，桥接会禁用。加入手动创建的房间后，房主需要把 `MCP Player` 分配到玩家席位并开始游戏；观察者无法操作单位。

## 区域地图观察

例如 `observe_map_region(160, 360, 400, 520, 16)`，坐标为世界 x/z，矩形采用半开区间。每格返回中心采样点：

```json
{"x": 168, "z": 368, "in_bounds": true, "explored": true,
 "currently_visible": false,
 "terrain": {"height": 20.5, "water_level": 0, "last_seen": 123400}}
```

未知区域 `explored=false, terrain=null`；可见区域返回当前高度/水面；迷雾下只保留曾在同一采样坐标看见的地形。已探索但从未采样的位置也可能 `terrain=null`。`last_seen` 为游戏时间毫秒，不是现实时间。格子标记只代表中心点，不能推断整格都可见。

对象列表包含当前可见的己方/其他玩家单位、建筑、树、资源与宝藏；每个对象独立检查真实视野，不借用格子中心视野。移动单位不保留迷雾历史；静态对象使用 GUI 独立快照，雾中不会更新资源量、血量或所有权，也不会因隐藏销毁而消失。只有重新看到旧位置且对象列表完整时才清除过期快照。尚未查询过的区域不会自动获得历史对象。

`cell_size` 范围 4..256，最多 4096 个采样点和 2000 个对象；`truncated=true` 表示安全结果被截断。缓存上限为 65536 个地形点、20000 个静态对象；跨会话、切换玩家或游戏时间倒退时清空。仅保留最后一个请求区域，每 500ms 更新；请求别的区域会切换观察窗口。

当前提供结构化高度网格和对象，不是游戏截图；`analyze_terrain_region` 可在同一公开视野数据上计算紧凑的行优先高度、坡度、可见性和建筑占地起伏摘要。它只使用已观察采样，不能替代原生放置、碰撞或通行性检查。仍不提供材质纹理、可通行性网格、精确碰撞外形或 NumPy/SciPy 等高级数学渲染，也无需额外科学计算依赖。水面或资源最大值无法确定时为 `null`。

高级数学地形（例如通行性、碰撞和优化选址）与个人策略案例库的后续方案见 [地形与策略保存方案](docs/terrain-and-strategy-plan.md)；当前已实现的是受视野约束的 `analyze_terrain_region` 摘要，不会自动记录或训练策略案例。

只读扩展给原有 `GuiInterface` 增加固定查询，不注册新模拟组件、不改写同步状态；房主无需启用该模组。新增模拟脚本必须在客户端启动时加载；升级前已运行的客户端需要重启并重新加入，不能靠 GUI 热加载升级。

## 单位与建筑 properties

己方实体和当前可见单位／建筑新增版本化 `properties`：能力、采集与建造数据、资源背包、当前目标、攻击伤害／目标倍率／治疗／光环、特殊身份（军犬、钦差大臣、劳工象、冠军／皇家近卫、雇佣兵、英雄）、驻军和折叠状态。地图仅输出公开属性，订单、背包、建造清单及驻军名单通过己方观察取得。字段、单位和视野规则见 [properties 文档](docs/properties.md)。新模拟脚本需重启 MCP 客户端后加载。

## Windows 双实例资源

本机 Windows 安装包的 ZIP 读取会阻止第二个进程读取同一个资源包。本地联机模式与加入 LAN 房间的流程会在 `%LOCALAPPDATA%\pyrogenesis-mcp\host-runtime` 缓存独立运行目录：ZIP 资源使用独立副本，程序和 DLL 优先使用硬链接。首次约需额外 3.6 GB，后续复用；安装文件变化时生成新的缓存版本。游戏安装目录保持原样。缓存可在所有受控游戏关闭后删除并在下次启动时重建。

## 验证

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe scripts\check_mod_contracts.py
.\.venv\Scripts\python.exe scripts\demo_player.py
.\.venv\Scripts\python.exe scripts\demo_mcp.py
```

两个演示均会启动真实游戏、观察己方单位、移动并确认位置变化、发送停止指令，再关闭自己启动的游戏。前者还验证 ENet 中继双向数据包计数；后者通过公开 MCP stdio 接口完成整条流程。加 `--singleplayer` 可验证单机。报告写入 `.runtime/`。

浏览器契约覆盖玩家桥接、单位命令、研究和训练、模拟端地图过滤、GUI 迷雾历史、实体及建筑属性。

新增契约覆盖原生规则描述、折叠／取消、卸载、炮位进出、生产取消和高级任务；实际对局验证范围单独记录在 [验证记录](docs/validation.md)。
