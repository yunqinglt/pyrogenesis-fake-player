# 玩家计分板与命令

## 加入房间与玩家身份

```json
{"tool":"join_lan_game","arguments":{"host":"127.0.0.1","port":20595,"nickname":"Model-A"}}
```

`nickname` 是加入房间时显示的昵称，`player_name` 为保留的兼容别名。都省略时使用 `MCP Player`；两者都提供时必须完全相同。昵称限 1..32 字符，须包含非空白字符且不含控制字符；非法或冲突参数在启动游戏前拒绝。昵称不指定阵营／席位，等待房主分配并开始后，通过 `player_status` 和 `observe_player` 确认实际玩家 ID。

多个模型各用独立的持续 MCP 进程、游戏客户端和席位，可加入同一房主／UDP 端口；为它们使用不同昵称。桥接与临时模组按会话隔离，无需手工设置实例 ID。一个 MCP 进程仍只能控制一个玩家；共享同一服务的模型需要分配命令负责人。实际并存验收范围见 [验证记录](validation.md)。

## 己方计分板

`observe_player().scoreboard` 仅投影当前玩家的数据：

| 字段 | 含义 |
| --- | --- |
| `phase` | `village`／`town`／`city`；不明为 `null` |
| `population` | `count`、当前 `limit`、最大 `max` |
| `resources` | 食物、木材、石料、金属的现有数量 |
| `gatherers` | 按上述四种资源分类的当前采集者人数 |
| `idle_units` | 空闲可移动对象总数 `count`、其中 `Worker` 人数 `worker_count` |
| `territory_percent` | 当前玩家统计器返回的统治区域百分比；不可得为 `null` |
| `current_research` | 科技名、研究者 ID、进度、剩余毫秒和暂停状态 |
| `construction` | 己方地基 ID、模板、生命值比例表示的施工进度、建造者人数 |
| `busy_buildings` | 有生产任务的己方建筑及其最多 16 个队列条目 |

计分板不输出其他玩家经济、研究、生产队列或内部元数据。空闲人数按对象计数，人口按游戏人口消耗计数，两者不保证相等。建筑自身的 `properties.building` 提供研究／训练清单、生产状态、忠诚度、驻军与箭矢等细节，见 [properties](properties.md)。

## 单位任务

`order_units(entity_ids, orders, queued=false)` 选择 1..200 个己方可移动单位，提交 1..100 项任务。`queued=false` 替换原有任务，`true` 追加；同批第二项起总是排队。整批先验证后提交，若之后资源、目标、所有权、地形或可见性变化，实际执行仍可能失败。

```json
{"entity_ids":[101,102],"orders":[
  {"type":"build","template":"structures/ptol/house","x":1100,"z":730,"angle":0},
  {"type":"gather","target":201}
],"queued":false}
```

目标任务为 `attack`（伤害攻击）、`capture`（占领）、`gather`、`repair`（含地基）、`garrison`、`occupy_turret`、`heal`、`guard`、`collect_treasure`、`return_resource`、`trade`，使用 `target` 实体 ID。`patrol` 和 `attack_move` 使用世界 `x,z`；后者映射到 0.28 原生 `attack-walk`。`build` 使用建造者清单中的结构模板、地图内世界 x/z 和可选弧度 `angle`（±2π）。

目标必须为己方或当前可见实体；支持、炮位和贸易目标限定己方，宝物限定可见中立对象。未设置端点的新商人需对两个市场分别提交一个 `trade`，两次均 `queued=false`；不能把同批第二任务的排队理解成立即设置端点。已有端点可能影响原生重设逻辑，当前没有独立清除端点或双端原子设置接口，需观察任务和贸易货物验证路线。建造保留原生自动续作行为，完成后单位可能继续工作；`abandon_targets` 会停止并清空已排队任务。

姿态工具 `set_unit_stance` 允许 `violent`、`aggressive`、`defensive`、`standground`、`passive`、`skittish`。最后两项分别为遭攻击逃跑与见敌逃跑。命令使用原生逻辑，不创建阵型。

这些任务沿用原生 UnitAI，不关闭自动决策。模型应保留高层任务记录，目标和进展正常时等待；只有目标改变、危险或已确认停滞时覆盖。停止并非永久禁用索敌，`standground` 仍会攻击。`stop_units` 不解除守卫绑定；`abandon_targets` 先解除绑定再停止，守卫换岗时应先调用它。采集会自动交货，重复同目标采集可能截断队列；施工保留续作，排队建造也可能立即创建并付款地基。完整调用约定见 [半自动化协作规则](../MCP.md#半自动化协作规则)。

用户于 2026-11-07 补充的建造与采集规则：

- 工人脚下的合法地形允许直接建造。地基实体范围内的所有单位会先避让，建造者再尝试施工；没有额外副作用，但影响建造用时。农田及其他无实体建筑除外。
- `[正在建造并刚刚建造完成]` 且 `[单位旁边存在未完工地基]` 时，工人会接续协作建造，直到附近没有地基后，在最近建造完成的建筑前空闲；资源回收点另有下述自动采集行为。
- 工人建成资源回收点后会自动采集最近的对应资源：粮仓 → 食物资源；市政中心／仓库／其他资源回收建筑 → 树木／矿石。

详细条件与例外见 [玩家手册](../AGENT.md#建造与采集的单位-ai-行为)。这些是用户提供的游戏行为说明，本次未新增实机验证；完工后先观察实际任务，再决定是否恢复原工作。

## 研究与训练

先从已完成己方建筑的 `properties.building.research.technologies` 或 `pairs` 读取研究选项，从 `training.entities` 读取训练选项。清单中的选项可能仍有科技或资源前提。

用 `describe_options(entity_id, kind, ids)` 查询原生名称、说明、成本、效果与前提。`kind` 支持 `self/research/training/construction`；一次最多 16 项，只能查询当前己方对象及其提供的选项。结果保留同名科技的不同 ID、上下配对位置、组前提和完整性标记；成本范围与语言来源见 [MCP 手册](../MCP.md)。

```json
{"tool":"research_technology","arguments":{"building_id":301,"technology":"phase_town_generic"}}
```

```json
{"tool":"train_units","arguments":{"building_id":302,"template":"units/ptol/infantry_pikeman_b","count":2}}
```

研究工具先通过原生 `CheckTechnologyRequirements` 检查本玩家前提，再下达 `research`；不接受指定其他玩家。训练工具限制每次 1..100 个对象，沿用原生科技、资源、人口及单位数量限制。二者都重新检查建筑所有权、完成状态和所提供选项。`accepted=true` 只代表提交；通过计分板／生产队列确认开始，通过时代变化／新实体确认完成。重复研究请求会在要求检查中被拒绝，不会重复排队。

用户于 2026-11-07 补充：训练建筑周围挤满单位、找不到新单位放置位置时，即使训练计时完成也拒绝生成，生产队列会卡住。及时委派已出生单位远离建筑，腾出空间，让建筑继续尝试搜索可放置位置，再用新实体和队列变化确认恢复。某些体型较大的战车、大型攻城器械和船舶尤其需要留出空间；预设远离建筑的集结点有助于疏散新生单位，已出生的拥堵单位仍需另行调离。详见 [训练建筑拥堵与单位生成](../AGENT.md#训练建筑拥堵与单位生成)。本条为用户提供的游戏规则，本次未新增实机验证。

`cancel_production(building_id, queue_id)` 使用最新观察中 `production.queue[].id` 取消一项研究或整批训练，退款由游戏决定；不能用模板 ID 代替队列 ID。

`delete_own_units`、`delete_own_buildings` 分别限制 `Unit`／`Structure` 类别，执行原生 `delete-entities`；不能选择对手实体。取消地基的退款和受保护实体的拒绝由引擎处理。

## 折叠与驻军撤出

`set_unit_packing(entity_ids, packed, queued=false)` 使用原生折叠／展开命令；`packed=true` 折叠，`false` 展开。所选己方 Unit 必须有 Pack 组件、状态稳定且不同于目标；选择不依赖 `movable`，因此展开后的固定攻城器也能折叠。`cancel_unit_packing(entity_ids)` 取消正在进行的切换。每次切换后重新观察实体 ID。

`unload_garrison(holder_id, entity_ids=null)` 从己方容器卸出指定己方乘员，省略名单只选择全部己方乘员；不操作盟友。验证同时核对容器与乘员两侧驻军关系。`leave_turret(entity_ids)` 对应另一套原生命令，只允许可弹出的己方炮位乘员。二者均需观察最终位置和占用量确认。

用户于 2026-11-07 补充：带驻军的船舶距离岸边太远，或岸边被单位挤满时，会拒绝卸载单位。应让船舶靠岸或疏散岸上的单位、腾出登陆空间，再尝试卸载，并核对船舶驻军名单、乘员的驻军关系与上岸位置。详见 [船舶靠岸与驻军卸载](../AGENT.md#船舶靠岸与驻军卸载)。本条为用户提供的游戏规则，本次未新增实机验证。

## 集结点

`set_rally_point(entity_ids, orders, queued=false)` 为 1..200 个已完成且有集结点组件的己方建筑／容器设置默认任务。支持位置 `move/attack_move/patrol`（`x,z`），以及目标 `gather/repair/garrison/occupy_turret/attack/capture/trade/collect_treasure`（`target`）；支援与贸易目标限己方。每次 1..100 项，替换或追加后最多 100 项，完整验证后才提交。`clear_rally_point(entity_ids)` 清除整条队列。

通过己方 `properties.rally_point.points` 读取队列。新单位出生时采用生产建筑当时设置；已出生单位不受设置／清除影响。原生 UnitAI 按顺位检查执行多点命令，不能直接等同于按人数分配。执行能力、驻军容量与失效目标回退均由原生引擎处理，详见 [MCP 手册](../MCP.md)。不要为自动执行正常的新兵重复发同一命令。

用户于 2026-11-06 补充：单位入驻后，卸载只执行当前驻守建筑的集结点；该建筑没有集结点时，以无目标状态卸出，不续接原生产建筑的剩余集结点命令。驻守建筑被摧毁时，被卸出单位的 AI 任务处理等同手动卸载。预设撤出路线应设置驻守建筑自己的集结点；无目标仍保留姿态与自主反应。建筑摧毁情形本轮未另行实测。

建筑升级、自动续产开关、市场兑换／资源比例、城门和警报尚无专用接口；光环说明也不提供任意主动技能执行。

## 实测控制台

```powershell
.\.venv\Scripts\python.exe scripts\mcp_console.py --compact --report .runtime\live-development.jsonl
```

每行输入一项 `{"tool":"...","arguments":{...}}` 请求。`--compact` 缩短屏幕属性输出；`--report` 保留完整 MCP 请求、响应和往返毫秒数。输入 `quit` 只关闭该会话启动的客户端。
