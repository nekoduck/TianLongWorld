import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// 开发期同源代理：前端代码里不出现后端地址，也免去 CORS
//   /api → backend（FastAPI HTTP，:8000）；/ws → TLBB-Engine（WebSocket /ws/play，:8001）
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      '/api': 'http://localhost:8000',
      '/ws': { target: 'ws://localhost:8001', ws: true },
    },
  },
})
