/**
 * [INPUT]: 依赖 react 的 useReducer / useRef / useCallback，依赖 api/client.ts 的 api 与 ApiError，依赖 types.ts 的 GameState 等协议类型
 * [OUTPUT]: 对外提供 useGame() -> { ...GameView, start, act }、Phase 类型
 * [POS]: hooks 的游戏状态机，前端唯一的状态源；App 读取它的快照，组件通过 start / act 发出意图
 * [PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
 */
import { useCallback, useReducer, useRef } from 'react'

import { api, ApiError } from '../api/client'
import type { ActionType, GameState, InteractResponse, Options } from '../types'

// ============================================================
//  状态机：idle（未入世）→ loading → playing ⇄ loading → dead
// ============================================================
export type Phase = 'idle' | 'loading' | 'playing' | 'dead'

interface GameView {
  phase: Phase
  sessionId: string | null
  /** 上一轮的 next_state 整树，下一次请求原样作为 current_state 回传 */
  current: GameState | null
  statusBar: string
  scene: string
  options: Options | null
  /** 已落定的上一招，显示为场景题记 */
  lastAction: string | null
  /** 正在推演的这一招，显示在缓冲提示里 */
  pendingAction: string | null
  /** 每次新场景 +1，驱动打字机重置（即使两幕文字恰好相同） */
  turn: number
  error: string | null
}

type Event =
  | { type: 'rebirth' }
  | { type: 'request'; action: string }
  | { type: 'resolve'; res: InteractResponse; sessionId?: string }
  | { type: 'reject'; error: string }

const INITIAL: GameView = {
  phase: 'idle',
  sessionId: null,
  current: null,
  statusBar: '',
  scene: '',
  options: null,
  lastAction: null,
  pendingAction: null,
  turn: 0,
  error: null,
}

function reducer(state: GameView, event: Event): GameView {
  switch (event.type) {
    case 'rebirth':
      // 清空前世的一切，只保留 turn 计数以确保打字机重置
      return { ...INITIAL, phase: 'loading', turn: state.turn }
    case 'request':
      return { ...state, phase: 'loading', pendingAction: event.action, error: null }
    case 'resolve': {
      const { res, sessionId } = event
      return {
        ...state,
        phase: res.game_over ? 'dead' : 'playing',
        sessionId: sessionId ?? state.sessionId,
        current: res.next_state,
        statusBar: res.ui_status_bar,
        scene: res.scene_description,
        options: res.options,
        lastAction: state.pendingAction,
        pendingAction: null,
        turn: state.turn + 1,
      }
    }
    case 'reject':
      // 推演失败不吞掉这一回合：退回出招前的局面，原场景与选项原样保留
      return { ...state, phase: state.sessionId ? 'playing' : 'idle', pendingAction: null, error: event.error }
  }
}

const describe = (err: unknown) => (err instanceof ApiError ? err.message : '天机紊乱，请再试一次')

export function useGame() {
  const [state, dispatch] = useReducer(reducer, INITIAL)
  // 同步闸门：两次点击可能落在同一帧内，phase 尚未重渲染，只能靠 ref 拦截
  const inflight = useRef(false)

  const flight = useCallback(async (task: () => Promise<void>) => {
    if (inflight.current) return
    inflight.current = true
    try {
      await task()
    } catch (err) {
      dispatch({ type: 'reject', error: describe(err) })
    } finally {
      inflight.current = false
    }
  }, [])

  /** 入世 / 重新投胎：清空上下文，请求新的开局 */
  const start = useCallback(
    () =>
      flight(async () => {
        dispatch({ type: 'rebirth' })
        const res = await api.newSession()
        dispatch({ type: 'resolve', res, sessionId: res.session_id })
      }),
    [flight],
  )

  const { phase, sessionId, current } = state
  const act = useCallback(
    (actionType: ActionType, text: string) =>
      flight(async () => {
        const action = text.trim()
        if (phase !== 'playing' || !sessionId || !current || !action) return
        dispatch({ type: 'request', action })
        const res = await api.interact({
          session_id: sessionId,
          current_state: current,
          action_type: actionType,
          action_text: action,
        })
        dispatch({ type: 'resolve', res })
      }),
    [flight, phase, sessionId, current],
  )

  return { ...state, start, act }
}
