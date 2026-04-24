import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, '../../', '')
  const vitePort = parseInt(env.VITE_PORT) || 5173
  const apiPort  = parseInt(env.API_PORT)  || 8001

  return {
    plugins: [react()],
    server: {
      port: vitePort,
      strictPort: true,
      proxy: {
        '/command': `http://localhost:${apiPort}`,
        '/status':  `http://localhost:${apiPort}`,
        '/upload':  `http://localhost:${apiPort}`,
        '/ws':      { target: `ws://localhost:${apiPort}`, ws: true },
      }
    }
  }
})