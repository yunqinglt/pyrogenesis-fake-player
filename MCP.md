# 普通玩家 MCP 使用手册

适用：本项目当前 0 A.D. 0.28.0 接口，核对日期 2026-11-07。先阅读 [AGENT.md](AGENT.md) 中的公平游戏边界和决策习惯。本文可与该文件一起交给其他模型；不要求模型拥有源码或终端权限。

## 连接与调用

项目提供 24 个公开工具。实际可调用名称可能带客户端命名空间；参数采用下表的原始名称。MCP 客户端通常直接调用工具；本文的 JSON 包装用于项目控制台，每行一条：

```json
{"tool":"player_status","arguments":{}}
```

| 工具 | 参数与用途 |
| --- | --- |
| `start_local_game` | `networked=true` 创建本地联机测试；`false` 单机对沙盒 AI。已有房间应使用加入工具 |
| `join_lan_game` | `host`，`port=20595`，`nickname=null`，`player_name=null`；指定昵称加入无密码 LAN 房间，房主分配席位并开始 |
| `player_status` | 无参数；读取连接生命周期、`ready`、`state/reason`、己方 `outcome` 和 `gameOver` |
| `observe_player` | 无参数；己方实体、经济、人口、计分板、properties 与终局字段 |
| `describe_options` | `entity_id,kind="research",ids=null`；当前己方对象及其选项的原生名称、说明、成本与规则 |
| `observe_map_region` | `x_min,z_min,x_max,z_max,cell_size=16`；当前视野与静态历史信息 |
| `analyze_terrain_region` | `x_min,z_min,x_max,z_max,footprint_width,footprint_depth,cell_size=8`；紧凑高度、坡度与建筑占地起伏估计 |
| `move_units` | `entity_ids,x,z,queued=false`；普通移动，坐标为世界 x/z |
| `stop_units` | `entity_ids`；停止己方可移动单位 |
| `order_units` | `entity_ids,orders,queued=false`；建造、目标和位置任务，类型见下表 |
| `set_rally_point` | `entity_ids,orders,queued=false`；设置己方建筑／容器集结点任务队列 |
| `clear_rally_point` | `entity_ids`；清除集结点队列，已有单位继续当前任务 |
| `abandon_targets` | `entity_ids`；解除原生守卫绑定，停止并清除当前及排队任务 |
| `set_unit_stance` | `entity_ids,stance`；设置原生追击／逃跑姿态 |
| `set_unit_packing` | `entity_ids,packed,queued=false`；`true` 折叠，`false` 展开 |
| `cancel_unit_packing` | `entity_ids`；取消正在进行的折叠或展开 |
| `unload_garrison` | `holder_id,entity_ids=null`；卸出指定己方驻军，省略名单则卸出全部己方驻军 |
| `leave_turret` | `entity_ids`；让可弹出的己方炮位乘员离开炮位 |
| `train_units` | `building_id,template,count=1`；每次排队 1..100 个建筑所提供的对象 |
| `research_technology` | `building_id,technology`；选择建筑所提供的科技 ID |
| `cancel_production` | `building_id,queue_id`；取消当前队列中的一个研究项目或整批训练 |
| `delete_own_units` | `entity_ids`；永久删除己方 Unit |
| `delete_own_buildings` | `entity_ids`；拆除己方 Structure 或取消地基；退款由游戏决定 |
| `close_game` | 无参数；关闭本 MCP 会话创建的客户端和中继，结束对局时使用 |

连接是异步的；等待 `ready=true` 后再发出游戏动作。对局结束后按 `state`／`outcome` 判断胜负；`ready=false` 本身只表示当前不可操作，不能推断为失败。无 XMPP 大厅认证、密码房间或任意原生命令入口。不要为了读取一次状态另起服务器接管已有会话。

推荐用 `nickname` 标识模型玩家；`player_name` 保留为兼容别名。两项都省略或为 `null` 时使用 `MCP Player`；两项同时提供时必须完全相同，否则在启动游戏前拒绝。昵称限 1..32 字符，必须包含非空白字符且不能含控制字符；不会按昵称选择或切换玩家席位。

```json
{"tool":"join_lan_game","arguments":{"host":"127.0.0.1","port":20595,"nickname":"Model-A"}}
```

## 多模型与多实例

每个模型使用独立的持续 MCP 进程，各自启动一个游戏客户端并占据一个玩家席位。不同实例可以加入同一房主、同一游戏 UDP 端口，例如分别使用 `Model-A` 和 `Model-B`；服务内部中继端口各自分配。一个 MCP 进程仍只控制一个玩家，多个模型共享该进程不会产生多个席位。席位由房主分配，确认就绪后读取实际玩家 ID。

服务器自动为会话创建独立桥接目录与临时伴随模组，Windows 另为受控引擎准备独立资源文件；无需给工具增加实例 ID，也不要用 `PYROGENESIS_MODS_DIR` 充当实例选择器。`close_game` 只结束当前服务创建的进程并清理其私有运行目录，不关闭其他模型或用户手动房主。若独立房主主动结束比赛，其他客户端仍会正常断线。

隔离范围是工具会话、游戏命令和所属进程，不是防止恶意本机进程的安全沙箱；部分 GUI 配置和崩溃文件仍可能共享。版本升级应在对局边界重启各受控客户端。并存测试实际覆盖的平台、连接及动作以 [验证记录](docs/validation.md) 为准，不能由隔离设计推断所有环境都已通过。

## 己方观察

`observe_player` 的顶层包含 `player`、`time`（游戏毫秒）、`mapSize`、`entities`、`resources`、`population`、`populationLimit`、`scoreboard`、`outcome`（`active|won|defeated|unknown`）和 `gameOver`（若玩家名单不完整则为 `null`）。只有 `won` 或 `defeated` 表示己方终局；`active`／`unknown` 不是结果。它只暴露己方状态；不返回对手身份或结果明细。

| `scoreboard` 字段 | 含义 |
| --- | --- |
| `phase` | `village`、`town`、`city`，未知为 `null` |
| `population` | 当前占用 `count`、现有限制 `limit`、房间上限 `max`；队列可能预留人口，不能直接当作已出生人数 |
| `resources` | `food,wood,stone,metal` 当前库存 |
| `gatherers` | 当前按四种资源分类的采集人数 |
| `idle_units` | 空闲可移动对象 `count`，以及其中 `Worker` 数 `worker_count` |
| `territory_percent` | 己方统治区域百分比，未知为 `null` |
| `current_research` | 研究科技 ID、研究者、进度、剩余毫秒及暂停状态 |
| `construction` | 地基 ID、模板、施工进度和建造者人数 |
| `busy_buildings` | 有生产任务的建筑及队列 |

实体顶层包含 `id,owner,movable,template,position,hitpoints,maxHitpoints,properties`。驻军单位可能没有世界位置。不要仅按 `movable` 判断所有能力；读取具体组件。`null` 表示不适用或不可得，空数组表示已知为空。

| `properties` 组 | 常用信息 |
| --- | --- |
| `identity` | 文明、原生名称与说明、类别表达式所用的 `classes`、等级、特殊身份 |
| `capabilities` | 移动、建造、维修、采集、农业、攻击、占领、治疗、驻军、折叠等标记 |
| `construction` | 建造模板 `templates` 与当前速率 `rate` |
| `gathering` | 资源子类采集速率、携带容量 |
| `storage` / `inventory` | 可存放的资源类型；当前背包和贸易货物 |
| `current_target` / `activity` | 当前任务、可知目标 ID／目的地、空闲状态和姿态 |
| `rally_point` | 己方建筑／容器集结点 `points` 队列及 `truncated`；无组件时为 `null` |
| `combat` | 攻击模式、伤害、射程、周期、倍率、禁攻类别、抗性、治疗与光环 |
| `garrison` | 容器容量、允许类别、占用槽位和己方驻军 ID |
| `garrisoned_in` | 单位所在的 `holder_id` 和所占槽位 |
| `packing` | `packed`、`changing`、`progress`、`state`；无 Pack 组件时为 `null` |
| `building` | 建筑专有信息，非 Structure 为 `null` |

`packing.state` 是 `packed`、`unpacked`、`packing`、`unpacking` 四者之一。能力标记只表示机制存在，不保证当前可以执行。光环说明不等于通用主动技能工具。

`building` 的重点字段：

- `research.technologies`、`research.pairs[{top,bottom}]`、`research.tech_cost_multiplier`；用 `describe_options` 查询这些选项的名称、效果、成本、研究时间和前提。
- `training.entities`：可选择的训练模板；部分选项仍有科技、资源、人口或数量前提。
- `production.queue`：单位／科技模板、批次数量、进度、剩余毫秒及暂停状态，最多 16 个条目。
- `building_ai`：默认／最大箭矢、驻军箭矢倍率和类别、当前箭矢数。
- `resource`：资源类型、是否无限、上限、最大／当前采集人数；无限资源的 `amount,max` 为 `null`。
- `owner,hitpoints,max_hitpoints,loyalty,placement,foundation,repair`：所有者、生命、忠诚度、领土／距离规则及施工／维修数据。

完整字段定义见 [docs/properties.md](docs/properties.md)。

## 原生名称与规则查询

`describe_options(entity_id, kind="research", ids=null)` 仅接受本次观察到的己方对象。`kind` 为 `self`（对象当前模板）、`research`、`training` 或 `construction`；后三者限该对象当前提供的选项，不能查询任意模板或指定其他玩家。数据来自正在运行的游戏模板与本地化，不依赖工作区中的旧源码。

```json
{"tool":"describe_options","arguments":{"entity_id":301,"kind":"research","ids":["civil_service_01","civil_service_02"]}}
```

此例只在对象 301 实际提供这两个科技时有效。省略 `ids` 返回前 16 项；`total_available` 和 `truncated` 表示是否还有选项。需要更多时，从对象当前清单中挑选其余 ID，每次最多 16 个。

每项包括 `id`、`name.generic/specific`、`description`、`tooltip`、`requirements_tooltip`、`cost/base_cost`、`cost_scope/cost_notes`、`requirements`、`requirements_met`、`affects`、`modifications` 及基本模板数据 `stats`。`requirements_complete`、`modifications_complete` 为假时，投影不完整，不能据此推断没有其他规则。`requirements_met` 也不是资源、人口、队列与位置的最终可执行性保证。

配对科技保留各自 ID、`pair.group/position/other` 及配对组前提；`position` 为 `top` 或 `bottom`。即使显示名称相同，也能比较实际修改路径与 `multiply/add/replace`，再按 ID 选择。例如训练时间 ×0.5 与生命值 ×1.5 是不同效果，不按名称匹配。

说明文字可能与原生机器前提不一致。已核对的 0.28 汉文明 `civil_service_02` 提示写都市阶段，而结构化前提为 `phase_town`；可研究性应以机器前提、原生检查和实际队列结果判断，并记录文本矛盾。

研究成本包含生产者科技成本倍率。单位／建筑成本是当前玩家修正后的单模板参考值，不能当成最终批量价格或训练耗时；读取 `cost_notes`，再用原生队列验证。`cost.time` 的单位为秒；计分板队列的 `time_remaining_ms` 和建筑 properties 队列的 `time_remaining` 均为毫秒。结果附带 `source`、`engine_version` 与 `locale`；文本随游戏语言，缺少译文可回退源文，不保证强制中文。

## 地图、视野与时间

```json
{"tool":"observe_map_region","arguments":{"x_min":1000,"z_min":700,"x_max":1128,"z_max":828,"cell_size":16}}
```

区域为半开矩形，`cell_size` 为 4..256，最多 4096 个中心采样点和 2000 个对象。`cells[]` 返回坐标、是否在地图内、探索／可见标记、地面高度与水面，以及地形最后可见时间。未知地形为 `null`；已探索但没在可见时采样过的位置也可能没有地形数据。

`objects[]` 按对象自身的真实视野过滤，包含单位、建筑、资源、树木和宝物。可见单位／建筑提供公开 properties；地图接口不提供私有订单、背包、生产队列或驻军名单。迷雾只保留曾经查询到的静态对象历史，移动单位不保留。`last_seen` 是游戏毫秒。

`truncated=true` 时结果不完整。当前只持续更新最后一次请求的区域，桥接约每 500ms 刷新。不要按现实等待时间推算游戏进度；暂停、速度和断线均会影响它。查询不提供地图纹理、精确碰撞或可通行性网格。

`analyze_terrain_region` 示例：

```json
{"tool":"analyze_terrain_region","arguments":{"x_min":240,"z_min":240,"x_max":368,"z_max":368,"footprint_width":16,"footprint_depth":12,"cell_size":8}}
```

该工具只分析玩家当前可见或曾在可见时采样的地形，不枚举区域对象。返回的 `grid` 除 `x_centers[columns]`、`z_centers[rows]` 外，其余数组均按 `row * columns + column` 展平；用 `visibility_legend` 解码 `visibility`。未知高度、水面、时间戳和无法可靠计算的坡度保留 `null`，不会跨越未知格插值。`slope_degrees` 是采样分辨率下的局部估值。

`footprint` 包含标量 `width`／`depth` 和与网格索引对齐的 `coverage`、`region_coverage`、`complete`、`height_range`、`mean_height` 数组。它们表达以该格中心为候选中心时的已知采样比例、落在本次分析区域内的占地比例、采样完整性、高差与均值。格网分辨率、边缘裁切、未知地形都会影响估值；即使 `complete=true` 且高差很小，也只说明已知采样下的地形适宜性。工具不测试领土、对象碰撞、单位阻挡、模板特殊限制或实际建造许可。最终以正常 `build` 命令返回、地基出现及施工结果为准；勿因数学分数重试非法位置。

## 任务与姿态

`order_units` 每次选择 1..200 个己方可移动单位，提交 1..100 个任务。整批先验证再提交。`queued=false` 替换旧任务；同批第二个任务起按顺序排队。`queued=true` 追加到旧任务后。

| 任务 `type` | 其他字段 |
| --- | --- |
| `build` | `template,x,z`，可选 `angle`（弧度）；模板必须在建造者当前清单中 |
| `repair` | `target`；己方受损建筑或地基，适合多人协助施工 |
| `gather` | `target`；当前可知资源对象，包括已完成农田 |
| `garrison` | `target`；己方建筑或可容纳人员的移动对象 |
| `attack` | `target`；伤害攻击，不自动改成占领 |
| `capture` | `target`；占领，需要实际占领能力和适当目标 |
| `heal` | `target`；治疗己方单位，需要实际治疗能力 |
| `guard` | `target`；守卫己方对象 |
| `collect_treasure` | `target`；收集当前可见的中立宝物 |
| `return_resource` | `target`；向己方存放点交回携带的资源 |
| `trade` | `target`；设置己方市场／码头贸易端点，建立路线需考虑已有端点状态 |
| `occupy_turret` | `target`；进入己方炮位容器，沿用原生位置与类别限制 |
| `patrol` | `x,z`；巡逻至世界坐标 |
| `attack_move` | `x,z`；沿途交战移动，使用原生 `attack-walk` |

例如，以下 ID 和坐标仅作格式示范，执行前必须换成本次观察到的值：

```json
{"tool":"order_units","arguments":{"entity_ids":[101,102],"orders":[{"type":"build","template":"structures/ptol/house","x":1100,"z":730,"angle":0},{"type":"gather","target":201}],"queued":false}}
```

修理、驻军、炮位、治疗、守卫、资源交回和贸易目标当前限定己方。其他实体目标也必须是己方或当前可见实体；宝物限定可见中立对象。原生资源、外交、放置和寻路限制仍然生效。建造命令会启用原生自动续作，需要观察完工后的实际任务。

尚未设置端点的新商人，可对两个市场分别调用 `order_units`，每次只有一个 `trade` 且 `queued=false`。放在同一 `orders` 中会让第二项排队。已有端点时原生逻辑可能保留或清除旧路线，不能保证简单 A→B 能重设成功；当前没有独立清除端点或原子设置双端的接口，需观察实际贸易货物和任务后确认。

| `stance` | 行为 |
| --- | --- |
| `violent` | 可追击到视野外，受击可打断强制任务；不意味着 MCP 获得隐藏信息 |
| `aggressive` | 在自身视野范围内追击 |
| `defensive` | 在守位附近有限追击后返回，并非原地不动 |
| `standground` | 原地攻击，不追击 |
| `passive` | 受到攻击时逃跑 |
| `skittish` | 看见敌人时逃跑 |

本版本不构建阵型。

姿态约束自主反应，不等于禁止显式移动／攻击命令。特别是 `violent` 可能为了反击打断原任务；执行脆弱的施工、撤退或精确移动前，应结合目标选择适合的姿态并观察实际反应。

## 集结点：新训练单位的初始目标

`set_rally_point(entity_ids, orders, queued=false)` 选择 1..200 个已完成且具有集结点组件的己方对象，不要求对象可移动。每次提交 1..100 项；`queued=false` 替换旧集结点，`true` 追加，追加后最多 100 项。目标队列先完整验证再发送原生 `set-rallypoint` 命令。

位置任务为 `move/attack_move/patrol`，字段为 `x,z`；目标任务为 `gather/repair/garrison/occupy_turret/attack/capture/trade/collect_treasure`，字段为 `target`。修复／协助地基、驻军、炮位和贸易目标限己方；采集限当前可见 Gaia／己方资源，宝物限可见 Gaia。攻击与占领按 `allowCapture=false/true` 区分。这里不提供 `heal/guard/return_resource` 或新建地基任务；不要把 `order_units` 的全部任务类型直接搬来。

```json
{"tool":"set_rally_point","arguments":{"entity_ids":[301],"orders":[{"type":"move","x":1100,"z":730},{"type":"gather","target":201}],"queued":false}}
```

在 `observe_player` 的 `properties.rally_point.points[]` 读取 `order,entity_id,position`。有组件但未设置时 `points=[]`；没有组件或旧桥接尚未加载时为 `null`。最多显示 100 项，超出以 `truncated=true` 标记，此时可替换或清除但不能追加。隐藏／消失目标 ID 隐去；坐标遵循原生最后已知位置，不追踪迷雾目标。地图公开属性始终不包含集结点队列。

单位在**出生时**采用生产建筑当时的集结点，而非训练排队时锁定。设置／清除不改派已出生单位。`clear_rally_point(entity_ids)` 清除整条队列，不会停止先前已出发的单位。驻军后卸载采用驻守建筑的集结点，规则见下。

集结点由原生 UnitAI 执行，仍受新单位能力、目标有效性、路径、驻军容量和外交约束。不能保证每种训练单位都能采集、修复、占领或驻军。多项是顺序任务，不是按人数或轮流分配；持续采集、巡逻或成功驻军可能阻止后续任务立即执行。目标消失时，原生逻辑可将采集／宝物改成在旧位置附近寻找，将攻击改成沿途交战，其他任务退回普通移动。移动资源默认使用当前位置附近采集，与原生右键语义一致。应观察新实体任务、背包或容器名单确认结果。

以下为用户于 2026-11-06 补充的 UnitAI 规则，属于游戏行为说明：

1. 单位 AI 按顺位检查并执行集结点命令。
2. 单位执行驻军并入驻后，卸载时只执行当前驻守建筑的集结点；该建筑没有集结点，则以无目标状态卸出，不续接原生产建筑的剩余集结点命令。
3. 驻守建筑被摧毁时，被卸出单位的 AI 任务处理与手动卸载相同。

例如市政中心设置“驻军塔 A → 驻军塔 B”，已进入 A 的单位卸出后采用 A 自己的集结点；A 没有集结点时无目标，不会因市政中心的原队列继续前往 B。要安排撤出后的路线，应设置驻守建筑自己的集结点，并在卸载后观察结果。“无目标”不关闭姿态或原生自主反应。建筑摧毁后的规则由用户提供，本轮未另外进行摧毁实测。

2026-11-06 波斯新房间已实测：市政中心依次设置两座空箭塔为驻军目标，两塔各 5 槽；一次训练 10 名弓箭公民兵后，首塔容纳 5 人，其余 5 人自动转向第二塔，最终各驻军 5 人，全程未手动分队。这是首塔满员后继续下一任务的实例，不是均分承诺；目标顺序、剩余容量和任务能否结束仍需观察。详见 [实战记录](docs/validation.md#新房间城镇化与两塔集结点实测2026-11-06)。

## 半自动化协作规则

模型负责选择任务、分配单位和设定优先级；单位内部 AI 负责原生寻路、接近目标、交还背包、交战反应及任务续作。这里是调用约定，没有新增“关闭单位 AI”或“手动模式”开关。以下规则用于防止重复覆盖：

1. **同一单位只保留一个高层任务负责人。** 多模型协作应先按单位分工；不要让经济与战斗代理同时向同一组对象发命令。记录当前目标、最后一次工具调用、观察游戏时间、预期完成证据和中断条件；这是模型侧记录，不是新增工具字段。
2. **先判断是否仍在执行目标，再决定下令。** 采集者回存放点、建造者接近地基、军队追击或逃跑，都可能是原任务的正常子步骤。若目标和进展符合计划，继续观察，不因坐标、背包或状态短暂变化而反复重发。
3. **只在目标改变、已确认停滞或触发中断条件时覆盖。** `queued=false` 替换当前任务，会打断寻路、工作或交战；对移动／攻击／建造不断重发可能让它们反复重新开始。普通单位危险撤退或换岗可直接提交新的替换任务；守卫单位要先用 `abandon_targets` 解除守卫绑定，再下新任务，避免到达后又自行返回守卫。没有后续任务而只想清空计划时也用该工具。
4. **排队只表达真正的先后关系。** `queued=true` 追加；同一 `orders` 第二项起也会排队。采集、守卫、贸易和巡逻可能长期持续，排在后面的任务未必很快开始。立即交货或改贸易端点应独立提交替换任务，不能排在持续任务后期待马上执行。原生重复采集同一目标可能截断后续队列；排队建造仍可能立即创建地基并扣费，只把施工排队。不要把 `queued=true` 当成无副作用的未来计划存储。
5. **姿态决定自主反应，停止不等于永久待命。** `stop_units` 不解除守卫绑定，单位可能恢复守卫；`abandon_targets` 先发 `remove-guard` 再停止。两者都不会关闭 UnitAI 或改变姿态。`standground` 不追击，但仍可在射程内自主攻击；`passive` 遭攻击会逃，`skittish` 见敌会逃。没有一个现有姿态等于完全禁止行动。进攻、驻军或施工前检查姿态是否符合计划，勿反复抵消合理的追击／避险反应。
6. **提交后等待可验证的状态变化。** `accepted=true` 不代表结果已发生，紧接着的观察也可能仍是上一轮桥接数据。比较游戏 `time`、任务、位置、队列和属性；未确认前不重复训练或建造。超时／拒绝先观察；实体换 ID 或队列完成后改用新身份。

| 原生自动行为 | 模型应怎样配合 |
| --- | --- |
| `gather` 携带满后交货，再继续采集；资源耗尽时可能另选附近资源 | 依据任务和库存判断进展；只在资源分配或采集范围需要变化时改派 |
| 工人脚下的合法地形允许直接建造；地基实体范围内的所有单位先避让，建造者再尝试施工；农田等无实体建筑除外 | 将避让用时计入施工过程，不因单位先走开就重复提交建造 |
| `build` 启用 `autorepair/autocontinue`；正在建造且刚完工的工人，旁边有未完工地基时会继续建造，直到附近没有地基后在最近完工建筑前空闲；资源回收点另有自动采集行为 | 按“刚完工且旁边有地基”的条件理解续建；完工后复核实际职责，需要收工时明确清空或换岗 |
| 工人建成资源回收点后自动采集最近的对应资源：粮仓 → 食物；市政中心／仓库／其他资源回收建筑 → 树木／矿石 | 核对实际采集目标和经济分工，需要调整时再改派 |
| `return_resource` 要求执行时仍有可交资源，交货后不保证恢复原采集任务 | 失败时核对背包是否已自然交空；需要保留测试货物可先停止再观察；交货后明确恢复采集 |
| `attack/attack_move` 与姿态共同控制交战；失去视野不等于原生命令立即停止 | 追击不授予模型隐藏信息；按已知威胁、撤退线和实际状态决定是否覆盖 |
| 攻城器的移动／攻击可自动折叠或展开 | 优先明确一次目标形态，等待切换或主动取消；不要交替重发移动与展开造成往复 |
| `guard/patrol/trade` 持续执行，贸易还保留原生端点状态 | 路线正常时让 AI 继续运行；变更目的前确认旧任务与端点影响 |
| 驻军后可无世界位置，生产中会预留人口，队列可能等待条件 | 先查容器、队列和人口，不把不可移动或尚未出生误判为命令丢失 |
| 训练建筑周围拥堵、找不到新单位放置位置时，即使训练计时完成也拒绝生成，生产队列卡住 | 调离已出生的单位，腾出空间后让建筑继续尝试搜索放置位置；大型战车、攻城器械和船舶尤其需要留出空间 |
| 带驻军的船舶距离岸边太远，或岸边被单位挤满时，拒绝卸载单位 | 让船舶靠岸或疏散岸上单位，腾出登陆空间后再尝试卸载，并观察实际结果 |
| 新生单位采用生产建筑集结点；驻军卸出（含建筑摧毁时的 AI 处理）采用驻守建筑集结点，无集结点则无目标 | 分别记录生产建筑与驻守建筑的默认队列；默认任务正常时不重复改派，卸出后不期待续接原生产建筑队列 |

上表的地基避让、完工续建和资源回收点自动采集由用户于 2026-11-07 补充，属于游戏规则说明；避让没有额外副作用，但会影响建造用时。完整条件、例外及尚未明确的搜索半径与行为先后顺序见 [玩家手册](AGENT.md#建造与采集的单位-ai-行为)，本次未新增实机验证。

例如，工人的高层任务是“耕种农田 A”时，`Gather` 与 `GATHER.RETURNINGRESOURCE` 都可能符合目标；看到工人离田交货无需立即补发 `gather`。若改为支援地基 B，则发一次替换 `repair`，观察完工后再明确安排回田。若遭袭，先执行撤退／驻军；安全后重新观察并恢复工作，不重放含旧实体 ID 的整串命令。

## 生产、配对研究与结果确认

```json
{"tool":"train_units","arguments":{"building_id":301,"template":"units/ptol/support_civilian","count":5}}
```

```json
{"tool":"research_technology","arguments":{"building_id":302,"technology":"phase_city_generic"}}
```

仅在已完成、当前己方所有、提供该选项的生产建筑提交。研究先调用原生前提校验，再提交原生队列；训练也受引擎资源、科技、数量和人口限制。同名科技按唯一 ID 区分，配对选项选择 `top` 或 `bottom` 对应 ID，互斥由引擎处理。

用户于 2026-11-07 补充：训练建筑周围挤满单位、没有可放置新单位的位置时，即使训练计时完成也会拒绝生成，生产队列会卡住。及时调离已出生的单位，腾出空间，让建筑继续尝试搜索放置位置，再观察新实体和队列变化确认恢复；体型较大的战车、攻城器械和船舶尤其需要注意。可提前设置远离建筑的集结点，已出生的拥堵单位仍需另行调离。完整说明见 [玩家手册](AGENT.md#训练建筑拥堵与单位生成)；这是用户提供的游戏规则，本次未新增实机验证。

按以下证据确认结果：

1. 命令返回 `accepted=true`：仅确认提交。
2. 地基／队列／当前研究出现：确认任务已开始或排队。
3. 新实体、建筑完工、阶段变化、属性变化：确认对应结果。

队列清空本身不足以证明成功，也可能是取消或生产者丢失。建造放置可能在提交后失败；形态切换、地基完工可能产生新 ID。遇到拒绝或超时先观察，再决定是否重试，防止重复训练或建造。

## 折叠、卸载与取消

`set_unit_packing` 要求所选己方 Unit 都有折叠组件、没有处于切换中，且当前状态与目标相反。展开后的攻城器可能 `movable=false`，仍可按 Unit 类别折叠。观察 `packing.state` 确认过程与完成，并在形态切换后重新获取实体 ID。`cancel_unit_packing` 仅在切换中有效。

`unload_garrison` 要求容器和乘员均为当前己方对象；指定名单时还会核对容器名单与乘员的 `garrisoned_in.holder_id`。省略名单只卸出己方乘员，不操作盟友。卸载位置、弹出限制和容量等由引擎判断。卸出后只采用驻守建筑的集结点；没有集结点时无目标，不恢复原生产建筑队列，详见上方集结点规则。炮位与普通驻军使用不同原生命令：进入用 `occupy_turret` 任务，离开用 `leave_turret`，固定不可弹出的乘员会被拒绝。

用户于 2026-11-07 补充：带驻军的船舶距离岸边太远，或岸边被单位挤满时，会拒绝卸载单位。让船舶靠岸，或疏散岸上的单位、腾出登陆空间，再尝试卸载；通过船舶驻军名单、乘员的驻军关系与上岸位置确认结果。详见 [船舶靠岸与驻军卸载](AGENT.md#船舶靠岸与驻军卸载)。这是用户提供的游戏规则，本次未新增实机验证。

`cancel_production` 的 `queue_id` 来自本次 `building.production.queue[].id`，不是科技 ID 或单位模板。取消训练会删除该整个批次；退款由游戏决定。队列可能在观察后完成或变化，遇到拒绝先重新观察。

## 本版本的操作边界

- 已提供显式折叠／展开及取消、普通驻军卸载、炮位进出、生产取消，以及治疗、守卫、巡逻、沿途交战、宝物、资源交回和己方端点贸易。
- 光环可读，但没有任意主动技能执行入口；建筑形态升级、自动续产开关、市场买卖／贸易资源比例、城门与警报等尚无专用接口。
- 没有建造位置预检、精确通行网格或完整战术 AI；不构建阵型。
- 不公开其他玩家经济、研究、隐藏单位、任意脚本或原始网络包。

因此，“属性可读”与“动作可调用”需要分别确认。玩家可以继续完成工具覆盖的目标，同时报告剩余限制。

此前已实测托勒密连发床弩的移动／攻击自动切换流程；本轮新增显式操作，其真实游戏验证范围见 [docs/validation.md](docs/validation.md)。不要把契约测试通过当作所有兵种和文明均已实战覆盖。

更高级的路线／通行性估计和个人策略案例保存方案见 [docs/terrain-and-strategy-plan.md](docs/terrain-and-strategy-plan.md)；当前地形摘要采用 Python 标准库，不增加 NumPy/SciPy 运行依赖，也没有自动案例记录系统。

另有独立于 MCP 的 [离线存档分析器](docs/save-analysis.md)，可解析用户另行授权提供的存档并输出 JSON、中文报告与对象图；它不属于公开 MCP 观察工具。产物为 `offline-save-omniscient`，不得在公平对战中作为玩家视野使用。`--player` 只指定报告焦点，不还原迷雾。未来人与 AI 案例按 [analysis_rules.json](analysis_rules.json) 核对三种视野、45 分钟停战和 `conquest_units`（不计建筑与动物），保留原档的实际差异。

## 策略技能与可移植交付

可按任务加载 [就近批量训练](skills/0ad-train-near-civic/SKILL.md)、[资源区仓库选址](skills/0ad-build-resource-storehouse/SKILL.md) 或 [分类集结空闲单位](skills/0ad-rally-idle/SKILL.md)。这些技能组合现有 24 个公开工具，不新增服务器工具。训练／集结的 Python 辅助脚本只接受公开 `observe_player` 结果并输出调用计划，不启动 MCP 服务或游戏，也不直接执行命令。实际调用仍由当前模型在当前会话完成，并通过后续观察确认结果。

服务器、游戏文档、提示词与技能的分包方式和安装／配置流程见 [可移植分包指南](docs/portable-distribution.md)。模型玩家资料包包括本文件与 `AGENT.md`，可交给其他模型阅读；其中离线复盘资料继续受上述公平游戏边界约束。

## 给使用终端的操作者

常规模型试玩只需要配置好的 MCP 客户端。需要在此工作区配置服务时：程序为项目 `.venv\Scripts\python.exe` 的绝对路径，参数为 `-m pyrogenesis_mcp.server`，工作目录为项目根目录。安装与 Windows 双实例资源说明见 [README.md](README.md)。

控制台提供相同受限 MCP 工具，每行输入上面的 JSON；退出 `quit` 会关闭它创建的受控客户端，持续对局时保持控制台运行：

```powershell
.\.venv\Scripts\python.exe scripts\mcp_console.py --compact --report .runtime\model-play.jsonl
```

`--compact` 只缩短屏幕输出，不能把省略字段理解成实际不存在。`--report` 保存完整允许的 MCP 请求／响应及 `elapsed_ms`，适合复核自己的动作。报告属于本机产物，不能把旧报告中的状态当成当前对局。
