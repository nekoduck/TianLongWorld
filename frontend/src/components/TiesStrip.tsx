/**
 * [INPUT]: 依赖 react 的 useState，依赖 view.ts 的 Bond / Pursuit / Tone，依赖 index.css 的 gold / ink / blood 令牌
 * [OUTPUT]: 对外提供 TiesStrip 组件
 * [POS]: components 的人情 / 心事条，紧贴 StatusBar 之下（随正文滚走，不与状态栏争 sticky）：
 *        人情「名 · 态度（缘由）」、心事「label — note」，两者皆空即整条不渲染（backend 永远如此）；
 *        宽屏常显，窄屏收成一行摘要、轻触展开——展开与否是本组件唯一的私有状态；只按 tone 定色，不解读态度语义
 * [PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
 */
import { useState } from 'react'

import type { Bond, Pursuit, Tone } from '../view'

// 态度的字色：敌视血、戒备金、其余素
const TONES: Record<Tone, string> = {
  calm: 'text-stone-400',
  probe: 'text-gold-400',
  risk: 'text-blood-400',
}

interface Props {
  bonds?: readonly Bond[]
  pursuits?: readonly Pursuit[]
}

export function TiesStrip({ bonds = [], pursuits = [] }: Props) {
  const [open, setOpen] = useState(false)
  if (bonds.length === 0 && pursuits.length === 0) return null

  const summary = [bonds.length > 0 && `人情 ${bonds.length}`, pursuits.length > 0 && `心事 ${pursuits.length}`]
    .filter(Boolean)
    .join(' · ')

  return (
    <aside aria-label="人情与心事" className="border-b border-ink-700/60 bg-ink-950/40 px-4 py-2 text-xs leading-relaxed">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className="mx-auto block tracking-widest text-gold-700 sm:hidden"
      >
        {summary} {open ? '▴' : '▾'}
      </button>

      {/* 每条是可折行的原子：中文在条内自然折行，窄屏绝不横向撑出 */}
      <div className={`${open ? 'mt-2 block' : 'hidden'} mx-auto max-w-3xl space-y-1 sm:mt-0 sm:block`}>
        {bonds.length > 0 && (
          <p className="flex min-w-0 flex-wrap gap-x-4 gap-y-1">
            <span className="shrink-0 tracking-widest text-gold-700">人情</span>
            {bonds.map((b, i) => (
              <span key={i} className="min-w-0 break-words text-stone-300">
                {b.name} · <span className={TONES[b.tone]}>{b.attitude}</span>
                {b.cause && <span className="text-stone-500">（{b.cause}）</span>}
              </span>
            ))}
          </p>
        )}
        {pursuits.length > 0 && (
          <p className="flex min-w-0 flex-wrap gap-x-4 gap-y-1">
            <span className="shrink-0 tracking-widest text-gold-700">心事</span>
            {pursuits.map((p, i) => (
              <span key={i} className="min-w-0 break-words text-gold-300">
                {p.label}
                {p.note && <span className="text-stone-500"> — {p.note}</span>}
              </span>
            ))}
          </p>
        )}
      </div>
    </aside>
  )
}
