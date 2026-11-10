# 实体 properties v1

`observe_player().entities[]` 中的己方实体，以及 `observe_map_region().objects[]` 中的单位／建筑对象，增加 `properties`。这是游戏当前组件的结构化投影；能力标记表示拥有相应机制，不保证当前资源、距离、地形、科技、驻军容量或折叠状态允许立刻执行。旧版桥接返回的实体可以暂时没有该字段。

## 字段

| 字段 | 内容 | 己方观察 | 地图观察 |
| --- | --- | --- | --- |
| `version` | 当前为整数 `1` | 有 | 有 |
| `identity` | `civ`、完整类别 `classes`、等级 `rank`、`special_roles`，以及可选原生 `name.generic/specific`、`tooltip`、`translation` | 有 | 有 |
| `capabilities` | 移动、建造、修理、采集、农业、贸易、治疗、攻击、占领、进入驻军／炮位、折叠、守卫、巡逻、收集宝物、资源存放 | 有 | 有 |
| `gathering` | 各资源子类采集速率 `rates`、各资源携带容量 `capacities` | 有采集组件时 | 有采集组件时 |
| `construction` | 建造清单 `templates`、当前建造速率 `rate` | 有建造组件时 | `null` |
| `storage` | 可接受资源 `accepts`，例如移动存放点劳工象 | 有存放组件时 | 有存放组件时 |
| `inventory` | 已携带资源 `resources`，贸易货物 `trade_goods` | 有 | `null` |
| `current_target` | 当前第一条命令类型 `order`、可知实体 `entity_id`、移动命令目的地 `position` | 当前有命令时 | `null` |
| `rally_point` | 建筑／容器默认任务队列 `points[{order,entity_id,position}]` 及 `truncated`，最多 100 项 | 有集结点组件时；未设置为空队列 | `null` |
| `activity` | 状态 `state`、空闲 `idle`、姿态 `stance` | 有 UnitAI 时 | `null` |
| `combat` | 攻击／占领能力、分攻击模式数据、抗性、治疗、光环 | 有 | 有 |
| `garrison` | 容量、允许类别、占用槽位、己方驻军对象 ID | 有容器时 | 仅容量／允许类别，其他为 `null` |
| `garrisoned_in` | 当前可知容器 `holder_id` 和所占槽位 `slot_size` | 可驻军时 | `null` |
| `packing` | 折叠标记、切换中标记、进度和四态枚举 | 有折叠组件时 | 有折叠组件时 |
| `building` | 建筑标记、研究／训练选项、己方生产队列、箭矢、资源、忠诚度、放置限制、所有者、生命值、建造与维修进度 | 结构建筑时 | 结构建筑时；队列和施工／维修动态数据为 `null` |

不适用的组件组为 `null`，空列表表示已知为空。地图上的己方对象同样使用公开属性；背包、建造清单、订单、姿态与驻军名单从己方观察取得。

`identity.name` 和 `tooltip` 来自当前实体模板的源语言标签，`translation="source-language"`；它们未经过 GUI 本地化。隐藏对象在读取模板前即被拒绝，迷雾静态对象沿用历史名称。需要按游戏语言读取详细规则时，用 `describe_options` 查询己方对象或其提供的选项；该工具的翻译来源为 `game-locale-or-source-fallback`。旧版属性允许没有这些新增字段。

## 建筑

`building` 仅对 `Structure` 类对象提供；非建筑为 `null`。其字段如下：

| 字段 | 含义 |
| --- | --- |
| `is_building` | 恒为 `true`，用于区分此对象组 |
| `research` | `technologies` 为可研究科技模板；`pairs` 保留成对科技的 `top`／`bottom`；`tech_cost_multiplier` 为当前科技成本修正 |
| `training` | `entities` 为可训练单位模板 |
| `production` | 己方私有生产队列：批次 ID、单位／科技模板、数量、进度、剩余毫秒、暂停状态及自动队列标记；地图观察为 `null` |
| `building_ai` | 默认／最大箭矢、驻军箭矢倍率与类别、当前箭矢数 |
| `resource` | 建筑自身提供的资源类型、数量上限和最大采集人数；无限资源（如农田）将 `amount` 与 `max` 表示为 `null` |
| `loyalty` | 按玩家索引排列的 `current` 忠诚点数与 `max` |
| `placement` | 模板给出的地形类型、允许领土类别、放置类别与相对指定类别建筑的距离范围 |
| `owner` | 当前所有者玩家 ID；中立所有者为 `0`，未知时为 `null` |
| `hitpoints`、`max_hitpoints` | 当前／最大生命值 |
| `foundation`、`repair` | 仅己方私有视图提供施工／维修人数与估算剩余秒数；地图观察为 `null` |

所有组按实体当前组件和模板投影，不依靠建筑名称猜测功能。因此码头／瞭望塔允许的领土类型、农田是否无限、驻军建筑的箭矢等都以实际模板／组件数据为准。`placement` 描述规则而非当前位置的放置合法性；它不会计算地图地形、遮挡、资源、领土变化或距离外的其他限制。`training`／`research` 表示可选列表，不代表资源、人口、前置科技或当前队列状态允许立刻开始。

建筑的 `garrison` 仍使用通用驻军组表示容量与允许类别；地图观察不会暴露占用槽位或驻军名单。可见建筑快照进入迷雾后会冻结上次观察到的公开建筑属性，但移除生产队列、建造／维修进度及采集人数，避免在地图记忆中保留私有动态状态。

## 能力、背包和当前目标

`capabilities` 的布尔键为 `move`、`build`、`repair`、`gather`、`farm`、`trade`、`heal`、`attack`、`capture`、`garrison`、`occupy_turret`、`pack`、`guard`、`patrol`、`collect_treasure`、`resource_dropsite`。其中 `garrison` 表示单位可以进入驻军容器，容纳其他单位的能力由 `garrison` 对象描述。`farm` 来自 `food.grain` 正采集速率，不由单位名称判断。

背包对应 0 A.D. 的实际资源机制，未引入装备或道具系统：

```json
{
  "resources": [{"type": "wood", "amount": 7, "capacity": 10}],
  "trade_goods": null
}
```

空背包为 `resources: []`；容量仍可从 `gathering.capacities` 读取。贸易货物用 `type`、`trader_gain`、`market1_gain`、`market2_gain` 描述，不输出市场内部实体、所有者或完整贸易路线。采集速率是当前组件给出的每秒基础采集量，实际产出还受目标和多人作业等机制影响。

`current_target` 仅提供当前命令，不复制完整订单队列、工作队列、自动索敌结果或目标的隐藏位置。目标是己方或当前可见实体时才返回其 ID；否则 `entity_id: null`。`position` 只来自普通位置命令的已下达目的地，例如 `Walk`、`WalkAndFight`、`Patrol`；不会追踪雾中目标。

## 战斗与特殊能力

`combat.attacks` 按 `Melee`、`Ranged`、`Capture` 等游戏模式分组，包含：

- `damage`：各伤害类型的每次攻击数值；`capture`：占领点数。
- `min_range`、`max_range`：世界单位距离；`prepare_ms`、`repeat_ms`：毫秒。
- `bonuses[]`：名称、目标类别表达式 `classes`、目标文明 `civs`、当前倍率 `multiplier`。例如长矛兵的 `Cavalry` 倍率，读取游戏实际数据，不给所有标枪兵附加同一种反骑兵加成。
- `restricted_classes`、`preferred_classes`：禁攻类别与优先目标；保留 `+`、`!` 等类别表达式，不扁平化为简单名称。
- `splash`：范围攻击形状、半径、友伤、伤害、占领和倍率；无范围攻击为 `null`。
- `status_effects[]`：中毒、灼烧等周期伤害的效果名 `name`、持续时间 `duration_ms`、周期 `interval_ms`、叠加规则 `stackability`、每次伤害 `damage` 与占领量 `capture`。例如毒箭的周期毒伤不会被混入直接穿刺伤害。

`combat.can_attack` 表示具有常规伤害攻击模式，`combat.can_capture` 单独表示占领能力；`Capture` 和动物屠宰 `Slaughter` 不会使普通攻击标记变为真。抗性在 `resistance` 中按伤害类型及占领类型保留游戏数值。`healing` 包含每次治疗量、范围、周期及可治疗／不可治疗类别。因此祭司可以是 `heal=true`、`attack=false`。

`resistance.status_effects` 按状态效果名保留持续时间倍率 `duration_multiplier` 和阻止概率 `block_chance`（0..1），包括机械单位／船舶的毒免疫。持续伤害来自游戏已修正的攻击数据，不输出隐藏的受害者名单或内部计时器。

`auras[]` 保留光环 ID、显示名、说明和作用范围。全局或未提供范围的光环为 `range: null`。这是光环说明，不是受影响对象名单或通用可执行技能接口。攻击值和倍率使用当前游戏数值修正；本接口不合成缺乏上下文的单一“战斗力分数”。

## 特殊对象和身份

| `special_roles` | 识别依据与含义 |
| --- | --- |
| `war_dog` | 布立吞军犬，保留实际攻击和可驻军等能力 |
| `minister` | 汉文明 `Minister` 类钦差大臣，能力及光环从当前组件取得 |
| `worker_elephant` | 孔雀文明劳工象，具有建造和资源存放组件；没有采集组件时 `gather=false` |
| `champion` | 游戏 `Champion` 类，包括托勒密皇家近卫等精锐单位 |
| `mercenary` | 游戏 `Mercenary` 类雇佣兵 |
| `hero` | 游戏 `Hero` 类英雄 |

标签可以并存，并与骑兵、步兵、攻城器等 `classes` 组合。当前原版没有独立 `Royal` 类；“皇家近卫”是部分 `Champion` 单位的名称，不能把所有冠军单位都视为皇家军队。英雄的光环、雇佣兵是否能建造／采集等仍按组件输出；身份标签不隐含某种固定数值或技能。

## 驻军与折叠

攻城锤、攻城塔和船舶的 `garrison` 返回真实容量、允许类别、`occupied_slots` 和 `occupants`。占用量按游戏槽位计算，不等于对象数量；名单仅含己方对象，不展开或读取盟友的隐藏属性。己方驻军单位可没有世界位置，其对象仍由己方实体观察返回。

`packing.state` 为 `packed`（已折叠）、`unpacked`（已展开）、`packing`（折叠中）或 `unpacking`（展开中）；`progress` 范围 0..1。`packed` 反映切换完成前的当前形式，`changing` 表示过程是否进行中。不是所有攻城器械都可折叠；没有 Pack 组件返回 `null`。

## 视野、兼容与验证

只读扩展在读取非己方实体之前检查实际可见性；公开属性进入 GUI 迷雾缓存时进行深复制，静态对象转入迷雾后保留其 `last_seen` 时的属性，不更新驻军、战斗、所有者或资源状态。移动对象仍不保留迷雾历史。Python 对每层属性再次白名单重建，限制列表、字符串、ID 和数值，丢弃未知字段。

新增模拟脚本需要重启 MCP 客户端并重新加入房间才能加载。房主不需要安装模组，也不需要重启。公开命令覆盖排队目标／位置／建造任务、集结点设置／清除、姿态、己方删除、生产及取消、折叠及取消、普通驻军卸载与炮位进出；原生名称与规则另有受限查询，详见 [玩家与命令](player-actions.md) 与 [MCP 手册](../MCP.md)。能力标记与可执行命令仍需分别检查；建筑升级等尚无专用接口。

`rally_point` 为 v1 可选组，旧桥接缺少时返回 `null`。原生命令名映射为工具任务名；移动资源显示 `gather_near_position`，未知命令显示 `unsupported`。隐藏目标 ID 隐去，坐标仅取原生按所有者视野维护的最后已知位置，不读取目标实时状态。公开地图即使显示己方建筑也不读取该私有队列。队列截断时禁止追加，但允许替换／清除；集结点和单位 `current_target` 含义不同，须分别观察。

自动测试：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe scripts\check_mod_contracts.py
```
