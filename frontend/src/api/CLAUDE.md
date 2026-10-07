# api/
> L2 | 父级: frontend/CLAUDE.md

后端访问的唯一出口。上层（hooks）只依赖 api 对象与 ApiError，不感知 fetch。前端没有 Mock：任何前端替身都免不了复刻世界规则、生死判定与状态栏渲染，离线体验交给后端 LLM_PROVIDER=mock。

成员清单
http.ts: 导出 api（newSession(worldId: string | null) / interact(req)）与 ApiError，全项目唯一裸写 fetch 处；网络失败、业务错误 {detail, code} 与 422 {detail: 数组} 统一收敛为 ApiError(status, message, code)，code 取 dead / busy / not_found / director / llm_unavailable，422 与网络失败（status 0）为 null

[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
