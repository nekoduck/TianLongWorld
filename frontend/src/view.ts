/**
 * [INPUT]: 依赖 types.ts 的 ActionType
 * [OUTPUT]: 对外提供 Phase、Tone、Choice、GameFacade
 * [POS]: frontend 的视图契约（前端内部，不镜像任何后端）：useGame（backend）与 useEngineGame（engine）都交付 GameFacade，
 *        App 与组件只认这里的形状——两套后端在 hooks 层收敛，视图层不知道自己连的是哪一个
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
  /** 角标：backend 为 A / B / C，engine 为方向（战斗 / 交涉 / 探索……） */
  key: string
  /** 按钮正文 */
  label: string
  /** 角标后的注：backend 为观 / 探 / 险，engine 为 why（选项为何在菜单上） */
  hint?: string
  tone: Tone
  /** 交还 act('choice', value) 的载荷：backend 为选项原文，engine 为 option id */
  value: string
}

export interface GameFacade {
  phase: Phase
  statusBar: string
  scene: string
  /** 本回合事件的白描（engine 的 turn_resolved），作为题记；backend 恒为空 */
  facts: readonly string[]
  choices: Choice[] | null
  /** 已落定的上一招，显示为场景题记 */
  lastAction: string | null
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
