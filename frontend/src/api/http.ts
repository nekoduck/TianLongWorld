/**
 * [INPUT]: 依赖浏览器 fetch，依赖 types.ts 的协议类型，依赖 VITE_API_BASE（缺省走 Vite 同源代理）
 * [OUTPUT]: 对外提供 api（newSession(worldId) / interact(req)）、ApiError（status + code + message）
 * [POS]: api 的唯一后端出口，全项目唯一裸写 fetch 处；把网络失败、业务错误 {detail, code} 与 422 {detail: [...]}
 *        统一收敛为 ApiError。前端不设 Mock——任何前端替身都免不了复刻世界规则，离线体验交给后端 LLM_PROVIDER=mock
 * [PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
 */
import type { InteractRequest, InteractResponse, NewSessionRequest, NewSessionResponse } from '../types'

const BASE = import.meta.env.VITE_API_BASE ?? ''

export class ApiError extends Error {
  /** HTTP 状态码；网络不可达为 0 */
  readonly status: number
  /** 服务端裁决码：dead / busy / not_found / director / llm_unavailable；422 校验错误与网络失败为 null */
  readonly code: string | null

  constructor(status: number, message: string, code: string | null = null) {
    super(message)
    this.status = status
    this.code = code
  }
}

// 业务错误体是 {detail: string, code}；FastAPI 的 422 只有 {detail: 数组}，没有 code
type ErrorBody = { detail?: unknown; code?: unknown } | null

function detailOf(body: ErrorBody): string | null {
  const detail = body?.detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail) && detail.length) return `招式不合规矩：${detail[0]?.msg ?? '参数错误'}`
  return null
}

const codeOf = (body: ErrorBody) => (typeof body?.code === 'string' ? body.code : null)

async function post<T>(path: string, payload: unknown): Promise<T> {
  let res: Response
  try {
    res = await fetch(`${BASE}${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
  } catch {
    throw new ApiError(0, '与江湖失去联系——后端是否已启动？')
  }
  const body: unknown = await res.json().catch(() => null)
  if (!res.ok) {
    const err = body as ErrorBody
    throw new ApiError(res.status, detailOf(err) ?? `天机紊乱（HTTP ${res.status}）`, codeOf(err))
  }
  return body as T
}

export const api = {
  /** worldId 为 null 开辟新世界；携带则在该世界重新投胎 */
  newSession: (worldId: string | null) =>
    post<NewSessionResponse>('/api/session', { world_id: worldId } satisfies NewSessionRequest),
  interact: (req: InteractRequest) => post<InteractResponse>('/api/interact', req),
}
