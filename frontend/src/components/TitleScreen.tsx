/**
 * [INPUT]: 依赖 react 的 useState，依赖 index.css 的 gold / blood / ink 令牌与 font-brush
 * [OUTPUT]: 对外提供 TitleScreen 组件
 * [POS]: components 的入世门槛（phase=idle）：由玩家主动点击开局，避免挂载即请求在 StrictMode 下双发、白烧一次大模型调用。
 *        askName（engine）时先问名号（缺省「无名氏」，回车即入世）；onResume 存在时多一个「续前缘」接回前世
 * [PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
 */
import { useState, type FormEvent } from 'react'

const NAMELESS = '无名氏'

interface Props {
  onStart: (name?: string) => void
  error: string | null
  /** engine 投胎需要名号；backend 不问 */
  askName?: boolean
  onResume?: () => void
}

export function TitleScreen({ onStart, error, askName = false, onResume }: Props) {
  const [name, setName] = useState('')

  const submit = (e: FormEvent) => {
    e.preventDefault()
    onStart(askName ? name.trim() || NAMELESS : undefined)
  }

  return (
    <main className="flex min-h-dvh flex-col items-center justify-center gap-12 px-6 text-center">
      <div className="animate-fade-in">
        <h1 className="font-brush text-7xl text-gold-300 sm:text-8xl">天龍八部</h1>
        <p className="mt-5 indent-[0.8em] text-sm tracking-[0.8em] text-gold-500">平行世界</p>
      </div>

      <p className="max-w-md text-sm leading-loose text-stone-500">
        你是江湖里一个无名小卒。
        <br />
        没有血条，没有等级，没有读档。
        <br />
        一念之差，便是生死。
      </p>

      <form onSubmit={submit} className="flex flex-col items-center gap-6">
        {askName && (
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            maxLength={12}
            enterKeyHint="go"
            aria-label="名号"
            placeholder={`名号（缺省「${NAMELESS}」）`}
            className="w-56 border-b border-ink-700 bg-transparent px-1 py-2 text-center tracking-widest text-stone-200 transition-colors placeholder:text-stone-600 focus:border-gold-500 focus:outline-none"
          />
        )}
        <button
          type="submit"
          className="border border-gold-700 px-14 py-3 indent-[0.6em] tracking-[0.6em] text-gold-300 transition-colors hover:border-gold-400 hover:bg-gold-900"
        >
          入世
        </button>
        {onResume && (
          <button
            type="button"
            onClick={onResume}
            className="text-xs tracking-[0.4em] text-stone-500 transition-colors hover:text-gold-400"
          >
            续前缘
          </button>
        )}
      </form>

      {error && <p className="text-sm text-blood-400">{error}</p>}
    </main>
  )
}
