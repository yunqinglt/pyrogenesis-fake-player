---
name: 0ad-train-near-civic
description: 在 0 A.D. 普通玩家 MCP 中，按市政中心、兵种模板和追加数量，从同一基地附近的合适建筑（含市政中心）选择空闲或较短队列，分批训练并核对数量。
---

# 基地附近批量训练

输入 `civic_centre_id`、`template`、`count`。`count` 是本次追加数量，不是军队总数。可选 `radius`（默认 200 世界单位）、`max_producers`（默认最近 4 座）、`batch_size`（默认 10，每次最多 100）。本技能只安排这批训练，不自动建房、研究、取消生产或修改集结点。

使用已有普通玩家 MCP 会话。先 `player_status`，确认 `ready=true`，再 `observe_player`；仅使用这些公开响应和 `describe_options`。不另开服务器、读取存档或引擎快照。用户已要求执行训练时，可直接按下述流程执行，无须再次请求相同授权。

1. 核对市政中心为当前己方、已完成的 `CivCentre`，有世界位置。候选建筑必须同属己方、已完工，且 `properties.building.training.entities` 包含精确的 `template`。市政中心本身满足条件时也参与选择。
2. 限于 `radius` 内，且以输入市政中心为最近己方市政中心的基地范围。先排除不适用和不可用的生产者（见下一步），再按距离选最近 `max_producers` 座；不是先截取最近四座再检查。无候选时报告缺口，不自动到其他基地生产。距离是直线距离，不是寻路耗时。
3. 读取各候选 `building.production.queue`，`time_remaining` 单位为毫秒。先考虑空队列，再比较可知剩余负载与距离；不取消已有研究。自动续产、暂停队列、未知队列及已有 16 项的队列本轮排除：投影最多 16 项，不能断言它们完整。用选中建筑的 `describe_options(kind="training",ids=[template])` 确认科技前提、资源和人口参考成本。
4. 在近处可用生产者之间按预计负载分配小批次。空闲建筑可并行，繁忙建筑只在其等待时间仍合适时追加；每次 `train_units(building_id,template,count)` 的 `count` 为 1..100。批量训练折扣和生产者修正使单模板参考时间不能证明最优分配，优先减少长队列，并保留批量收益。
5. 每次提交后记录建筑、批次数和原生队列 ID；在资源、人口、所有权或前提改变时重新观察再继续。计分板人口包含已开始批次的预留，但队尾批次可能尚未预留，公开队列没有开始／预留标记。`limit-count` 只提供当前人口空间上界；不能一概把所有队列再扣一遍，也不能把整个差额都当作未承诺空间。有任何既有单位批次时，核对其他生产承诺、必要时等待其开始或完成，再逐批观察提交，不盲目大量追加。总追加量不超过用户的 `count`；资源或人口不足时停止并报告未提交数量，不擅自扩大任务。
6. `accepted=true` 只确认提交。观察队列与新单位确认；超时或不明响应先核对队列和出生 ID，不直接重试。新单位可能按建筑集结点离开或驻军，不因生产建筑附近找不到就补训。保留各建筑原集结点：新单位出生时采用各自生产建筑当时的默认队列。

需要快速算候选与分配时，用自带的 [scripts/plan_training.py](scripts/plan_training.py)。它仅在本地读取公开响应的 JSON，输出计划，不连接游戏、不执行动作。无终端能力时按上面相同步骤调用 MCP。

```json
{
  "observation": {"说明": "替换为本次 observe_player 的完整 result 对象"},
  "request": {
    "civic_centre_id": 301,
    "template": "units/pers/infantry_archer_b",
    "count": 20,
    "unit_cost": {"food": 50, "wood": 50, "stone": 0, "metal": 0, "population": 1, "time": 10}
  }
}
```

ID、模板、数值均为格式示例；`unit_cost` 从本次 `describe_options` 提取，不照抄。`unit_cost` 省略时脚本仍能列出分配，但会标记未完成成本预检，执行前必须补查。不同生产者有不同单模板成本／时间时分别核对，脚本的共同参考值只用于初步估算。

脚本输出 `budget_status`：缺成本时为 `needs_unit_cost`；有成本但观察到任意己方建筑已有单位批次时为 `needs_queue_reservation_review`，同时 `population_reservation_uncertain=true`；没有既有单位批次时也只标记 `reference_budget_only`，不宣称原生预算校验完成。`population_headroom_upper_bound` 未扣未预留的旧批次承诺，不能直接当作可追加人数。通过 `queued_unit_building_ids` 定位要复查的队列。当前安装版 0.28 的 `Trainer.Item.Queue` 扣资源、`Trainer.Item.Start` 才尝试预留人口；公开 MCP 投影没有 `started` 或预留数量，因此脚本不推断每批是否已预留。

```text
python scripts/plan_training.py request.json
```

上面的脚本路径相对本 `SKILL.md` 所在目录解析，安装到其他目录后也使用该技能自己的脚本。

执行 `actions` 前重查当前队列和预算；`planned_count` 与 `unplanned_count` 是计划数量。每个请求只执行一次，记录已提交累计数；继续剩余部分时传剩余数量，而不是重复原请求。交付时分别报告已提交、已出生、仍在排队及未提交数量。
