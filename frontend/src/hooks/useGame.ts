/**
 * [INPUT]: 依赖 react 的 useReducer / useRef / useCallback，依赖 api/http.ts 的 api 与 ApiError，依赖 types.ts 的 GameState 等协议类型
 * [OUTPUT]: 对外提供 useGame() -> { ...GameView, start, rebirth, act }、Phase 类型
 * [POS]: hooks 的视图状态机，前端唯一的状态源；App 读取它的快照，组件通过 start / rebirth / act 发出意图。
 *        它只记录服务端给出的状态树与界面阶段，不做任何世界规则计算：生死以 game_over 与错误码 dead 为准，
 *        状态栏、世界大事原样转交渲染
 * [PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
 */
import { useCallback, useReducer, useRef } from 'react'

import { api, ApiError } from '../api/http'
import type { ActionType, GameState, InteractResponse, Options } from '../types'

// ============================================================
//  视图状态机：idle（未入世）→ loading → playing ⇄ loading → dead → loading（投胎）
//  阶段转移只由服务端的裁决驱动：响应的 game_over，或错误码 dead / not_found
// ============================================================
export type Phase = 'idle' | 'loading' | 'playing' | 'dead'

interface GameView {
  phase: Phase
  sessionId: string | null
  /** 此身所在的平行世界：投胎时携带它，世界大事由服务端延续；只在开局落定时写入、此局已散时清空 */
  worldId: string | null
  /** 上一轮的 next_state 整树，下一次请求原样作为 current_state 回传（视图回显，服务端不采信） */
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
  | { type: 'resolve'; res: InteractResponse; born?: { sessionId: string; worldId: string } }
  | { type: 'reject'; error: string }
  | { type: 'perish' }
  | { type: 'vanish' }

const INITIAL: GameView = {
  phase: 'idle',
  sessionId: null,
  worldId: null,
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
      // 清空此身的一切（会话、状态树、场景），只保留所在世界与 turn 计数（确保打字机重置）
      return { ...INITIAL, phase: 'loading', worldId: state.worldId, turn: state.turn }
    case 'request':
      return { ...state, phase: 'loading', pendingAction: event.action, error: null }
    case 'resolve': {
      const { res, born } = event
      return {
        ...state,
        phase: res.game_over ? 'dead' : 'playing',
        sessionId: born?.sessionId ?? state.sessionId,
        worldId: born?.worldId ?? state.worldId,
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
      // 推演失败不吞掉这一回合：退回出招前的局面，原场景与选项原样保留；
      // 投胎失败（已无此身、仍有世界）退回投胎之门而非入世页——入世页只会开辟新世界，前世所在的世界就此失联
      return {
        ...state,
        phase: state.sessionId ? 'playing' : state.worldId ? 'dead' : 'idle',
        pendingAction: null,
        error: event.error,
      }
    case 'perish':
      // 死讯迟到：服务端早已封印此身，只是死亡响应没送到。停在最后所见的场景、交出投胎之门，
      // 不再让玩家对着 409 反复出招
      return { ...state, phase: 'dead', options: null, pendingAction: null, error: null }
    case 'vanish':
      // 会话或世界已不在服务端（重启、过期）：前端无从恢复，回到入世页另开新局
      return { ...INITIAL, turn: state.turn, error: '此局已散，请重新入世' }
  }
}

// 只翻译服务端的裁决，不自行推断：dead / not_found 改变阶段，其余错误退回出招前局面并提示
function failure(err: unknown): Event {
  if (!(err instanceof ApiError)) return { type: 'reject', error: '天机紊乱，请再试一次' }
  if (err.code === 'dead') return { type: 'perish' }
  if (err.code === 'not_found') return { type: 'vanish' }
  return { type: 'reject', error: err.message }
}

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
      dispatch(failure(err))
    } finally {
      inflight.current = false
    }
  }, [])

  /** 降生：清空此身，向服务端求一个开局；worldId 为 null 即开辟新世界 */
  const incarnate = useCallback(
    (worldId: string | null) =>
      flight(async () => {
        dispatch({ type: 'rebirth' })
        const res = await api.newSession(worldId)
        dispatch({ type: 'resolve', res, born: { sessionId: res.session_id, worldId: res.world_id } })
      }),
    [flight],
  )

  const { phase, sessionId, worldId, current } = state

  /** 入世：开辟一个新的平行世界 */
  const start = useCallback(() => incarnate(null), [incarnate])
  /** 重新投胎：在同一世界转生，前世所为由服务端留在世界大事里 */
  const rebirth = useCallback(() => incarnate(worldId), [incarnate, worldId])

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

  return { ...state, start, rebirth, act }
}
