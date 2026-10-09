# presentation/
> L2 | 父级: engine/app/CLAUDE.md

表现层：WebSocket 线协议与路由，零业务逻辑。一条连接即一位玩家的会话；只认识 application/bus.py 的命令与回合消息。

成员清单
protocol.py: 客户端帧 spawn{name, location?} / resume{player_id, quiet?}（quiet 只回 session 与叙事为空的终帧，零大模型调用）/ act{text} / choose{option_id}（按 type 判别，extra=forbid）；to_command 帧 → 命令（未入世前的 act / choose 抛 ProtocolError）；to_frame 回合消息 → 服务端帧 session / turn_resolved / narration_delta / turn_completed（选项只下发 id、label、category、why（≤12 字的上榜缘由），意图留在服务端；status 只有语义标签：tier / health / 「北冥神功（略有小成）」式的 skills）；error_frame{code, message}
websocket.py: router 挂载 /ws/play——收帧 → 校验 → 命令 → 总线分派 → 逐帧推送（叙事逐片流式）；坏 JSON / 坏帧 / EngineError / 未知异常都只回 error 帧，连接不断
__init__.py: 包标识

[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
