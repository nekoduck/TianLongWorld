/**
 * [INPUT]: 依赖 index.css 的 blood 令牌与 font-brush
 * [OUTPUT]: 对外提供 DeathScreen 组件
 * [POS]: components 的死亡锁死层：取代整个交互区，只留一个红色"重新投胎"按钮
 * [PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
 */
export function DeathScreen({ onRebirth }: { onRebirth: () => void }) {
  return (
    <div className="flex animate-fade-in flex-col items-center gap-6 py-6">
      <p className="font-brush text-2xl tracking-[0.4em] text-blood-500">胜负已分 · 生死已定</p>
      <button
        type="button"
        onClick={onRebirth}
        autoFocus
        className="rounded-sm border border-blood-500 bg-blood-600 px-12 py-3 indent-[0.5em] text-lg tracking-[0.5em] text-stone-100 shadow-[0_0_48px_-8px] shadow-blood-500 transition-colors outline-none hover:bg-blood-500 focus-visible:ring-2 focus-visible:ring-blood-400 focus-visible:ring-offset-4 focus-visible:ring-offset-ink-950"
      >
        重新投胎
      </button>
    </div>
  )
}
