/**
 * [INPUT]: 依赖 react 的 useState / useEffect
 * [OUTPUT]: 对外提供 LoadingOracle 组件
 * [POS]: components 的缓冲提示，等待导演推演期间占据中央视窗；以"电光火石之间……"开篇，轮转谶语
 * [PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
 */
import { useEffect, useState } from 'react'

const OMENS = ['电光火石之间……', '风声骤紧，杀机暗伏……', '江湖风云，瞬息万变……', '天机流转，因果将定……']
const ROTATE_MS = 1800

export function LoadingOracle({ action }: { action: string | null }) {
  const [index, setIndex] = useState(0)

  useEffect(() => {
    const id = window.setInterval(() => setIndex((i) => (i + 1) % OMENS.length), ROTATE_MS)
    return () => window.clearInterval(id)
  }, [])

  return (
    <div role="status" aria-live="polite" className="text-center">
      {action && <p className="mb-8 text-sm tracking-widest text-stone-500">你决意「{action}」</p>}
      <p key={index} className="animate-fade-in font-brush text-3xl text-gold-400 sm:text-4xl">
        {OMENS[index]}
      </p>
    </div>
  )
}
