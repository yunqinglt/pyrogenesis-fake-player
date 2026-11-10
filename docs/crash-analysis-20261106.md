# 截图报错与恢复存档崩溃分析（2026-11-06）

## 结论

根目录截图显示的是一条可重现的 **MCP 观察桥接运行期错误**：己方实体枚举没有排除历史影像（`mirage`），而后续 properties 投影明确拒绝这种实体。已经使用项目真实 JS 文件和合成数据离线复现了完全相同的异常；无需连接游戏或读取任何其他玩家状态。

它与此前房主的 `Error loading js`、客户端的 `mozjs128-release.dll` 访问违规是三个需要分别记录的现象。截图提供了第一个问题的明确定位，但不能证明后两者同因。此前恢复存档的原生崩溃仍未解决。

初始阶段只读分析截图与既有转储。用户随后授权从恢复残局的新房间实际复现；已完成一次正常加入并保全新崩溃证据，见文末。两个阶段均未修改游戏／模组运行代码，没有提交移动、生产、建造等玩法动作。

## 截图证据

实际查看文件：`屏幕截图 2026-11-06 180556.png`。截图仍在比赛界面，左上角重复出现：

```text
JavaScript error: simulation/components/zy_mcp_properties.js line 304
Unit properties require a normal player entity.
GuiInterface.prototype.MCPProjectEntityProperties ... :304:19
GuiInterface.prototype.MCPGetEntityProperties ... :417:21
GuiInterface.prototype.ScriptCall ... GuiInterface.js:2131:20
ownStates ... gui/session/zz_mcp_player.js:49:46
tick ... :596:33
onTick ... :668:25
Error calling component script function ScriptCall
```

两次可见报错前缀为 `[696.843]` 和 `[697.342]`，相隔约 0.5 秒，与桥接 `interval=500` 毫秒一致。这些前缀不直接当作游戏仿真时间。对应新开局诊断日志 `interestinglog_1793958856_8228.html` 中，该异常正文出现 128 次；这里只筛查异常消息，没有把诊断日志当作对局情报。

这是已加载函数主动抛出的异常。截图本身没有显示 JS 语法错误、脚本无法编译或新的原生崩溃弹窗。

## 源码链路

以下 JS 原生行为均从当前安装版的独立 `public.zip` 核对，版本为 0 A.D. 0.28.0。C++ 部分另外核对了官方发布提交 `a2cae4d69f816e9e9d7eecb6bf88f762afc0c90d`，没有使用工作区旧 `_0ad-src` 的数值或实现代替发布版。

| 位置 | 已确认行为 | 对报错的影响 |
| --- | --- | --- |
| 原生 `simulation/components/GuiInterface.js:1026` | `GetPlayerEntities(player)` 直接调用 `RangeManager.GetEntitiesByPlayer(player)` | 名称不保证只返回当前真实单位／建筑 |
| 官方 `source/simulation2/components/CCmpRangeManager.cpp:1097..1123` | 按 owner mask 遍历筛选；没有历史影像过滤 | 所有者匹配的历史影像也可进入候选集 |
| 原生 `simulation/components/Fogging.js:63..95` | 创建 `mirage|...`，并把其 Ownership 设为原实体所有者 | 为其他玩家视野维护的己方建筑历史影像，仍可与己方真实建筑拥有相同 owner |
| 原生 `simulation/components/GuiInterface.js:276..278` | 有 Mirage 组件时，返回状态标记 `mirage=true` | 调用方已有足够信息跳过这种对象 |
| 本项目 `mod/mcp_player/gui/session/zz_mcp_player.js:38..53` | `ownStates` 仅检查 `state && state.player === player`，随后查询 properties | 历史影像满足所有者条件，误入严格投影 |
| 本项目 `mod/mcp_player/simulation/components/zy_mcp_properties.js:300..304` | 校验玩家、ID、状态，并在 `state.mirage` 时抛出截图里的异常 | 保护边界按设计拒绝历史影像，GUI 侧却没有预先过滤 |

官方 C++ 原文获取地址：

```text
https://gitea.wildfiregames.com/0ad/0ad/raw/commit/a2cae4d69f816e9e9d7eecb6bf88f762afc0c90d/source/simulation2/components/CCmpRangeManager.cpp
```

因此问题出在“所有者正确”被当成“正常实体”的充分条件。它不是让 properties 接受历史影像的理由，也不需要修改迷雾规则。地图观察已有 `state.mirage` 过滤（`zz_mcp_map.js`），漏项位于己方 `ownStates` 路径。

截图没有记录触发时的具体实体 ID，因此不能声称已经识别当时是哪一个建筑影像。`GetEntityState` 二次查询得到空状态等其他瞬态也可能触发同一复合校验；但历史影像路径已经形成源码闭环并离线重现，是目前最强的具体解释。

## 离线复现

诊断页：`.runtime/diagnostics-mirage-20261106.html`。它加载项目真实的 `zy_mcp_properties.js` 与 `zz_mcp_player.js`，仅提供两个合成实体：一个正常实体和一个相同所有者的历史影像。

实际运行结果：

```text
PASS: real source reproduces exact screenshot error when same-owner mirage
enters ownStates; removing mirage from synthetic enumeration restores
normal projection. No game connection or game state used.
Requests=[11,11,12,11]
```

正常实体单独枚举通过；加入影像后，真实投影抛出 `Unit properties require a normal player entity.`；只从合成枚举中移除影像，正常实体再次通过。这个检查证明缺陷路径，未修改源文件，也不等同于已在真实对局部署修复。

异常会影响可用性：`tick()` 在 `ownStates` 失败后写出 `available=false`，丢弃本轮普通观察；`player_status` 又依赖观察成功来设置 `ready=true`。因此该问题可以解释“网络仍 connected，但 ready 短暂为 false／观察暂不可用”，不能由此认定断网。错误由模拟端 ScriptCall 记录，GUI 捕获异常并写通用错误也不会抹去游戏界面的红字。

## 与此前两类错误的关系

| 现象 | 证据与范围 | 当前判断 |
| --- | --- | --- |
| 房主“无法主持游戏：Error loading js” | 用户报告，后来重开成功；没有对应脚本名、具体行号或完整异常栈 | 尚不能定位，不能仅凭 `js` 字样关联截图 |
| 客户端恢复存档后访问违规 | 两份已保存转储，故障模块／偏移一致；此前符号解析位于 JS GC 根扫描 | 原生崩溃仍待单独排查 |
| 新房间中截图的 properties 异常 | 脚本、行号、完整调用链明确；离线合成复现通过 | 已定位己方枚举漏掉影像过滤的具体缺陷 |

此前对照试验仅撤回本轮新增的集结点 properties 读取，2026-11-06 17:47:33 仍发生相同原生崩溃。这只排除了“该新增读取是必要触发条件”，没有排除整个旧模组／存档恢复的其他交互。本轮没有重复这项试验。

本轮还独立只读解析了两份 minidump 的异常记录和模块表，结果一致：

| 转储 | 异常 | 访问方式／地址 | 故障模块与偏移 |
| --- | --- | --- | --- |
| `.runtime/rally-crash-20261106/crashlog.dmp` | `0xc0000005` | 读 `0x900000016` | `mozjs128-release.dll+0x477c69` |
| `.runtime/rally-crash-20261106/baseline-crashlog.dmp` | `0xc0000005` | 读 `0x900000016` | `mozjs128-release.dll+0x477c69` |

本轮临时只读解析脚本为 `.runtime/diagnostics-minidump-20261106.py`。前述 `JS::RootingContext::traceStackRoots+0x369` 是此前使用本地 PDB 得到的符号结果；本轮重新确认了二进制地址，不把旧的候选栈扫描描述为可靠的完整调用栈。GC 中崩溃能够说明发现损坏的位置，不能单靠它判断最初的损坏来源。

## 后续最小修复与验收条件

下次开发可在 `ownStates` 调用 `MCPGetEntityProperties` 前排除 `state.mirage`，同时核对状态 ID／所有者，保留模拟端对影像和非正常玩家的严格拒绝。不要通过“接受影像”或吞掉所有 properties 异常来掩盖问题；其他真正的校验失败仍应可见。

有意义的回归条件：

1. 枚举中混入与玩家同所有者的历史影像时，真实单位／建筑仍被正常投影；影像既不查询 properties，也不进入己方实体、计分板或动作候选。
2. 不要求所有对象都有世界位置：已驻军的正常己方单位仍须保留，不能用“无位置即丢弃”的替代条件。
3. 正常地基、生产建筑以及驻军单位的属性、驻军关系和队列保持可读；敌方、空状态、失效 ID 的边界仍然有效。
4. 将上述混入情形加入浏览器桥接契约，再正常新开局，跨建造／驻军／卸载和视野变化观察红字与 `ready`。当前契约没有覆盖“同所有者影像混入己方枚举”，因此此前 9 组契约通过没有发现它。

恢复存档的访问违规另需保留现有转储，再使用同一存档核对完全原版恢复与同版本伴随模组的差异。初始阶段只记录了建议；用户随后授权的单次正常复现如下，没有做新的原版／模组 A/B，也不能把新房间测试成功写成恢复崩溃已修复。

## 用户授权的恢复房间复现（2026-11-06 21:37，UTC+8）

用户从残局创建 `127.0.0.1:20595` 联机房间，并明确允许进入后直接抓取相关游戏日志或截图。此前控制台会话已不存在；进程检查仅发现用户手动原安装版房主 **PID 17116**，没有旧受控客户端或 MCP Python 进程。因此新开独占 MCP 控制台，正常加入一次，未操作房主。

| 本地时间 | 已观察证据 |
| --- | --- |
| 21:37:05 | 20595/UDP 由手动房主 17116 持有；新控制台初始化成功 |
| 21:37:27 | 正常 `join_lan_game` 创建受控客户端 **PID 15584**；先为 `network=listening` |
| 随后两次公开状态查询 | `network=connected`、`running=true`、`ready=false`，没有获得有效玩家观察 |
| 21:38:39／21:38:40 | 新 `crashlog.dmp`／`crashlog.txt` 写出，出现原生访问违规 |
| 21:41:06 | 仅抓取 PID 15584 自己的窗口：`Program Error` 对话框与黑色游戏窗口；没有截取其他应用 |
| 21:41:47 | 经公开 `close_game` 关闭本服务的崩溃客户端与中继；进程复核只剩用户房主 17116；未再次加入 |

证据目录：`.runtime/crash-repro-20261106-saved-room/`，与此前保存目录分离。

| 文件 | 内容 |
| --- | --- |
| `mcp.jsonl` | 此次公开状态、加入和关闭请求／响应；没有玩法动作 |
| `crashlog.dmp` | 新转储，204854 字节；SHA-256 `c739be64d28eb9d9566c99b0f54b3c386430cad0b12d2344c32c84e06aa385e3` |
| `crashlog.txt` | 新崩溃正文及环境信息 |
| `window-3277476.png` | 实际查看确认的 `Program Error` 对话框，显示 `Access violation reading 0x900000016` |
| `window-1181024.png` | 实际查看确认的受控游戏黑色窗口，尚未显示比赛界面 |
| `mainlog.html` | 此受控客户端的唯一日志副本，1395836 字节 |
| `interestinglog.html` | 此次副本为空，0 字节；没有将其写成“没有发生原生崩溃”的证据 |

新转储独立解析仍为 **`0xc0000005`、读 `0x900000016`、`mozjs128-release.dll+0x477c69`**，与下午两份转储的故障模块／偏移和访问地址一致。这是先前恢复加载问题的再次复现，不是截图中 `zy_mcp_properties.js:304` 的红字异常。

本次 `mainlog` 只按错误消息和加载路径筛查，没有读取局势快照或把其他玩家信息用于决策。日志包含 `zy_mcp_properties.js`、`zz_mcp_map.js` 的正常加载记录，最后匹配到 `Total map generation time: 0.810967s.`；没有匹配到 `JavaScript error`、`Script error`、`Error loading` 或截图里的 `Unit properties require a normal player entity.`。这个结果说明此次尚未捕获脚本编译／运行报错，不能把 GC 访问违规改写成已证明的 JS 加载错误。

结论：**复现已完成，不需要用户另交截图或继续等待房主开始**。客户端没有进入 `ready=true`，无法开展恢复后的正常观察；用户手动房主仍保留。下一步有价值的是精确版本的原版恢复对照或更可靠的原生调用栈／GC 根损坏诊断；本轮没有实施这些额外实验，也没有重复此前的部分撤回 A/B。
