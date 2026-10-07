/**
 * [INPUT]: 依赖 hooks/useGame 的状态机（start / rebirth / act）、hooks/useTypewriter 的打字机，依赖 components/* 全部六个组件
 * [OUTPUT]: 对外提供 App 根组件（default export）
 * [POS]: frontend 的布局编排者：三段式（状态栏 / 叙事视窗 / 交互区）+ 入世页 + 死亡锁死；只做组合，不持有业务逻辑。
 *        入世页的"入世"开辟新世界（start），死亡层的"重新投胎"留在同一世界（rebirth）
 * [PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
 */
import { ActionPanel } from './components/ActionPanel'
import { DeathScreen } from './components/DeathScreen'
import { LoadingOracle } from './components/LoadingOracle'
import { SceneView } from './components/SceneView'
import { StatusBar } from './components/StatusBar'
import { TitleScreen } from './components/TitleScreen'
import { useGame } from './hooks/useGame'
import { useTypewriter } from './hooks/useTypewriter'

export default function App() {
  const game = useGame()
  const typer = useTypewriter(game.scene, game.turn)

  if (game.phase === 'idle') return <TitleScreen onStart={game.start} error={game.error} />

  const dead = game.phase === 'dead'
  // 死亡叙事先以血色写完，随后整个世界褪成灰烬，只剩投胎之门
  const mourning = dead && typer.done

  return (
    <div className="flex min-h-dvh flex-col">
      <div
        className={`flex flex-1 flex-col transition-[filter,opacity] duration-[1500ms] ${mourning ? 'opacity-50 grayscale' : ''}`}
      >
        <StatusBar text={game.statusBar} />
        <main className="flex flex-1 items-center justify-center px-4 py-10 sm:px-8">
          {game.phase === 'loading' ? (
            <LoadingOracle action={game.pendingAction} />
          ) : (
            <SceneView
              key={game.turn}
              text={typer.text}
              typing={!typer.done}
              lastAction={game.lastAction}
              dead={dead}
              onSkip={typer.skip}
            />
          )}
        </main>
      </div>

      <footer className="px-4 pb-8 sm:px-8">
        {dead ? (
          mourning && <DeathScreen onRebirth={game.rebirth} />
        ) : (
          <ActionPanel
            key={game.turn}
            options={game.options}
            ready={game.phase === 'playing' && typer.done}
            error={game.error}
            onAct={game.act}
          />
        )}
      </footer>
    </div>
  )
}
