/**
 * [INPUT]: 依赖 react 的 useReducer / useRef / useState / useMemo / useCallback / useEffect，依赖 api/ws.ts 的 EngineSocket / storedPlayer，
 *          依赖 engineTypes.ts 的服务端帧与 PlayerStatus，依赖 view.ts 的 GameFacade / Choice / Phase / Tone，依赖 types.ts 的 ActionType
 * [OUTPUT]: 对外提供 useEngineGame() -> GameFacade（与 useGame 同形，另带 resume）
 * [POS]: hooks 的 engine 状态机，VITE_ENGINE 时取代 useGame 成为前端唯一的状态源。帧驱动：turn_resolved 开新一幕并立题记
 *        （facts，手段非寻常时附「手段 · 所图」），narration_delta 逐片累加进 scene（打字机随文本增长接着吐字），
 *        turn_completed 落定选项（角标先风险档后方向）、状态栏（时辰与名望有才显示）、人情 / 心事 / 暗流与生死；P1 与时钟字段缺省时一切照旧；
 *        断线 / 选项过期 / 连接被 Fast Refresh 关掉都进入「悄悄续局」：旧菜单作废、交互区锁进缓冲提示，quiet resume 的终帧
 *        只换回菜单、状态栏、人情心事暗流与生死（此景、题记、打字机进度与输入草稿原样保留，零大模型）
 * [PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
 */
import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from 'react'

import { EngineSocket, storedPlayer } from '../api/ws'
import type { EngineIntent, EngineOption, PlayerStatus, ServerFrame } from '../engineTypes'
import type { ActionType } from '../types'
import type { Bond, Choice, Clock, GameFacade, Phase, Pursuit, Tone } from '../view'

const NAMELESS = '无名氏'
const LOST = '与江湖失去联系，正在重连……'
const UNREACHABLE = '与江湖失去联系——engine 是否已启动于 :8001？'
// 服务端原话带内部 id（ply:…），不给玩家看
const FORGOTTEN = '前世已湮没于江湖（引擎重启后不留旧账），请重新入世。'

/** 悄悄续局的缘由：reconnect（断线重连，落定即抹去提示）/ expired（选项过期，换上新菜单后提示仍在） */
type Resync = 'reconnect' | 'expired'

interface EngineView {
  phase: Phase
  statusBar: string
  scene: string
  facts: readonly string[]
  options: EngineOption[] | null
  lastAction: string | null
  /** 上一招的手段与所图（手段寻常时为空），随题记而立 */
  manner: string | null
  bonds: readonly Bond[]
  pursuits: readonly Pursuit[]
  clocks: readonly Clock[]
  pendingAction: string | null
  turn: number
  error: string | null
  /** 悄悄续局在途（quiet resume）：终帧只换菜单、状态栏与生死，不开新一幕 */
  resync: Resync | null
}

type Event =
  | { type: 'rebirth' }
  | { type: 'request'; action: string }
  | { type: 'resync'; reason: Resync; note: string }
  | { type: 'frame'; frame: ServerFrame }
  | { type: 'fail'; error: string; code?: string }

const INITIAL: EngineView = {
  phase: 'idle',
  statusBar: '',
  scene: '',
  facts: [],
  options: null,
  lastAction: null,
  manner: null,
  bonds: [],
  pursuits: [],
  clocks: [],
  pendingAction: null,
  turn: 0,
  error: null,
  resync: null,
}

// ============================================================
//  状态栏：PlayerStatus → 与 backend 同形的「 | 」分段串，交给现成的 StatusBar
// ============================================================
const statusOf = (s: PlayerStatus) =>
  [
    `【名号：${s.name}】`,
    `【位置：${s.location}】`,
    ...(s.time ? [`【时辰：${s.time}】`] : []),
    `【境界：${s.tier}】`,
    `【伤势：${s.health}${!s.alive && s.death_cause ? `（${s.death_cause}）` : ''}】`,
    ...(s.renown ? [`【名望：${s.renown}】`] : []),
    `【武学：${s.skills.join(', ') || '不会武功'}】`,
    `【行囊：${s.inventory.join(', ') || '空无一物'}】`,
  ].join(' | ')

// ============================================================
//  人情 / 心事（P1 起下发）：态度定色——敌视血、戒备金、友善与信赖素
//  暗流（语义物理引擎起下发）：进展素，凶险金，只差一格就满的血
// ============================================================
const TONE_BY_ATTITUDE: Record<string, Tone> = { 敌视: 'risk', 戒备: 'probe' }

const clockTone = (kind: string, progress: number, maximum: number): Tone =>
  kind === '进展' ? 'calm' : maximum - progress <= 1 ? 'risk' : 'probe'

const tiesOf = (s: PlayerStatus): Pick<EngineView, 'bonds' | 'pursuits' | 'clocks'> => ({
  bonds: (s.bonds ?? []).map((b) => ({ ...b, tone: TONE_BY_ATTITUDE[b.attitude] ?? 'calm' })),
  pursuits: s.pursuits ?? [],
  clocks: (s.clocks ?? []).map((c) => ({ ...c, tone: clockTone(c.kind, c.progress, c.maximum) })),
})

/** 题记的「手段 · 所图」：手段寻常（或旧 engine 不下发）即不显示 */
const mannerOf = (intent: EngineIntent | null): string | null =>
  intent?.approach && intent.approach !== '寻常' ? [intent.approach, intent.aim].filter(Boolean).join(' · ') : null

// ============================================================
//  选项 → 通用抉择：角标与色调都先看风险档（P1 起下发），缺省按方向
// ============================================================
const TONE_BY_RISK: Record<string, Tone> = { 稳妥: 'calm', 有险: 'probe', 凶险: 'risk' }
const TONE_BY_CATEGORY: Record<string, Tone> = {
  探索: 'calm',
  休养: 'calm',
  交涉: 'probe',
  修习: 'probe',
  取物: 'probe',
  战斗: 'risk',
}

const choicesOf = (options: EngineOption[] | null): Choice[] | null =>
  options &&
  options.map((o) => ({
    key: o.risk || o.category,
    label: o.label,
    hint: o.why || undefined,
    tone: (o.risk && TONE_BY_RISK[o.risk]) || TONE_BY_CATEGORY[o.category] || 'probe',
    value: o.id,
  }))

// ============================================================
//  状态机：idle → loading → streaming → playing ⇄ … → dead
// ============================================================

/** 新一幕：打字机按 turn 重置，上一招（连同手段所图）升为题记，旧选项作废 */
const begin = (state: EngineView, facts: readonly string[], manner: string | null = null): EngineView => ({
  ...state,
  phase: 'streaming',
  scene: '',
  facts,
  options: null,
  lastAction: state.pendingAction,
  manner: state.pendingAction ? manner : null,
  pendingAction: null,
  turn: state.turn + 1,
  error: null,
  resync: null,
})

function onFrame(state: EngineView, frame: ServerFrame): EngineView {
  switch (frame.type) {
    case 'session':
      // spawn / resume 的开场：先回缓冲提示，等叙事第一片；悄悄续局则原样等终帧
      return state.resync ? state : { ...state, phase: 'loading', error: null }
    case 'turn_resolved': {
      // 驳回的招没有事件可白描：以驳回理由立题记
      const reason = frame.intent?.action_type === 'INVALID' ? frame.intent.reason : null
      return begin(state, frame.facts.length ? frame.facts : reason ? [reason] : [], mannerOf(frame.intent))
    }
    case 'narration_delta': {
      const scene = state.phase === 'streaming' ? state : begin(state, [])
      return { ...scene, scene: scene.scene + frame.text }
    }
    case 'turn_completed': {
      // 悄悄续局：此景玩家已读过，只换菜单、状态栏与生死；turn 不变，打字机进度与输入草稿因此都留着
      if (state.resync)
        return {
          ...state,
          // 断在首幕写出之前：此景是空的，就地补一句定场白（取自状态栏，不调大模型）
          scene: state.scene || `你定了定神，此刻身在${frame.status.location}。`,
          phase: frame.game_over ? 'dead' : 'playing',
          options: frame.options,
          statusBar: statusOf(frame.status),
          ...tiesOf(frame.status),
          resync: null,
          error: state.resync === 'expired' ? state.error : null,
        }
      const scene = state.phase === 'streaming' ? state : begin(state, [])
      return {
        ...scene,
        phase: frame.game_over ? 'dead' : 'playing',
        scene: frame.narration || scene.scene,
        options: frame.options,
        statusBar: statusOf(frame.status),
        ...tiesOf(frame.status),
      }
    }
    case 'error':
      return fail(state, frame.message, frame.code)
    default:
      return state // 不认得的帧（协议日后加法）：原样不动，绝不让界面崩掉
  }
}

/** 失败不吞掉局面：在途回合退回出招前（或停在已写出的半幕），开局失败则回入世页 */
function fail(state: EngineView, error: string, code?: string): EngineView {
  if (code === 'PLAYER_DEAD') return { ...state, phase: 'dead', pendingAction: null, resync: null, error }
  // 前世查无此人（engine 重启、内存账本已清）：续不上，只能回入世页重新投胎
  if (code === 'UNKNOWN_PLAYER') return { ...INITIAL, turn: state.turn, error: FORGOTTEN }
  switch (state.phase) {
    case 'loading':
      return { ...state, phase: state.statusBar ? 'playing' : 'idle', pendingAction: null, resync: null, error }
    case 'streaming':
      // 事件已入账、叙事断在半途：旧选项与新世界对不上，只留自由输入
      return { ...state, phase: 'playing', options: null, error }
    case 'dead':
      return state
    default:
      return { ...state, error }
  }
}

function reducer(state: EngineView, event: Event): EngineView {
  switch (event.type) {
    case 'rebirth':
      return { ...INITIAL, phase: 'loading', turn: state.turn }
    case 'request':
      return { ...state, phase: 'loading', pendingAction: event.action, error: null }
    case 'resync':
      // 旧菜单作废、交互区锁进缓冲提示（提示语随之显示），直到悄悄续局的终帧或失败到来；死者无局可续
      if (state.phase === 'dead') return state
      return { ...state, phase: 'loading', options: null, pendingAction: null, resync: event.reason, error: event.note }
    case 'frame':
      return onFrame(state, event.frame)
    case 'fail':
      return fail(state, event.error, event.code)
  }
}

export function useEngineGame(): GameFacade {
  const [state, dispatch] = useReducer(reducer, INITIAL)
  const [heir, setHeir] = useState(storedPlayer)
  // 同步闸门：同一帧内的两次点击只发一招；终帧、错误帧或断线才放行（重连续局在途时一直关着）
  const busy = useRef(false)
  // 名号：入世时取玩家所填，session 帧到来以服务端为准（续前缘的前世名号由此得来），重新投胎沿用
  const name = useRef(NAMELESS)
  // 本页正在玩的前世：session 帧记下，死亡 / 查无此人即抹去；连接被换掉时据此悄悄续局（不依赖 localStorage）
  const player = useRef<string | null>(null)
  const sock = useRef<EngineSocket | null>(null)

  const socket = useCallback((): EngineSocket => {
    if (sock.current) return sock.current
    // 新连接接过本页已知的前世：断线照样重连续局；新开局时为空，等 session 帧确认
    const s: EngineSocket = new EngineSocket(
      {
        onFrame: (frame) => {
          switch (frame.type) {
            case 'session':
              player.current = frame.player_id
              name.current = frame.name || name.current
              break
            case 'turn_completed':
              busy.current = false
              if (frame.game_over) bury(s)
              break
            case 'error':
              busy.current = false
              if (frame.code === 'UNKNOWN_PLAYER' || frame.code === 'PLAYER_DEAD') bury(s)
              if (frame.code === 'OPTION_EXPIRED' && player.current) {
                // 菜单与状态栏都已不合时宜：悄悄续局取回当下的（零大模型），此景不动
                busy.current = true
                dispatch({ type: 'resync', reason: 'expired', note: frame.message })
                s.resume(player.current, true)
                return
              }
          }
          dispatch({ type: 'frame', frame })
        },
        onDown: (retrying) => {
          // 重连在途：socket 自会悄悄 resume，其终帧到来前不得出招
          busy.current = retrying
          dispatch(retrying ? { type: 'resync', reason: 'reconnect', note: LOST } : { type: 'fail', error: UNREACHABLE })
        },
      },
      player.current,
    )
    /** 死者 / 查无此人：断线不再续、存储抹去、入世页不再露「续前缘」 */
    function bury(from: EngineSocket) {
      from.forget()
      player.current = null
      setHeir(null)
    }
    sock.current = s
    return s
  }, [])

  /** 连接已失而前世尚在：锁住交互区，换（或催）连接悄悄续局，菜单回来再出招 */
  const revive = useCallback(() => {
    if (!player.current) {
      dispatch({ type: 'fail', error: UNREACHABLE })
      return
    }
    busy.current = true
    dispatch({ type: 'resync', reason: 'reconnect', note: LOST })
    socket().resume(player.current, true)
  }, [socket])

  // 连接的寿命归组件：卸载即关。Fast Refresh 会先跑 cleanup 再重跑本 effect——局中有人就换一条新连接悄悄续上，
  // 免得此后每一招都对着已关的连接报「正在重连」
  useEffect(() => {
    if (player.current && !sock.current) revive()
    return () => {
      sock.current?.close()
      sock.current = null
    }
  }, [revive])

  /** 入世 / 重新投胎：名号缺省沿用上一世，再缺省「无名氏」 */
  const start = useCallback(
    (given?: string) => {
      if (busy.current) return
      busy.current = true
      if (typeof given === 'string' && given.trim()) name.current = given.trim().slice(0, 12)
      dispatch({ type: 'rebirth' })
      socket().spawn(name.current)
    },
    [socket],
  )

  /** 续前缘（页面刷新后）：此页没有此景，故请 engine 复述一遍（非 quiet） */
  const resume = useCallback(() => {
    const playerId = storedPlayer()
    if (busy.current || !playerId) return
    busy.current = true
    dispatch({ type: 'rebirth' })
    socket().resume(playerId)
  }, [socket])

  const { phase, options } = state
  const act = useCallback(
    (type: ActionType, text: string) => {
      const value = text.trim()
      if (busy.current || phase !== 'playing' || !value) return
      const label = type === 'choice' ? options?.find((o) => o.id === value)?.label : value
      if (!label) return
      const sent = type === 'choice' ? socket().choose(value) : socket().act(value)
      if (!sent) {
        // 这一招没发出去、也绝不补发：先续上局，草稿还在，玩家再出一次
        revive()
        return
      }
      busy.current = true
      dispatch({ type: 'request', action: label })
    },
    [socket, revive, phase, options],
  )

  const choices = useMemo(() => choicesOf(options), [options])

  return {
    phase: state.phase,
    statusBar: state.statusBar,
    scene: state.scene,
    facts: state.facts,
    choices,
    lastAction: state.lastAction,
    manner: state.manner,
    bonds: state.bonds,
    pursuits: state.pursuits,
    clocks: state.clocks,
    pendingAction: state.pendingAction,
    turn: state.turn,
    error: state.error,
    start,
    act,
    resume: heir ? resume : undefined,
  }
}
