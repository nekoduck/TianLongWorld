/**
 * [INPUT]: 依赖 react 的 useState / useEffect / useMemo / useCallback / useRef，依赖 matchMedia(prefers-reduced-motion)
 * [OUTPUT]: 对外提供 useTypewriter(text, resetKey) -> { text, done, skip }
 * [POS]: hooks 的叙事节奏器：逐字吐出场景描写，标点处顿挫；被 App 持有，SceneView 消费其输出、ActionPanel 等待其完成。
 *        逐字计时只随进度（shown / done）走、不随文本走：engine 流式叙事每来一片，在途的那一拍照常落下，不被重置
 * [PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

const MS_PER_GLYPH = 32
const PAUSE_FACTOR = 6 // 标点后停顿倍数：读起来像说书人换气
const PAUSES = new Set('，。！？；：、…—」』”'.split(''))

const reducedMotion = () => window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false

export function useTypewriter(text: string, resetKey: unknown) {
  // Array.from 按码点切分，不会把生僻字（代理对）劈成两半
  const glyphs = useMemo(() => Array.from(text), [text])
  // 计时 effect 从 ref 读字形：文本增长（流式叙事的每一片）不该清掉在途的那一拍，否则来片快于 32ms 时整幕冻住
  const latest = useRef(glyphs)
  useEffect(() => {
    latest.current = glyphs
  }, [glyphs])
  const [shown, setShown] = useState(0)
  const [key, setKey] = useState(resetKey)

  // 渲染期重置（而非 effect 里）：新一幕的第一帧就从零开始，不会闪现上一幕的进度
  if (key !== resetKey) {
    setKey(resetKey)
    setShown(0)
  }

  const done = shown >= glyphs.length

  useEffect(() => {
    if (done) return
    if (reducedMotion()) {
      setShown(latest.current.length)
      return
    }
    const delay = PAUSES.has(latest.current[shown - 1]) ? MS_PER_GLYPH * PAUSE_FACTOR : MS_PER_GLYPH
    const id = window.setTimeout(() => setShown((n) => n + 1), delay)
    return () => window.clearTimeout(id)
  }, [shown, done])

  const skip = useCallback(() => setShown(glyphs.length), [glyphs.length])

  return { text: glyphs.slice(0, shown).join(''), done, skip }
}
