/**
 * [INPUT]: 依赖 hooks/useGame（backend）或 hooks/useEngineGame（engine）的状态机——模块级按 VITE_ENGINE 二选一，
 *          依赖 hooks/useTypewriter 的打字机，依赖 view.ts 的 GameFacade，依赖 components/* 全部七个组件
 * [OUTPUT]: 对外提供 App 根组件（default export）
 * [POS]: frontend 的布局编排者：三段式（状态栏 + 人情心事条 / 叙事视窗 / 交互区）+ 入世页 + 死亡锁死；只做组合，不持有业务逻辑。
 *        engine 流式叙事时打字机随文本增长接着吐字（resetKey 每回合才变）；可交互 = 终帧已到（playing）且打字机已追平；
 *        缓冲期间的 error（engine 断线重连、选项过期）随缓冲提示显示，交互区此时隐身
 * [PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
 */
import { ActionPanel } from './components/ActionPanel'
import { DeathScreen } from './components/DeathScreen'
import { LoadingOracle } from './components/LoadingOracle'
import { SceneView } from './components/SceneView'
import { StatusBar } from './components/StatusBar'
import { TiesStrip } from './components/TiesStrip'
import { TitleScreen } from './components/TitleScreen'
import { useEngineGame } from './hooks/useEngineGame'
import { useGame } from './hooks/useGame'
import { useTypewriter } from './hooks/useTypewriter'
import type { GameFacade } from './view'

// 构建期开关：整个应用生命周期只用一个 hook，Hooks 调用顺序因此恒定
const ENGINE = import.meta.env.VITE_ENGINE === 'true'
const useWorld: () => GameFacade = ENGINE ? useEngineGame : useGame

export default function App() {
  const game = useWorld()
  const typer = useTypewriter(game.scene, game.turn)

  if (game.phase === 'idle')
    return <TitleScreen onStart={game.start} error={game.error} askName={ENGINE} onResume={game.resume} />

  const dead = game.phase === 'dead'
  // 死亡叙事先以血色写完，随后整个世界褪成灰烬，只剩投胎之门
  const mourning = dead && typer.done

  return (
    <div className="flex min-h-dvh flex-col">
      <div
        className={`flex flex-1 flex-col transition-[filter,opacity] duration-[1500ms] ${mourning ? 'opacity-50 grayscale' : ''}`}
      >
        <StatusBar text={game.statusBar} />
        <TiesStrip bonds={game.bonds} pursuits={game.pursuits} />
        <main className="flex flex-1 items-center justify-center px-4 py-10 sm:px-8">
          {game.phase === 'loading' ? (
            <LoadingOracle action={game.pendingAction} note={game.error} />
          ) : (
            <SceneView
              key={game.turn}
              text={typer.text}
              typing={!typer.done || game.phase === 'streaming'}
              lastAction={game.lastAction}
              manner={game.manner}
              facts={game.facts}
              dead={dead}
              onSkip={typer.skip}
            />
          )}
        </main>
      </div>

      <footer className="px-4 pb-8 sm:px-8">
        {dead ? (
          mourning && <DeathScreen onRebirth={() => game.start()} />
        ) : (
          <ActionPanel
            key={game.turn}
            choices={game.choices}
            ready={game.phase === 'playing' && typer.done}
            error={game.error}
            onAct={game.act}
          />
        )}
      </footer>
    </div>
  )
}
