/**
 * [INPUT]: 依赖浏览器 WebSocket / localStorage / location，依赖 engineTypes.ts 的 ClientFrame / ServerFrame
 * [OUTPUT]: 对外提供 EngineSocket（连接 /ws/play，发 spawn / resume / act / choose，按帧分派）、SocketHandlers、storedPlayer()
 * [POS]: api 的 engine 适配器，全项目唯一裸写 WebSocket 处；与 http.ts（backend）并列，互不知晓。
 *        同源连接走 Vite 的 /ws 代理（ws / wss 随页面协议），engine 因此不需要 CORS；player_id 记在 localStorage，
 *        断线自动重连并以它悄悄 resume（quiet：不复述此景、零大模型）——存储不可用时只是记不住前世，照常可玩
 * [PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
 */
import type { ClientFrame, ServerFrame } from '../engineTypes'

const PATH = '/ws/play'
const STORE_KEY = 'tlbb.engine.player_id'
const BACKOFF_MS = [1000, 2000, 4000, 8000] // 重连间隔，封顶 8s 后一直按 8s 重试

// ============================================================
//  存储：每一次读写都可能抛（隐私模式、禁用站点数据），一律吞掉
// ============================================================
export function storedPlayer(): string | null {
  try {
    return window.localStorage.getItem(STORE_KEY)
  } catch {
    return null
  }
}

function remember(playerId: string | null) {
  try {
    if (playerId) window.localStorage.setItem(STORE_KEY, playerId)
    else window.localStorage.removeItem(STORE_KEY)
  } catch {
    // 记不住前世而已，不影响这一世
  }
}

const endpoint = () => `${window.location.protocol === 'https:' ? 'wss' : 'ws'}://${window.location.host}${PATH}`

export interface SocketHandlers {
  onFrame: (frame: ServerFrame) => void
  /** 连接断开：retrying 为真表示有前世可续，socket 会自行重连并悄悄 resume（quiet）；为假则等下一次 spawn / resume 再连 */
  onDown: (retrying: boolean) => void
}

export class EngineSocket {
  private readonly handlers: SocketHandlers
  private ws: WebSocket | null = null
  /** 只在连接建立前暂存 spawn / resume；断线即清空，绝不在重连后补发一招 */
  private outbox: ClientFrame[] = []
  private playerId: string | null = null
  private attempt = 0
  private timer: number | undefined
  private closed = false

  /** playerId：本页已在玩的前世（换连接时由上层交接），断线照样重连续局；新开局留空，等 session 帧确认 */
  constructor(handlers: SocketHandlers, playerId: string | null = null) {
    this.handlers = handlers
    this.playerId = playerId
  }

  // ============================================================
  //  上行：开局帧可排队等连接，行动帧只在连接就绪时发出
  // ============================================================
  spawn(name: string, location?: string) {
    this.enqueue(location ? { type: 'spawn', name, location } : { type: 'spawn', name })
  }

  /** quiet：悄悄续局，只取回当下的选项与状态（叙事为空，零大模型）；缺省则复述此景（页面刷新后的「续前缘」） */
  resume(playerId: string, quiet = false) {
    this.enqueue(quiet ? { type: 'resume', player_id: playerId, quiet } : { type: 'resume', player_id: playerId })
  }

  /** 返回 false 表示断线中、未发出：上层据此提示，玩家稍后再出招 */
  act(text: string): boolean {
    return this.deliver({ type: 'act', text })
  }

  choose(optionId: string): boolean {
    return this.deliver({ type: 'choose', option_id: optionId })
  }

  /** 死者无可续之前世：断线不再 resume，存储里的 player_id 一并抹去 */
  forget() {
    this.playerId = null
    remember(null)
  }

  close() {
    this.closed = true
    window.clearTimeout(this.timer)
    this.ws?.close()
    this.ws = null
  }

  // ============================================================
  //  连接生命周期
  // ============================================================
  private deliver(frame: ClientFrame): boolean {
    if (this.ws?.readyState !== WebSocket.OPEN) return false
    this.ws.send(JSON.stringify(frame))
    return true
  }

  private enqueue(frame: ClientFrame) {
    if (this.deliver(frame)) return
    this.outbox.push(frame)
    this.connect()
  }

  private connect() {
    const state = this.ws?.readyState
    if (state === WebSocket.CONNECTING || state === WebSocket.OPEN) return
    window.clearTimeout(this.timer)
    this.closed = false

    const ws = new WebSocket(endpoint())
    this.ws = ws
    // 每个回调都先验明正身：旧连接的迟到事件不得搅动新连接
    ws.onopen = () => {
      if (this.ws !== ws) return
      this.attempt = 0
      const heir = this.playerId ?? storedPlayer()
      const opening = this.outbox.some((f) => f.type === 'spawn' || f.type === 'resume')
      // 重连续局一律悄悄：此景玩家已读过，不再花一次大模型复述
      if (heir && !opening) ws.send(JSON.stringify({ type: 'resume', player_id: heir, quiet: true } satisfies ClientFrame))
      for (const frame of this.outbox.splice(0)) ws.send(JSON.stringify(frame))
    }
    ws.onmessage = (e: MessageEvent) => {
      if (this.ws !== ws) return
      let frame: ServerFrame
      try {
        frame = JSON.parse(String(e.data)) as ServerFrame
      } catch {
        return
      }
      if (frame.type === 'session') {
        this.playerId = frame.player_id
        remember(frame.player_id)
      }
      this.handlers.onFrame(frame)
    }
    ws.onclose = () => {
      if (this.ws !== ws) return
      this.ws = null
      this.outbox = []
      const retrying = !this.closed && this.playerId !== null
      this.handlers.onDown(retrying)
      if (!retrying) return
      const delay = BACKOFF_MS[Math.min(this.attempt++, BACKOFF_MS.length - 1)]
      this.timer = window.setTimeout(() => this.connect(), delay)
    }
  }
}
