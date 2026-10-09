/**
 * [INPUT]: 依赖 hooks/useTypewriter 的输出（由 App 透传 text / typing / onSkip）
 * [OUTPUT]: 对外提供 SceneView 组件
 * [POS]: components 的中央叙事视窗：题记（上一招 + 可选的〔手段 · 所图〕+ 可选的本回合白描 facts）+ 逐字显现的场景；轻触即跳过打字。
 *        engine 流式叙事时 typing 由 App 置真直到终帧，文本在增长时光标不灭
 * [PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
 */
interface Props {
  text: string
  typing: boolean
  lastAction: string | null
  /** 上一招的手段与所图（engine）：缺省或为空则不显示 */
  manner?: string | null
  /** 本回合事件的白描（engine）：缺省或为空则不显示 */
  facts?: readonly string[]
  dead: boolean
  onSkip: () => void
}

export function SceneView({ text, typing, lastAction, manner, facts, dead, onSkip }: Props) {
  return (
    <article
      onClick={typing ? onSkip : undefined}
      className={`w-full max-w-2xl animate-fade-in ${typing ? 'cursor-pointer' : ''}`}
    >
      {lastAction && (
        <p className="mb-8 text-center text-sm tracking-widest text-stone-500">
          —— 你决意「{lastAction}」{manner && <span className="text-gold-700">〔{manner}〕</span>} ——
        </p>
      )}
      {facts && facts.length > 0 && (
        <ul className="-mt-4 mb-8 space-y-1 text-center text-xs leading-relaxed tracking-wider text-gold-700">
          {facts.map((fact, i) => (
            <li key={i}>{fact}</li>
          ))}
        </ul>
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
