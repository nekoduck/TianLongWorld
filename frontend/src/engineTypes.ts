/**
 * [INPUT]: 无（与 engine/app/presentation/protocol.py 的帧、engine/app/application/bus.py 的 PlayerStatus 逐字段镜像）
 * [OUTPUT]: 对外提供 客户端帧 SpawnFrame / ResumeFrame / ActFrame / ChooseFrame / ClientFrame，
 *           服务端帧 SessionFrame / TurnResolvedFrame / NarrationDeltaFrame / TurnCompletedFrame / ErrorFrame / ServerFrame，
 *           以及 EngineActionType、EngineIntent、EngineOption、PlayerStatus
 * [POS]: frontend 的 engine 线协议类型，与 types.ts（backend 协议）并列；只被 api/ws.ts 与 hooks/useEngineGame.ts 引用。
 *        ResumeFrame.quiet 为真即悄悄续局：只回 session 与叙事为空的终帧，零大模型
 * [PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md；字段变化必须与 engine 的 protocol.py / bus.py 同步
 */

// ============================================================
//  客户端帧：WebSocket /ws/play 上行
// ============================================================
export interface SpawnFrame {
  type: 'spawn'
  /** 名号：1~12 字 */
  name: string
  /** 投胎地点（id 或地名）；留空由图谱确定性分配 */
  location?: string | null
}

export interface ResumeFrame {
  type: 'resume'
  player_id: string
  /** 悄悄续局（断线重连、选项过期）：只回 session 与叙事为空的终帧（当下选项、状态、生死），不复述此景、不调大模型；缺省 false */
  quiet?: boolean
}

export interface ActFrame {
  type: 'act'
  /** 自由文本：1~200 字 */
  text: string
}

export interface ChooseFrame {
  type: 'choose'
  option_id: string
}

export type ClientFrame = SpawnFrame | ResumeFrame | ActFrame | ChooseFrame

// ============================================================
//  回合载荷
// ============================================================
export type EngineActionType = 'MOVE' | 'OBSERVE' | 'TALK' | 'ATTACK' | 'TAKE' | 'GIVE' | 'LEARN' | 'REST' | 'INVALID'

/** domain/intent.PlayerIntent：意图解析的结果，只读回显 */
export interface EngineIntent {
  action_type: EngineActionType
  target_entity: string | null
  item_used: string | null
  skill_used: string | null
  narrative_style: string
  /** 仅 INVALID：驳回理由 */
  reason: string | null
}

/** application/options.ActionOption 的下发子集：意图留在服务端，前端只能点选 id */
export interface EngineOption {
  id: string
  label: string
  /** 战斗 / 交涉 / 探索 / 修习 / 取物 / 休养 */
  category: string
  /** 为何在菜单上：≤12 字的确定性短语（P0 起下发） */
  why?: string
  /** 风险档：只露区间最坏的一端（P1 起下发） */
  risk?: string
}

/** bus.PlayerStatus：只有语义标签，没有数值 */
export interface PlayerStatus {
  name: string
  location: string
  /** 火候折算后的境界 */
  tier: string
  /** 安然无恙 / 轻伤 / 重伤 / 奄奄一息 / 气绝 */
  health: string
  alive: boolean
  death_cause: string | null
  inventory: string[]
  /** 「北冥神功（略有小成）」：武学连同火候 */
  skills: string[]
}

// ============================================================
//  服务端帧：spawn / resume → session · [narration_delta…] · turn_completed
//            resume(quiet) → session · turn_completed（narration 为空）
//            act / choose  → turn_resolved · [narration_delta…] · turn_completed
//            任一帧出错只回 error，连接不断
// ============================================================
export interface SessionFrame {
  type: 'session'
  player_id: string
  name: string
}

export interface TurnResolvedFrame {
  type: 'turn_resolved'
  intent: EngineIntent | null
  /** 本回合事件的白描，先于叙事送达 */
  facts: string[]
}

export interface NarrationDeltaFrame {
  type: 'narration_delta'
  text: string
}

export interface TurnCompletedFrame {
  type: 'turn_completed'
  narration: string
  options: EngineOption[]
  status: PlayerStatus
  game_over: boolean
}

export interface ErrorFrame {
  type: 'error'
  /** BAD_FRAME / UNKNOWN_PLAYER / PLAYER_DEAD / OPTION_EXPIRED / LLM_ERROR / INTERNAL …… */
  code: string
  message: string
}

export type ServerFrame = SessionFrame | TurnResolvedFrame | NarrationDeltaFrame | TurnCompletedFrame | ErrorFrame
