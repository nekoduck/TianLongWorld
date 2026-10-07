# api/
> L2 | 父级: frontend/CLAUDE.md

后端访问的唯一出口。上层（hooks）只依赖 GameApi 接口，不感知 fetch、不感知是否在 Mock 模式。

成员清单
client.ts: 门面，定义 GameApi 接口（newSession / interact），按 VITE_USE_MOCK 选择实现导出 api 单例，转出 ApiError
http.ts: 真实实现 httpApi，全项目唯一裸写 fetch 处；网络失败与后端 {detail}（字符串或 422 数组）统一收敛为带 status 的 ApiError
mock.ts: 静态实现 mockApi，三帧太湖剧情在客户端回传的整树上逐帧推进（帧只写改动字段，第三帧新增行囊、身份与一条带实体标签的世界大事 {tags, event_desc}），动作含"杀/刺/偷袭/挑衅/吐口水/骂"即触发死亡；镜像后端六段 status_bar，模拟 1.2s 延迟

[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
