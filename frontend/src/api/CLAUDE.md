# api/
> L2 | 父级: frontend/CLAUDE.md

后端访问的唯一出口。backend 一侧：useGame 只依赖 GameApi 接口，不感知 fetch、不感知是否在 Mock 模式；engine 一侧：useEngineGame 只依赖 EngineSocket，不感知 WebSocket 与存储。两侧互不知晓。

成员清单
client.ts: 门面，定义 GameApi 接口（newSession / interact），按 VITE_USE_MOCK 选择实现导出 api 单例，转出 ApiError
http.ts: 真实实现 httpApi，全项目唯一裸写 fetch 处；网络失败与后端 {detail}（字符串或 422 数组）统一收敛为带 status 的 ApiError
ws.ts: engine 适配器 EngineSocket，全项目唯一裸写 WebSocket 处；同源连 /ws/play（ws / wss 随页面），spawn / resume 可排队等连接，act / choose 只在连接就绪时发出（返回 false 表示断线未发）；session 帧的 player_id 记入 localStorage（读写全包 try/catch，无存储照常可玩）；断线清空待发队列，有前世则 1/2/4/8s 退避重连并自动悄悄 resume（quiet：不复述此景、零大模型），forget() 后（死亡）不再续；resume(id, quiet?) 缺省复述此景（页面刷新后的续前缘）；构造时可交接本页已知的前世（换连接后断线照样重连）；storedPlayer() 供入世页续前缘
mock.ts: 静态实现 mockApi，三帧太湖剧情在客户端回传的整树上逐帧推进（帧只写改动字段，第三帧新增行囊、一条私密情报 secrets（雾中无人目睹，故不记公开身份）与一条带实体标签的世界大事 {tags, event_desc}），动作含"杀/刺/偷袭/挑衅/吐口水/骂"即触发死亡；镜像后端六段 status_bar，模拟 1.2s 延迟

[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
