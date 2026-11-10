# 0 A.D. 0.28.0 网络与公平性边界

本项目协议实现对应本机安装的 **0.28.0**，而非会继续变化的 main。研究快照来自官方 `v0.28.0` 标签，commit `a2cae4d69f816e9e9d7eecb6bf88f762afc0c90d`；协议号 `0x01010019`，引擎序列化版本 `0.28.0`。具体来源、获取日期与 SHA-256 见 [source_snapshot/manifest.json](source_snapshot/manifest.json)。源码快照版权归 Wildfire Games，保留原版权与 GPL v2 或更新版本声明；`lib/build_version.h` 保留其原 MIT 声明。

## 传输与 XMPP 的角色

直接连接局域网游戏使用 **ENet over UDP**；默认端口 `20595`（`0x5073`），一个通道，应用消息发往通道 `0`，均为 ENet reliable packet。`NetEnet.cpp` 将 MTU 设置为 `1372`；该版本未开启 ENet range-coder compression 或 CRC checksum。`NetHost.cpp` 实现包发送，`NetSession.cpp` 实现客户端 ENet 事件循环。

**XMPP 并非直接局域网连接的必需依赖**。`source/lobby/XmppClient.cpp` 负责公共大厅、房间发现、账号认证、连接信息交换和 NAT/STUN 协助。服务器握手响应的 flags 位 `1` 表示需要大厅认证，此时官方客户端通过 XMPP 发送 LobbyAuth，之后服务器请求 ENet Authenticate。当前 Python 大厅客户端仅接受不要求 XMPP 认证的直接连接；不能通过忽略该位绕过认证。

## 普通应用消息的字节布局

`CNetMessage::Serialize` 位于 `source/network/NetMessage.cpp`。每个 ENet packet 对应一条应用消息，不是 TCP 字节流。

| 字段 | 字节数 | 编码 |
| --- | ---: | --- |
| message type | 1 | 正整数，枚举见 `NetMessages.h` |
| total message size | 2 | unsigned big endian，包含这 3 字节 header |
| message fields | 可变 | 按消息声明顺序 |

`Serialization.h` 的普通整数都是 big endian。`CStr.cpp` 区分两类字符串：`CStr` 是 `u32 BE` 字节长度加原始字节（文本通常是 UTF-8）；`CStrW` 是 **UTF-16BE + `0000` 终止符**，没有长度前缀。`NMTCreator.h` 数组没有元素个数前缀，直接重复元素直至 packet 结束，因此数组只出现在消息尾部。空数组编码为零字节。

`GAME_SETUP` 和 `SIMULATION_COMMAND` 是特殊消息：header 后使用引擎 BinarySerializer 序列化 JS 值，并不遵从普通字段布局。它们在 Python 大厅解码器中保持 opaque bytes，禁止作为 MCP 结果返回。

## 握手与身份

`NetClient.cpp` 的状态机与 `NetServer.cpp` 的服务端状态机规定以下流程：

1. ENet connect 完成，客户端进入 CONNECT，等待服务器；不用先发应用消息。
2. 服务器发送 `SERVER_HANDSHAKE=1`：magic `0x5073013f`、protocol u32、engine version CStr、enabled mods 的 `{name CStr, version CStr}` 数组。
3. 客户端发送 `CLIENT_HANDSHAKE=2`：magic response `0x50630121`，同协议号与本机兼容引擎、mods 列表。`NetProtocol.cpp::CheckHandshake` 按名称、版本和顺序比较所有需要兼容检查的 mods；没有数组长度字段。基础游戏通常为 `[("0ad", "0.28.0")]`。不能为了通过握手而谎报本机不具备的模组。
4. 服务器发送 `SERVER_HANDSHAKE_RESPONSE=3`：protocol u32、flags u32、GUID CStr。**GUID 由服务器生成**并保证会话唯一；客户端保存，不能自造来冒充别的连接。
5. 不需大厅认证时，客户端发送 `AUTHENTICATE=4`：name CStrW、password CStr、controller secret CStr。普通玩家 controller secret 留空。无密码房间的 password 留空。非空密码不能直接发送明文：`Hashing.cpp` 使用 keyed BLAKE2b 生成盐后 Argon2id，客户端还按玩家名再次计算；当前大厅工具不提供密码房间功能。
6. 服务器发送 `AUTHENTICATE_RESULT=5`：code u32、host ID u16、is-controller u8、message CStrW。成功 code 为 `0`（普通）、`1`（存档）、`2`（重连）；`3` 表示密码错误。**host ID 也由服务器递增分配**，和游戏 player ID 不是一回事。
7. 服务器发送 `PLAYER_ASSIGNMENT=11`：重复 `{guid CStr, name CStrW, player ID i8, ready status u8}`。player ID `-1` 是观察者；以本连接 GUID 查找实际座位。分配可以由主机后来更新。

Python API 位于 `src/pyrogenesis_mcp/protocol.py`：`build_handshake`、`build_authenticate`、`build_ready`、`build_chat`；通用 `decode_message` 返回 `Message(type, fields)`。`Message.__repr__` 只包含类型，避免日志意外展示秘密或原始 payload。固定字节 fixture 测试覆盖 header、Unicode 身份、负的观察者 ID、无长度数组及损坏包。

### 空密码的精确行为

`Hashing.cpp::HashCryptographically` 开头明确执行 `if (string.empty()) return string;`，所以 `HashCryptographically("", playerName)` 是空字符串，不是盐化的空密码摘要。`network/scripting/JSInterface_Network.cpp::StartNetworkJoin` 仅设置玩家名和服务器地址，没有调用 `SetGamePassword`。`StartNetworkHost` 也只在 `hasLobby` 分支设置密码。`build_lan_authenticate(name, password="")` 因此发送两个空 CStr（password 和 controllerSecret），并拒绝非空密码参数。

公共大厅的非空密码是另一条流程：先计算 `H(password, hostJID + password + PS_SERIALIZATION_VERSION)`，再计算 `H(firstHash, playerName)`。这里 `H` 的盐是先以固定 16 字节 key `eb521d1487a8b86107f0306d08229e20` 对 UTF-8 salt 做 BLAKE2b，摘要长16字节；随后 Argon2id v1.3，输出32字节、opslimit=2、memlimit=32768，最终以大写十六进制字符串表达。空输入两层都提前返回空串。该算法的准确来源保存在源码快照，当前无需引入额外密码学依赖，因为工具只实现直接 LAN 分支。

## 大厅可控消息

| 枚举 | 名称 | 字段 |
| ---: | --- | --- |
| 6 | CHAT | sender GUID CStr、文本 CStrW、接收 GUID CStr 数组；空数组广播 |
| 7 | READY | GUID CStr、status u8（0 未准备，1 已准备，2 保持准备） |
| 8 | CLEAR_ALL_READY | 无字段；普通玩家无此控制权限 |
| 9 | GAME_SETUP | 引擎 ScriptVal；不向模型回传 |
| 10 | ASSIGN_PLAYER | player ID i8、GUID CStr；仅房间控制者 |
| 11 | PLAYER_ASSIGNMENT | 公开身份、座位与准备状态列表 |
| 24 / 25 | GAME_START / SAVED_GAME_START | init attributes CStr；可能包含地图种子等，不向模型回传 |

服务器会覆盖客户端提交的 chat sender GUID 和 ready GUID，采用实际会话身份。MCP 仍应固定当前会话身份，且只暴露加入、公开状态、聊天和自己准备状态。收到 GAME_START、SAVED_GAME_START 或 JOIN_SYNC_START，纯 Python 大厅模式断开，不发送伪造的 LOADED_GAME。

## 为什么不能仅靠 ENet 加入后一直发空命令

0 A.D. 使用确定性锁步模拟。官方客户端在本地运行完整仿真，`NetClientTurnManager.cpp` 为每回合提交自己的命令完成批次，并从真实模拟状态计算 SYNC_CHECK 哈希。服务器将所有玩家命令广播给已加载客户端（`NetServer.cpp::OnSimulationCommand`），按回合批次协调推进（`NetServerTurnManager.cpp`）。

独立 Python 实现即使能构造 SIMULATION_COMMAND，也没有完成确定性仿真、地图加载、AI、脚本和真实哈希。谎报加载完成或复制别人的哈希会造成锁步停顿或掩盖失步。真实对局方案由官方 Pyrogenesis 客户端负责模拟、加载和哈希，Python ENet relay 转发两侧可靠包，MCP 只经本地游戏 GUI bridge 下达允许的自身单位操作。

## 对模型隐藏的信息

网络协议是内部可信传输层，不能作为模型观察接口。以下内容虽可能存在客户端内存或网络中，仍需隐藏：

- 全部对手的 SIMULATION_COMMAND，包含未发现单位 ID、目标、移动坐标、生产与资源行为。
- 地图种子、完整初始化参数、完整地图与未探索区域；重连的 `JOIN_SYNC_START` 和 file transfer 还可能传输完整仿真存档，`NetClient.cpp` 中以 zlib 压缩。
- 同步哈希、OOS dump、原始 packet、任意调试日志和文件路径；这些接口可能旁路正常可见性约束。
- 其他玩家的资源、队列、科技与未可见单位状态；不能因为确定性客户端知道，就让模型知道。
- password/controller secret、任意房间控制者权限或任意 player ID / simulation command 注入。

公平观察必须来自固定玩家的 GUI 可见性接口，再由 Python 白名单投影。当前允许范围可进一步收紧为自己拥有的实体、自己资源与人口。动作必须校验实体归当前玩家所有，并走引擎正常 PostNetworkCommand，拒绝任意 JS、任意对象、对手 ID 与作弊命令。原始通信不能进入 MCP resource、tool 结果或自动错误消息。

服务器在 CheatsEnabled 关闭时校验命令声明的 player 与连接座位对应（`NetServer.cpp` 约第 1200 行），该检查不会替代 MCP 的自身权限边界。在房间允许作弊时，本项目仍应维持相同的限制。
