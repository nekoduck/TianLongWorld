/**
 * [INPUT]: 依赖 index.css 的 blood 令牌与 font-brush，依赖 Tailwind 内建 stone 灰阶（全项目统一的次要文字色）
 * [OUTPUT]: 对外提供 DeathScreen 组件
 * [POS]: components 的死亡锁死层：取代整个交互区，"胜负已分 · 生死已定"之下一行"前世所为，江湖犹记"小字，
 *        再留一个红色"重新投胎"按钮。小字是世界延续的唯一提示：此身清空，世界大事留在同一个平行世界里；
 *        投胎失败时 useGame 退回此层，按钮下方显示错误，再按即在同一世界重试
 * [PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
 */
export function DeathScreen({ onRebirth, error }: { onRebirth: () => void; error: string | null }) {
  return (
    <div className="flex animate-fade-in flex-col items-center gap-6 py-6">
      <div className="flex flex-col items-center gap-3">
        <p className="font-brush text-2xl tracking-[0.4em] text-blood-500">胜负已分 · 生死已定</p>
        <p className="indent-[0.3em] text-xs tracking-[0.3em] text-stone-500">前世所为，江湖犹记</p>
      </div>
      <button
        type="button"
        onClick={onRebirth}
        autoFocus
        className="rounded-sm border border-blood-500 bg-blood-600 px-12 py-3 indent-[0.5em] text-lg tracking-[0.5em] text-stone-100 shadow-[0_0_48px_-8px] shadow-blood-500 transition-colors outline-none hover:bg-blood-500 focus-visible:ring-2 focus-visible:ring-blood-400 focus-visible:ring-offset-4 focus-visible:ring-offset-ink-950"
      >
        重新投胎
      </button>
      {error && <p className="text-sm tracking-wider text-blood-400">{error}</p>}
    </div>
  )
}
