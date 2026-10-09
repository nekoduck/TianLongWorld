/**
 * [INPUT]: 依赖 view.ts 的 Waypoint / Tone，依赖 index.css 的 gold / ink / blood 令牌
 * [OUTPUT]: 对外提供 CompassBar 组件
 * [POS]: components 的方位导航条（engine），由 ActionPanel 嵌在抉择按钮与自定义输入之间：
 *        平面八方排成九宫格（西北 北 东北 / 西 ✦ 东 / 西南 南 东南），上下、内外、不明另起一行；同一方位多条出路在格内竖排；
 *        按钮写方位与去处（未知即「未知区域」，过长截断），悬停（title）与读屏（aria-label）给出去处、交通方式、耗时与认知；
 *        只按 tone 定色（脱身之路血、未知金、其余素），不解读方位与认知的语义；没有出路即整条不渲染（backend 永远如此）
 * [PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
 */
import type { Tone, Waypoint } from '../view'

const TONES: Record<Tone, string> = {
  calm: 'border-ink-700 text-stone-300 hover:border-stone-500',
  probe: 'border-gold-900 text-gold-300 hover:border-gold-500',
  risk: 'border-blood-900 text-blood-400 hover:border-blood-500',
}

// 九宫格：中心是你所在之处
const GRID = ['西北', '北', '东北', '西', null, '东', '西南', '南', '东南'] as const
// 九宫之外的方位：一行排开
const EXTRA = ['上', '下', '内部', '外部', '不明']

interface Props {
  waypoints?: readonly Waypoint[] | null
  onGo: (value: string) => void
}

function Go({ way, onGo }: { way: Waypoint; onGo: (value: string) => void }) {
  return (
    <button
      type="button"
      title={way.tip}
      aria-label={`往${way.direction}：${way.tip}`}
      onClick={() => onGo(way.value)}
      className={`w-full min-w-0 rounded-sm border bg-ink-900/70 px-2 py-1.5 text-center text-xs transition-colors hover:bg-ink-800 ${TONES[way.tone]}`}
    >
      <span className="block tracking-[0.3em] text-stone-500">
        {way.direction}
        {way.retreat && ' · 脱身'}
      </span>
      <span className="block truncate">{way.target}</span>
    </button>
  )
}

export function CompassBar({ waypoints, onGo }: Props) {
  if (!waypoints || waypoints.length === 0) return null
  const at = (direction: string) => waypoints.filter((w) => w.direction === direction)
  const planar = waypoints.some((w) => (GRID as readonly (string | null)[]).includes(w.direction))
  const extras = EXTRA.flatMap(at)

  return (
    <nav aria-label="方位导航" className="mx-auto mt-5 w-full max-w-sm">
      {planar && (
        <div className="grid grid-cols-3 gap-2">
          {GRID.map((direction, i) =>
            direction === null ? (
              <span key={i} aria-hidden className="flex items-center justify-center text-gold-700">
                ✦
              </span>
            ) : (
              <div key={direction} className="flex min-w-0 flex-col justify-center gap-1">
                {at(direction).map((way) => (
                  <Go key={way.value} way={way} onGo={onGo} />
                ))}
              </div>
            ),
          )}
        </div>
      )}
      {extras.length > 0 && (
        <div className={`flex flex-wrap justify-center gap-2 ${planar ? 'mt-2' : ''}`}>
          {extras.map((way) => (
            <div key={way.value} className="w-[calc(33.333%-0.34rem)] min-w-0">
              <Go way={way} onGo={onGo} />
            </div>
          ))}
        </div>
      )}
    </nav>
  )
}
