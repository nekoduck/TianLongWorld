/**
 * [INPUT]: 依赖 react 的 useState，依赖 types.ts 的 ActionType，依赖 view.ts 的 Choice / Tone
 * [OUTPUT]: 对外提供 ActionPanel 组件
 * [POS]: components 的底部交互区：通用抉择按钮（backend 为 A/B/C 三档，engine 为 3~4 招，角标注 hint）+ 自定义动作输入；
 *        只按 tone 定色、不解读选项语义；ready 为假时隐身且 inert
 * [PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
 */
import { useState, type FormEvent } from 'react'

import type { ActionType } from '../types'
import type { Choice, Tone } from '../view'

// 视觉分级：观（灰）→ 探（金）→ 险（血）
const TONES: Record<Tone, string> = {
  calm: 'border-ink-700 hover:border-stone-500',
  probe: 'border-gold-900 hover:border-gold-500',
  risk: 'border-blood-900 hover:border-blood-500',
}

interface Props {
  choices: Choice[] | null
  ready: boolean
  error: string | null
  onAct: (type: ActionType, text: string) => void
}

/** 草稿不在提交时清空：父组件以 key={turn} 挂载，新一幕到来才重置——推演失败时玩家的长句不会丢 */
export function ActionPanel({ choices, ready, error, onAct }: Props) {
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

      {choices && choices.length > 0 && (
        // 四招排成两列两行，三招一行三列
        <div className={`grid gap-3 ${choices.length === 4 ? 'sm:grid-cols-2' : 'sm:grid-cols-3'}`}>
          {choices.map(({ key, label, hint, tone, value }, i) => (
            <button
              key={i}
              type="button"
              onClick={() => onAct('choice', value)}
              className={`rounded-sm border bg-ink-900/80 px-4 py-3 text-left transition-colors hover:bg-ink-800 ${TONES[tone]}`}
            >
              <span className="mb-1 block text-xs tracking-[0.3em] text-stone-500">
                {key}
                {hint && ` · ${hint}`}
              </span>
              <span className="text-stone-200">{label}</span>
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
