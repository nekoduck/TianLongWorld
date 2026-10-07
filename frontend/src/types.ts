/**
 * [INPUT]: 无（与 backend/app/schemas.py 逐字段镜像）
 * [OUTPUT]: 对外提供 PlayerState、WorldState、GameState、Options、OptionKey、ActionType、InteractRequest、InteractResponse、NewSessionResponse
 * [POS]: frontend 的协议类型，是 api / hooks / components 共享的唯一数据形状
 * [PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md；字段变化必须与后端 schemas.py 同步
 */

/** 玩家状态：前四项是每回合重写的快照，后四本标签账由服务端记账，只认增减 */
export interface PlayerState {
  location: string
  time: string
  weather: string
  /** 生命体征：健康、轻伤、重伤濒死…… */
  health_status: string
  /** 中毒、内力枯竭、致盲……战斗判定的关键 */
  buffs_debuffs: string[]
  /** 门派、称号、性格、与核心 NPC 的恩怨 */
  social_traits: string[]
  inventory: string[]
  martial_arts: string[]
}

/** 平行世界的大事记：只增不删，至多 10 条 */
export interface WorldState {
  major_events: string[]
}

/** current_state / next_state 的完整树：每次请求必须整树回传，不可遗漏 world_state */
export interface GameState {
  player_state: PlayerState
  world_state: WorldState
}

export type OptionKey = 'A' | 'B' | 'C'
export type Options = Record<OptionKey, string>

export type ActionType = 'choice' | 'custom'

export interface InteractRequest {
  session_id: string
  current_state: GameState
  action_type: ActionType
  action_text: string
}

export interface InteractResponse {
  ui_status_bar: string
  scene_description: string
  game_over: boolean
  /** 死者没有选择：game_over 为 true 时恒为 null */
  options: Options | null
  next_state: GameState
}

export interface NewSessionResponse extends InteractResponse {
  session_id: string
}
