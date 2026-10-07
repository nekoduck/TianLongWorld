/**
 * [INPUT]: 依赖 index.css 的 gold / blood 令牌与 font-brush
 * [OUTPUT]: 对外提供 TitleScreen 组件
 * [POS]: components 的入世门槛（phase=idle）：由玩家主动点击开局，避免挂载即请求在 StrictMode 下双发、白烧一次大模型调用
 * [PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
 */
export function TitleScreen({ onStart, error }: { onStart: () => void; error: string | null }) {
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

      <button
        type="button"
        onClick={onStart}
        className="border border-gold-700 px-14 py-3 indent-[0.6em] tracking-[0.6em] text-gold-300 transition-colors hover:border-gold-400 hover:bg-gold-900"
      >
        入世
      </button>

      {error && <p className="text-sm text-blood-400">{error}</p>}
    </main>
  )
}
