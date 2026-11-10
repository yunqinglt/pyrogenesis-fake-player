---
name: 0ad-rally-idle
description: 在 0 A.D. 普通玩家 MCP 中，把空闲单位按所有、公民兵、平民、骑兵或冠军筛选，分批集结到指定坐标，并保留正在工作的单位职责。
---

# 按类别集结空闲单位

输入 `x,z` 和 `category`。类别为 `all`（空闲所有）、`citizen`（空闲公民兵，`CitizenSoldier`）、`civilian`（空闲平民）、`cavalry`（空闲骑兵，`Cavalry`）、`champion`（空闲冠军／冠勇，`Champion`）。公民兵和平民分开；类别可重叠，例如冠军骑兵既匹配骑兵也匹配冠军。可选 `exclude_ids`、`limit` 和源区域 `source={x,z,radius}`；默认全己方范围。用户说“公民”且上下文明用词不明确时，说明当前按公民兵理解，结合上下文确认是否应为纯平民。

这是场上单位移动，不设置建筑集结点、不训练、不卸载驻军、不构建阵型。使用已有普通玩家 MCP 会话，仅依赖 `player_status`、`observe_player`、`move_units`；不另起服务，不读取存档或引擎快照。

1. 确认 `ready=true`，取得最新 `observe_player`。验证目标坐标有限且在地图范围；地图内不等于可通行，不假设能穿越水面、悬崖或敌方防线。若已知目标危险，按当前玩家职责说明并选择用户授权范围内的处理。
2. 只选当前己方 `Unit`、`movable=true`、`capabilities.move=true`、有世界坐标的实体，同时要求 `activity.idle=true`、原生状态为 `INDIVIDUAL.IDLE`（或同类以 `.IDLE` 结尾的单位空闲态）且 `current_target=null`。驻军状态必须明确为 `garrisoned_in.holder_id=null`，或 `garrisoned_in=null` 且 `capabilities.garrison=false`（无法作为乘员的单位，例如部分战船）；未知／缺失字段继续排除。`packing.changing` 不能为真。排除用户的 `exclude_ids` 和其他代理／职责记录中仍被保留的单位。
3. 在上述真正空闲集合里用 `properties.identity.classes`／`special_roles` 筛类别，不从模板名包含“horse”之类字符串推断。冠军可用 `Champion` 或 `special_roles` 中的 `champion`。平民优先识别 `Civilian`／`FemaleCitizen`；作为兼容回退，可选有 `Worker`、能采集且没有 `CitizenSoldier/Champion/Cavalry/Hero/Animal/Elephant/Ship/Siege` 的单位。无法明确识别时列为未知，避免误集结祭司、英雄、劳工象或军队。
4. 应用可选源区域，按到目标的距离和 ID 排序，再应用 `limit`。默认 `all` 可包含闲置英雄、祭司和可移动攻城器，调用前在简短计划中标出这些特殊对象，尊重既有职责，不擅自把它们改成其他类型。计分板的空闲人数或人口预留不是选择名单，不能据此补充尚未出生单位。
5. 每批最多 200 个 ID，通过 `move_units(entity_ids,x,z,queued=false)` 提交一次普通移动。分批期间若队伍状态可能变化，先重新观察，剔除已获得任务的单位。没有匹配单位就报告 0，不扩大类别或先停止忙碌单位。
6. 比较后续游戏时间、位置和 `current_target` 确认接收与到达。让原生 AI 自行寻路和合理响应；进度正常时不重发。终点有空间误差不要求所有单位重合。若已知有守卫绑定，不以停止冒充解绑；本技能默认跳过有守卫职责者。只有用户要求换岗时才另行 `abandon_targets` 后移动。

需要确定性筛选／分批时，运行 [scripts/plan_idle_rally.py](scripts/plan_idle_rally.py)。输入是本次公开响应，输出仅为候选和调用计划；没有执行器，不会修改游戏。

```json
{
  "observation": {"说明": "替换为本次 observe_player 的完整 result 对象"},
  "request": {"x": 600, "z": 1100, "category": "citizen", "exclude_ids": []}
}
```

```text
python scripts/plan_idle_rally.py request.json
```

上面的脚本路径相对本 `SKILL.md` 所在目录解析，安装到其他目录后也使用该技能自己的脚本。

执行计划前重查每批资格。报告筛中数量、已提交数量、已确认到达数量、仍在行军或因状态改变跳过的数量；不能把提交确认当作到达证据。
