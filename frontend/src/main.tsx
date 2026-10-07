/**
 * [INPUT]: 依赖 react-dom/client 的 createRoot，依赖 App 与 index.css
 * [OUTPUT]: 无导出，副作用为挂载应用到 #root
 * [POS]: frontend 的启动入口，index.html 唯一引用的脚本
 * [PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
 */
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'

import App from './App'
import './index.css'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
