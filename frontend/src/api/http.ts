/**
 * [INPUT]: 依赖浏览器 fetch，依赖 types.ts 的协议类型，依赖 VITE_API_BASE（缺省走 Vite 同源代理）
 * [OUTPUT]: 对外提供 httpApi（GameApi 的真实实现）、ApiError
 * [POS]: api 的 HTTP 适配器，唯一一处裸写 fetch 的地方；把网络失败与后端 {detail} 统一收敛为 ApiError
 * [PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
 */
import type { InteractRequest, InteractResponse, NewSessionResponse } from '../types'

const BASE = import.meta.env.VITE_API_BASE ?? ''

export class ApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

// FastAPI 的 detail：业务错误是字符串，422 校验错误是数组
function detailOf(body: unknown): string | null {
  const detail = (body as { detail?: unknown } | null)?.detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail) && detail.length) return `招式不合规矩：${detail[0]?.msg ?? '参数错误'}`
  return null
}

async function post<T>(path: string, payload?: unknown): Promise<T> {
  let res: Response
  try {
    res = await fetch(`${BASE}${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: payload === undefined ? undefined : JSON.stringify(payload),
    })
  } catch {
    throw new ApiError(0, '与江湖失去联系——后端是否已启动？')
  }
  const body: unknown = await res.json().catch(() => null)
  if (!res.ok) throw new ApiError(res.status, detailOf(body) ?? `天机紊乱（HTTP ${res.status}）`)
  return body as T
}

export const httpApi = {
  newSession: () => post<NewSessionResponse>('/api/session'),
  interact: (req: InteractRequest) => post<InteractResponse>('/api/interact', req),
}
