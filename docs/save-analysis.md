# 离线存档局势分析

本工具只读解析用户提供的 `.0adsave`，独立于 MCP，不需要游戏运行、服务器连接或第三方 Python 库。当前支持 **0 A.D. 0.28.0**，输出经济、人口、单位和建筑构成、研究、生产队列、目标、驻军、折叠状态及对象分布。原始存档保持不变。

## 使用

在项目根目录运行，输出目录必须尚不存在：

```powershell
.\.venv\Scripts\python.exe scripts\analyze_save.py `
  "$env:USERPROFILE\Documents\My Games\0ad\saves\savegame-0002.0adsave" `
  --output .runtime\save-analysis-0002 --player 2
```

安装项目后也可运行 `pyrogenesis-analyze`；或者将 `src` 加入 `PYTHONPATH` 后运行 `python -m pyrogenesis_analysis`。分析包仅使用 Python 标准库，直接运行脚本无需安装 MCP 依赖。

给模型复盘时可使用 [0ad-analyze-save 技能](../skills/0ad-analyze-save/SKILL.md)。构建后的 player-kit 和 skill-kit 为该技能携带完整解析器及默认案例规则，复制整个技能目录即可独立使用，无需本项目或安装 MCP。源码技能入口复用 `src/pyrogenesis_analysis`，构建时从同一份源码收录，避免维护两份解析器。示例：

```text
python "<技能目录>/scripts/analyze_save.py" "<存档>.0adsave" --output "<新报告目录>" --player 1 --templates "<同版本public目录或ZIP>"
```

Linux 应显式提供本机公开模板路径，例如 `/usr/share/0ad/data/mods/public`；工具不随包分发游戏资源。技能同时说明单档复盘和多档比较的证据口径，不把不同地图的残局当作同一局行动过程。

`--player` 指定报告焦点，默认使用存档中的玩家 ID。它不会删除其他玩家的数据，也不会还原焦点玩家当时的迷雾视野。重复分析时选择新的输出目录，工具不覆盖已有报告。

原生 `Obstruction` 的二进制结构取决于对象模板，因此解码需要对应版本的公开 XML 模板。默认自动发现本机游戏资源和已有运行目录中的公开资源副本，跳过锁定的候选并记录原因。也可重复使用 `--templates` 指定公开模组目录或 ZIP，按从低到高的覆盖优先级排列。无法找到版本兼容的模板，或者遇到未知组件、未知原型、截断数据、尾随数据时，工具明确失败，不猜测后续字节。自定义模组的模板覆盖应显式提供。

## 输出与信息边界

| 文件 | 内容 |
| --- | --- |
| `report.json` | 结构化快照、来源 SHA-256、完整解码字节数、实际与预期规则、所有玩家、实体及可核对的发现 |
| `report.md` | 中文概况、经济与人员配置、规则差异和解释限制 |
| `map.html` | 自包含交互对象图，支持玩家、类别和名称过滤及点选详情；无外部脚本或网络请求 |

所有产物标为 **`offline-save-omniscient`**：这是获授权的离线全知分析，包含迷雾之外的信息。不得把它作为正在公平对战的模型观察。常规模型玩家仍遵守 [AGENT.md](../AGENT.md) 和 [MCP.md](../MCP.md) 的视野限制；离线案例的全局事实、当时允许的观察和人工解释须分开保存。

对象图显示保存的世界坐标，不是地形图、通行网格或建造位置验证。驻军单位通常没有世界位置。迷雾历史影像 `mirage` 与真实实体分开表示，不重复计入兵力；装饰对象默认隐藏。

## 未来案例规范

用户于 **2026-11-06** 确认，后续案例是人与游戏 AI 对局，停战 **45 分钟**，胜利条件只清空敌方单位，**建筑不计入**。这些预期记录于 [analysis_rules.json](../analysis_rules.json)，可用 `--rules` 指定另一个规范文件。

| 允许的视野 | 存档设置 |
| --- | --- |
| 迷雾、不预先探索 | `RevealMap=false, ExploreMap=false` |
| 迷雾、地图已探索 | `RevealMap=false, ExploreMap=true`，不授予敌方当前视野 |
| 全图视野 | `RevealMap=true` |

`Ceasefire=45` 的原始单位是分钟。原生仅单位胜利条件为 `conquest_units`，分类表达式为 `Unit+!Animal`；动物也不计入。建筑数量另列，不参与此单位清空计数。工具同时保存 `rules_audit.expected` 和 `rules_audit.actual`，列出差异，不改写存档、房间或胜负。

这份旧测试存档 `savegame-0002` 的实际规则是：双方均非游戏 AI，未探索迷雾，停战 45 分钟，胜利条件 `conquest`。前两项差异（对手类型、胜利条件）会被标记；它仍可用于功能验证。

## 统计口径

- 人口来自保存的 `Player.popUsed`，上限为 `min(popBonuses,maxPop)`。单位总数不等于人口，单位可能占多个名额，原生生产预留也可能计入。
- 已完成对象类别优先来自存档 `TechnologyManager.typeCountsByClass`，再与保存的 `classCounts` 逐类核对；地基单独计数。无法分类时保留未知 ID，并将单位清空计数标为不完整。
- Worker、平民、公民兵属于可重叠类别。空闲 Worker 依据保存的 UnitAI 状态，排除驻军；交还背包、施工或行军者不因采集人数为零就被当成空闲。
- 采集人数来自 `Player.resourceGatherers`；背包、资源存量、无限农田、队列和驻军来自各自模拟组件。`Infinity` 被转换为合法 JSON，并另标 `infinite=true`。
- 研究过的科技保留原生 ID，包括自动科技与文明加成；阶段从保存的阶段科技识别。名称来自公开模板的源语言，明确是基础模板资料，不冒充中文本地化或受科技修改后的当前数值。
- 停战状态优先展示保存的 `CeasefireManager`；从初始设置推算的剩余时间另列为估计。经济提示和空闲提示是核查线索，不自动改派单位，不声称已知留守意图。
- `building_fire.arrowsLeft` 是保存的射击批次余箭状态，不是 GUI 显示的每轮箭矢总数；空闲时为零不表示建筑失去射击能力。后者还需要基础箭数、驻军加成及当前科技修正，首版不自行推算。

## 格式依据与验证范围

ZIP 内的 `metadata.json` 是 JSON，`simulation.dat` 是按组件顺序写出的原生二进制流，组件没有统一长度封套。解析器按 0.28.0 对应提交 `a2cae4d69f816e9e9d7eecb6bf88f762afc0c90d` 的 `source/simulation2/serialization`、`source/simulation2/components` 及版本一致的公开模拟脚本读取。工作区 `_0ad-src` 不是同一提交，不能用于猜测格式。

解析不运行 JavaScript，不反序列化 Python 可执行对象，不解压 ZIP 路径到磁盘，也不调用游戏命令。长度、嵌套、节点和解压大小都有上限；JS 引用、集合、映射、稀疏数组、类型化数组有明确表示。

早期测试样本已完整消费 **2,956,186 / 2,956,186 字节**，28 种系统组件、66 种普通组件、4,781 条模板映射；玩家分类计数完全吻合，原档 SHA-256 前后相同。2026-11-07 新增残局技能验收：红海和下努比亚两份真实人对 Petra 存档分别完整消费 **19,592,412 / 19,592,412** 和 **14,667,718 / 14,667,718 字节**，全部玩家分类核对通过，均无解析警告。两份旧档实际胜利条件均为 `conquest`，与未来案例预期 `conquest_units` 的差异保留在规则核对中。历史测试记录见 [validation.md](validation.md)；这两份真实含 AI 样本补充了此前仅有合成结构的 Petra 分支验证。

本快照没有完整地形高度图；AI 分支可能保存通行掩码，但首版未将其输出为路径分析。工具不恢复动作历史、玩家意图、完整视野历史，不预测胜者或给出单一战斗力评分，也不自动训练模型或生成有理由的策略案例。策略数据的保存建议见 [terrain-and-strategy-plan.md](terrain-and-strategy-plan.md)。
