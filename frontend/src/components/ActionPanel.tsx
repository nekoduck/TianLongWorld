/**
 * [INPUT]: 依赖 react 的 useState，依赖 types.ts 的 Options / OptionKey / ActionType
 * [OUTPUT]: 对外提供 ActionPanel 组件
 * [POS]: components 的底部交互区：A/B/C 三档抉择 + 自定义动作输入；ready 为假时隐身且 inert
 * [PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
 */
import { useState, type FormEvent } from 'react'

import type { ActionType, OptionKey, Options } from '../types'

// 三档风险的视觉分级：观（灰）→ 探（金）→ 险（血）
const TIERS: { key: OptionKey; label: string; tone: string }[] = [
  { key: 'A', label: '观', tone: 'border-ink-700 hover:border-stone-500' },
  { key: 'B', label: '探', tone: 'border-gold-900 hover:border-gold-500' },
  { key: 'C', label: '险', tone: 'border-blood-900 hover:border-blood-500' },
]

interface Props {
  options: Options | null
  ready: boolean
  error: string | null
  onAct: (type: ActionType, text: string) => void
}

/** 草稿不在提交时清空：父组件以 key={turn} 挂载，新一幕到来才重置——推演失败时玩家的长句不会丢 */
export function ActionPanel({ options, ready, error, onAct }: Props) {
  const [draft, setDraft] = useState('')

  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (draft.trim()) onAct('custom', draft)
  }

  return (
    <section
      aria-label="行动"
      inert={!ready}
      className={`mx-auto w-full max-w-3xl transition-opacity duration-700 ${ready ? '' : 'pointer-events-none opacity-0'}`}
    >
      {error && <p className="mb-4 text-center text-sm text-blood-400">{error}</p>}

      {options && (
        <div className="grid gap-3 sm:grid-cols-3">
          {TIERS.map(({ key, label, tone }) => (
            <button
              key={key}
              type="button"
              onClick={() => onAct('choice', options[key])}
              className={`rounded-sm border bg-ink-900/80 px-4 py-3 text-left transition-colors hover:bg-ink-800 ${tone}`}
            >
              <span className="mb-1 block text-xs tracking-[0.3em] text-stone-500">
                {key} · {label}
              </span>
              <span className="text-stone-200">{options[key]}</span>
            </button>
          ))}
        </div>
      )}

      <form onSubmit={submit} className="mt-5">
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          maxLength={200}
          enterKeyHint="send"
          placeholder="或者，写下你自己的抉择……（回车出招）"
          className="w-full border-b border-ink-700 bg-transparent px-1 py-3 text-stone-200 transition-colors placeholder:text-stone-600 focus:border-gold-500 focus:outline-none"
        />
      </form>
    </section>
  )
}
