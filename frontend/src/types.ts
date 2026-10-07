/**
 * [INPUT]: 无（与 backend/app/schemas.py 逐字段镜像）
 * [OUTPUT]: 对外提供 WorldState、Options、OptionKey、ActionType、InteractRequest、InteractResponse、NewSessionResponse
 * [POS]: frontend 的协议类型，是 api / hooks / components 共享的唯一数据形状
 * [PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md；字段变化必须与后端 schemas.py 同步
 */

export interface WorldState {
  location: string
  time: string
  weather: string
  /** 身体状况：伤病、饥寒、疲惫；不含物品 */
  physical_state: string
  /** 随身物品：服务端记账，只认增减，不会被大模型整体改写 */
  inventory: string[]
}

export type OptionKey = 'A' | 'B' | 'C'
export type Options = Record<OptionKey, string>

export type ActionType = 'choice' | 'custom'

export interface InteractRequest {
  session_id: string
  current_state: WorldState
  action_type: ActionType
  action_text: string
}

export interface InteractResponse {
  ui_status_bar: string
  scene_description: string
  game_over: boolean
  /** 死者没有选择：game_over 为 true 时恒为 null */
  options: Options | null
  next_state: WorldState
}

export interface NewSessionResponse extends InteractResponse {
  session_id: string
}
