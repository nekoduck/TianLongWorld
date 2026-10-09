# presentation/
> L2 | 父级: engine/app/CLAUDE.md

表现层：WebSocket 线协议与路由，零业务逻辑。一条连接即一位玩家的会话；只认识 application/bus.py 的命令与回合消息。

成员清单
protocol.py: 客户端帧 spawn{name, location?} / resume{player_id, quiet?}（quiet 只回 session 与叙事为空的终帧，零大模型调用）/ act{text} / choose{option_id}（按 type 判别，extra=forbid）；to_command 帧 → 命令（未入世前的 act / choose 抛 ProtocolError）；to_frame 回合消息 → 服务端帧 session / turn_resolved / narration_delta / turn_completed（选项只下发 id、label、category、why（≤12 字的上榜缘由）与 risk（稳妥 / 有险 / 凶险，只露区间最坏一端，有才下发），意图留在服务端；status 只有语义标签：tier / health / 「北冥神功（略有小成）」式的 skills / 人情 bonds{name, attitude, cause} / 心事 pursuits{label, note} / 眼前的暗流 clocks{name, kind, progress, maximum}（id 与挂处不下发）/ 名望 renown（语义标签，点数不下发）/ 时辰 time（「第一日·辰正」，世界的 tick 不下发）；turn_resolved.intent 整个下发，含此行所为 motivation（动作可能是 THINK 沉思）；世界心跳的白描几乎都不出声（facts 里只有眼前人群的溃散），时间流逝只经状态栏的时辰被感知；与 frontend/src/engineTypes.ts 逐字段镜像）；error_frame{code, message}
websocket.py: router 挂载 /ws/play——收帧 → 校验 → 命令 → 总线分派 → 逐帧推送（叙事逐片流式）；坏 JSON / 坏帧 / EngineError / 未知异常都只回 error 帧，连接不断
__init__.py: 包标识

[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
