/**
 * [INPUT]: 依赖 index.css 的 gold / ink 令牌
 * [OUTPUT]: 对外提供 StatusBar 组件
 * [POS]: components 的顶部悬浮条，呈现后端渲染好的 ui_status_bar；只按协议分隔符 " | " 切段排版，不解读段内语义
 * [PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
 */
const SEPARATOR = ' | '

export function StatusBar({ text }: { text: string }) {
  const segments = text ? text.split(SEPARATOR) : []

  return (
    <header className="sticky top-0 z-10 border-b border-gold-900 bg-ink-950/75 px-4 py-3 backdrop-blur-md">
      <p className="text-center text-xs leading-relaxed tracking-wider break-keep text-gold-400 sm:text-sm">
        {segments.length === 0 && <span className="text-gold-700">天龍八部 · 平行世界</span>}
        {/* 每段是 inline-block 原子：放不下就整段换行，不会从「【状态：」中间断开；超宽的段才在段内折行 */}
        {segments.map((segment, i) => (
          <span key={i}>
            {i > 0 && <span className="mx-1.5 text-gold-700">|</span>}
            <span className="inline-block">{segment}</span>
          </span>
        ))}
      </p>
    </header>
  )
}
