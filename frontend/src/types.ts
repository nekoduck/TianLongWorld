/**
 * [INPUT]: 无（与 backend/app/schemas.py 逐字段镜像）
 * [OUTPUT]: 对外提供 PlayerState、WorldEvent、WorldState、GameState、Options、OptionKey、ActionType、
 *           NewSessionRequest、InteractRequest、InteractResponse、NewSessionResponse
 * [POS]: frontend 的协议类型，是 api / hooks / components 共享的唯一数据形状；只描述服务端给出的状态树，
 *        不携带任何规则——前端是状态视图的渲染层，这里没有一个字段需要前端计算
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

/** 世界大事：一句话原子事实 + 实体标签（地点、人物、门派），由服务端检索路由 */
export interface WorldEvent {
  /** 1~6 个实体标签 */
  tags: string[]
  /** 如：玩家抢走了段誉的折扇 */
  event_desc: string
}

/** 平行世界的大事记：只追加、跨投胎延续——投胎清空 player_state，world_state 原样留在世界里 */
export interface WorldState {
  major_events: WorldEvent[]
}

/** current_state / next_state 的完整树：前端整树保存、整树回传，从不改写其中任何字段 */
export interface GameState {
  player_state: PlayerState
  world_state: WorldState
}

export type OptionKey = 'A' | 'B' | 'C'
export type Options = Record<OptionKey, string>

export type ActionType = 'choice' | 'custom'

/** 开局：world_id 为 null 开辟新世界；携带则在该世界重新投胎，世界大事延续 */
export interface NewSessionRequest {
  world_id: string | null
}

export interface InteractRequest {
  session_id: string
  /** 上一轮 next_state 的原样回显：仅是客户端视图，服务端以自己的状态为唯一权威，从不采信 */
  current_state: GameState
  action_type: ActionType
  action_text: string
}

export interface InteractResponse {
  /** 服务端从 next_state 确定性渲染的状态栏，前端只排版不解读 */
  ui_status_bar: string
  scene_description: string
  game_over: boolean
  /** 死者没有选择：game_over 为 true 时恒为 null */
  options: Options | null
  next_state: GameState
}

export interface NewSessionResponse extends InteractResponse {
  session_id: string
  /** 此身所在的平行世界：死后携带它投胎，前世所为留在世界里 */
  world_id: string
}
