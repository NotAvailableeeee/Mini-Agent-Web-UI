import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// Vite config — proxies /api/* to the FastAPI backend during dev.
// In production, the FastAPI app serves the built /dist as static files.
//
// 之前考虑过给 /api/init-trace/stream 配 selfHandleResponse + 手动
// pipe 绕开 http-proxy 缓冲,实测 Vite 自家中间件不一定透传这些选项,
// 即使能透传也容易踩坑。所以前端改成轮询 /api/init-trace/state,
// 不依赖 SSE,proxy 用默认行为即可。
export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: true,
  },
})