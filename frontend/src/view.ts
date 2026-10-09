/**
 * [INPUT]: 依赖 types.ts 的 ActionType
 * [OUTPUT]: 对外提供 Phase、Tone、Choice、Bond、Pursuit、Clock、Waypoint、GameFacade
 * [POS]: frontend 的视图契约（前端内部，不镜像任何后端）：useGame（backend）与 useEngineGame（engine）都交付 GameFacade，
 *        App 与组件只认这里的形状——两套后端在 hooks 层收敛，视图层不知道自己连的是哪一个；
 *        人情 / 心事 / 暗流 / 手段所图 / 方位导航是 engine 才有的可选项，backend 不交付即不显示
 * [PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
 */
import type { ActionType } from './types'

// ============================================================
//  状态机：idle（未入世）→ loading → [streaming] → playing ⇄ … → dead
//  streaming 只属于 engine：叙事逐片到达，场景已在书写而终帧未至
// ============================================================
export type Phase = 'idle' | 'loading' | 'streaming' | 'playing' | 'dead'

/** 抉择的视觉分级：观（灰）→ 探（金）→ 险（血） */
export type Tone = 'calm' | 'probe' | 'risk'

export interface Choice {
  /** 角标：backend 为 A / B / C，engine 为战术维度的徽记（激化 / 诡道 / 化解 / 旁观） */
  key: string
  /** 按钮正文：backend 为选项原文，engine 为风味文案 flavor_text */
  label: string
  /** 角标后的注：backend 为观 / 探 / 险，engine 不写（缘由与风险进悬停提示） */
  hint?: string
  /** 悬停提示：engine 为「why · 风险档」 */
  tip?: string
  tone: Tone
  /** 交还 act('choice', value) 的载荷：backend 为选项原文，engine 为 option id */
  value: string
}

/** 方位导航的一项（engine）：方位按钮上写方位与去处，悬停看交通方式、耗时与认知；tone 由状态 hook 定（脱身之路血、未知金、其余素） */
export interface Waypoint {
  /** 方位：东 / 南 / … / 上 / 下 / 内部 / 外部 / 不明 */
  direction: string
  /** 去处：认得即其名，否则「未知区域」 */
  target: string
  /** 悬停提示：「步行 · 约一个时辰 · 问路得知」 */
  tip: string
  /** 仇人在侧时的脱身之路 */
  retreat: boolean
  tone: Tone
  /** 交还 act('choice', value) 的载荷：导航项的 id */
  value: string
}

/** 人情一条：「名 · 态度（缘由）」；tone 由状态 hook 按态度定（敌视血、戒备金、友善与信赖素），组件不解读态度 */
export interface Bond {
  name: string
  attitude: string
  cause: string
  tone: Tone
}

/** 心事一条：「label — note」 */
export interface Pursuit {
  label: string
  note: string
}

/** 暗流一只：「疑心·钟灵的戒心 ▮▮▯▯」；tone 由状态 hook 定（进展素、凶险金、只差一格血），组件不解读种类 */
export interface Clock {
  name: string
  kind: string
  progress: number
  maximum: number
  tone: Tone
}

export interface GameFacade {
  phase: Phase
  statusBar: string
  scene: string
  /** 本回合事件的白描（engine 的 turn_resolved），作为题记；backend 恒为空 */
  facts: readonly string[]
  choices: Choice[] | null
  /** 方位导航（engine）：缺省或为空则不显示 */
  waypoints?: readonly Waypoint[] | null
  /** 已落定的上一招，显示为场景题记 */
  lastAction: string | null
  /** 上一招解析出的手段与所图（「言辞 · 求艺」），手段寻常时为空；附在题记后（engine） */
  manner?: string | null
  /** 人情、心事与暗流（engine）：缺省或为空则不显示 */
  bonds?: readonly Bond[]
  pursuits?: readonly Pursuit[]
  clocks?: readonly Clock[]
  /** 正在推演的这一招，显示在缓冲提示里 */
  pendingAction: string | null
  /** 每次新场景 +1，驱动打字机与面板重置 */
  turn: number
  error: string | null
  /** 入世 / 重新投胎；engine 以名号投胎，backend 忽略参数 */
  start: (name?: string) => void
  act: (type: ActionType, text: string) => void
  /** 存有前世（engine 的 player_id）时可续：TitleScreen 据此露出「续前缘」 */
  resume?: () => void
}
