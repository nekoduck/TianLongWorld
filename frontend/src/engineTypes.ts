/**
 * [INPUT]: 无（与 engine/app/presentation/protocol.py 的帧、engine/app/application/bus.py 的 PlayerStatus 逐字段镜像）
 * [OUTPUT]: 对外提供 客户端帧 SpawnFrame / ResumeFrame / ActFrame / ChooseFrame / ClientFrame，
 *           服务端帧 SessionFrame / TurnResolvedFrame / NarrationDeltaFrame / TurnCompletedFrame / ErrorFrame / ServerFrame，
 *           以及 EngineActionType、Approach、Aim、EngineIntent、Risk、EngineOption、Bond、Pursuit、ClockKind、ClockInfo、PlayerStatus
 * [POS]: frontend 的 engine 线协议类型，与 types.ts（backend 协议）并列；只被 api/ws.ts 与 hooks/useEngineGame.ts 引用。
 *        ResumeFrame.quiet 为真即悄悄续局：只回 session 与叙事为空的终帧，零大模型。
 *        P1 加法一律可缺省（旧 engine 不下发）：intent 的手段 / 所图 / 话题、option.risk、status 的人情 bonds 与心事 pursuits；
 *        语义物理引擎的加法同样可缺省：status 的眼前暗流 clocks（id 与挂处不下发）与名望 renown（语义标签）；
 *        世界心跳的加法同样可缺省：动作 THINK（沉思）、intent 的此行所为 motivation、status 的时辰 time（「第一日·辰正」）
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
export type EngineActionType =
  | 'MOVE'
  | 'OBSERVE'
  | 'THINK'
  | 'TALK'
  | 'ATTACK'
  | 'TAKE'
  | 'GIVE'
  | 'USE'
  | 'LEARN'
  | 'REST'
  | 'INVALID'

/** domain/intent.Approach：手段（寻常即未特意讲究手段） */
export type Approach = '寻常' | '武力' | '言辞' | '人情' | '计谋' | '潜行' | '借势'

/** domain/intent.Aim：所图 */
export type Aim = '制人' | '夺物' | '脱身' | '求艺' | '打探' | '结交' | '化解' | '讨要' | '示警'

/** domain/intent.PlayerIntent：意图解析的结果，只读回显 */
export interface EngineIntent {
  action_type: EngineActionType
  target_entity: string | null
  item_used: string | null
  skill_used: string | null
  narrative_style: string
  /** 仅 INVALID：驳回理由 */
  reason: string | null
  /** 手段（P1 起下发，缺省视同寻常） */
  approach?: Approach
  /** 所图（P1 起下发） */
  aim?: Aim | null
  /** 话题指称：人 / 物 / 功 / 地 / 见闻，落不了地即为空（P1 起下发） */
  topic?: string | null
  /** 此行所为（≤24 字，MOVE 时去找谁、去做什么），空串即未说（世界心跳起下发） */
  motivation?: string
}

/** 风险档：只看可裁区间最坏的一端，不露结局 */
export type Risk = '稳妥' | '有险' | '凶险'

/** application/options.ActionOption 的下发子集：意图留在服务端，前端只能点选 id */
export interface EngineOption {
  id: string
  label: string
  /** 战斗 / 交涉 / 探索 / 修习 / 取物 / 休养 */
  category: string
  /** 为何在菜单上：≤12 字的确定性短语（P0 起下发） */
  why?: string
  /** 风险档：只露区间最坏的一端（P1 起下发） */
  risk?: Risk
}

/** 人情：对玩家态度不是漠然的人（在场者优先，至多 6 条） */
export interface Bond {
  name: string
  /** 敌视 / 戒备 / 友善 / 信赖 */
  attitude: string
  /** 缘由：「你打伤其得意门徒」 */
  cause: string
}

/** 心事：未了的所图（至多 3 条） */
export interface Pursuit {
  /** 「求艺 · 白虹贯日」 */
  label: string
  /** 「口风已松；已试：言辞」 */
  note: string
}

/** domain/clocks.ClockKind：时钟种类（进展有利，其余凶险） */
export type ClockKind = '疑心' | '敌意' | '危机' | '进展'

/** bus.ClockInfo：眼前的一只叙事时钟（凶险在前、近坍缩在前，至多 4 只） */
export interface ClockInfo {
  /** 「钟灵的戒心」 */
  name: string
  kind: ClockKind
  progress: number
  /** 4 / 6 / 8 */
  maximum: number
}

/** bus.PlayerStatus：只有语义标签，没有数值（时钟的格数是叙事节拍，不是属性） */
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
  /** 人情（P1 起下发） */
  bonds?: Bond[]
  /** 心事（P1 起下发） */
  pursuits?: Pursuit[]
  /** 眼前的暗流（语义物理引擎起下发） */
  clocks?: ClockInfo[]
  /** 名望：声名狼藉 / 略有恶名 / 籍籍无名 / 小有名气 / 名动一方 / 威震江湖；空串即旧服务端未填 */
  renown?: string
  /** 时辰：「第一日·辰正」（每条命令都花时间，死者停在最后时刻）；空串即旧服务端未填（世界心跳起下发） */
  time?: string
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
