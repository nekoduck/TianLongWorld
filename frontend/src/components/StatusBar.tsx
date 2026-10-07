/**
 * [INPUT]: 依赖 index.css 的 gold / ink 令牌
 * [OUTPUT]: 对外提供 StatusBar 组件
 * [POS]: components 的顶部悬浮条，原样呈现后端渲染好的 ui_status_bar；不解析、不拼装（那是服务端的职责），break-keep 让窄屏只在标签之间折行
 * [PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
 */
export function StatusBar({ text }: { text: string }) {
  return (
    <header className="sticky top-0 z-10 border-b border-gold-900 bg-ink-950/75 px-4 py-3 backdrop-blur-md">
      <p className="text-center text-xs leading-relaxed tracking-wider break-keep text-gold-400 sm:text-sm">
        {text || <span className="text-gold-700">天龍八部 · 平行世界</span>}
      </p>
    </header>
  )
}
