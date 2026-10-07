/**
 * [INPUT]: 依赖 hooks/useTypewriter 的输出（由 App 透传 text / typing / onSkip）
 * [OUTPUT]: 对外提供 SceneView 组件
 * [POS]: components 的中央叙事视窗：题记（上一招）+ 逐字显现的场景；轻触即跳过打字
 * [PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
 */
interface Props {
  text: string
  typing: boolean
  lastAction: string | null
  dead: boolean
  onSkip: () => void
}

export function SceneView({ text, typing, lastAction, dead, onSkip }: Props) {
  return (
    <article
      onClick={typing ? onSkip : undefined}
      className={`w-full max-w-2xl animate-fade-in ${typing ? 'cursor-pointer' : ''}`}
    >
      {lastAction && (
        <p className="mb-8 text-center text-sm tracking-widest text-stone-500">—— 你决意「{lastAction}」 ——</p>
      )}
      <p
        className={`indent-[2em] text-lg leading-[2.1] tracking-wide whitespace-pre-line sm:text-xl ${
          dead ? 'text-blood-400' : 'text-stone-200'
        }`}
      >
        {text}
        {typing && <span className="ml-0.5 animate-breathe text-gold-400">▍</span>}
      </p>
      <p className={`mt-6 text-center text-xs text-stone-600 transition-opacity ${typing ? '' : 'opacity-0'}`}>
        轻触文字，一览全文
      </p>
    </article>
  )
}
