/**
 * [INPUT]: 依赖 api/http.ts 的 httpApi、api/mock.ts 的 mockApi，依赖 VITE_USE_MOCK 开关
 * [OUTPUT]: 对外提供 GameApi 接口、api 单例、ApiError（转出）
 * [POS]: api 的门面：上层只依赖 GameApi 抽象，真实后端与静态 Mock 在此一处切换
 * [PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
 */
import type { InteractRequest, InteractResponse, NewSessionResponse } from '../types'
import { httpApi } from './http'
import { mockApi } from './mock'

export { ApiError } from './http'

export interface GameApi {
  newSession(): Promise<NewSessionResponse>
  interact(req: InteractRequest): Promise<InteractResponse>
}

export const api: GameApi = import.meta.env.VITE_USE_MOCK === 'true' ? mockApi : httpApi
