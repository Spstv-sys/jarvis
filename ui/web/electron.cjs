const electron = require('electron')
const fs = require('fs')
const os = require('os')
const path = require('path')

if (!electron || typeof electron === 'string') {
  console.error('[JARVIS] Electron main process was started in Node mode.')
  console.error('[JARVIS] Run via "npx electron ." (from ui/web) and ensure ELECTRON_RUN_AS_NODE is not set.')
  process.exit(1)
}

const { app, BrowserWindow, ipcMain } = electron

function ensureDir(dirPath) {
  try {
    fs.mkdirSync(dirPath, { recursive: true })
  } catch (_) {
    // Ignore directory creation failures here; Electron defaults will still apply.
  }
}

function configureElectronPaths() {
  const localAppData = process.env.LOCALAPPDATA || path.join(os.homedir(), 'AppData', 'Local')
  const baseDir = path.join(localAppData, 'JARVIS')
  const userDataDir = path.join(baseDir, 'electron-user-data')
  const cacheDir = path.join(baseDir, 'electron-cache')

  ensureDir(userDataDir)
  ensureDir(cacheDir)

  try {
    app.setPath('userData', userDataDir)
    app.setPath('cache', cacheDir)
    app.commandLine.appendSwitch('disk-cache-dir', cacheDir)
  } catch (_) {
    // If path override fails, keep default Electron behavior.
  }
}

let win = null

function createWindow() {
  win = new BrowserWindow({
    width: 690,
    height: 680,
    minWidth: 560,
    minHeight: 400,
    frame: false,
    resizable: true,
    show: false,
    backgroundColor: '#050505',
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      preload: path.join(__dirname, 'preload.cjs')
    }
  })

  win.setTitle('N.E.O.')

  const targetUrl = 'http://localhost:5173'
  const loadWithRetry = (attempt = 0) => {
    win.loadURL(targetUrl).catch((err) => {
      if (attempt >= 60) {
        console.error('[JARVIS] Vite did not respond in time:', err?.message || err)
        return
      }
      setTimeout(() => loadWithRetry(attempt + 1), 1000)
    })
  }
  loadWithRetry()

  // Re-try if the renderer fails to load (e.g. Vite started after Electron)
  win.webContents.on('did-fail-load', (_e, errorCode, errorDescription) => {
    if (errorCode === -3) return // ABORTED — usually means a new navigation started
    setTimeout(() => loadWithRetry(0), 1000)
  })

  const showTimer = setTimeout(() => win.show(), 3000)
  win.once('ready-to-show', () => {
    clearTimeout(showTimer)
    win.show()
  })

  ipcMain.on('win-minimize', () => win?.minimize())
  ipcMain.on('win-maximize', () => win?.isMaximized() ? win.unmaximize() : win.maximize())
  ipcMain.on('win-close', () => win?.close())

  // ---------------------------------------------------------------------
  // Backend health-watcher: якщо Jarvis-бекенд (server.py) завершився
  // (напр. голосова команда «нео стоп»), Electron теж має закритися.
  // Vite/бекенд — окремі процеси, тож без цього вікно «висне».
  // ---------------------------------------------------------------------
  startBackendWatcher()
}

function startBackendWatcher() {
  const healthUrl = 'http://127.0.0.1:8001/status'
  const checkIntervalMs = 1500
  let consecutiveFailures = 0
  // 2 провали поспіль ≈ 3 с після зникнення бекенду.
  const failuresToQuit = 2
  let backendWasUp = false

  const tick = async () => {
    let alive = false
    try {
      const ctrl = new AbortController()
      const timer = setTimeout(() => ctrl.abort(), 1500)
      const res = await fetch(healthUrl, { signal: ctrl.signal })
      clearTimeout(timer)
      alive = res.ok
    } catch (_) {
      alive = false
    }

    if (alive) {
      backendWasUp = true
      consecutiveFailures = 0
    } else if (backendWasUp) {
      consecutiveFailures += 1
      if (consecutiveFailures >= failuresToQuit) {
        console.log('[JARVIS] Backend gone — shutting down everything.')
        shutdownEverything()
        return
      }
    }
    setTimeout(tick, checkIntervalMs)
  }
  tick()
}

let shuttingDown = false
function shutdownEverything() {
  if (shuttingDown) return
  shuttingDown = true

  // 1) Вбити Vite (порт 5173) — окремий node-процес, що залишається висіти.
  killProcessOnPort(5173)
  // 1b) Вбити Python-бекенд (порт 8001) — на випадок ручного close.
  killProcessOnPort(8001)

  // 2) Закрити Electron-вікна та сам процес.
  try { if (win && !win.isDestroyed()) win.destroy() } catch (_) {}
  try { app.quit() } catch (_) {}

  // 3) Захист від «зависання» quit (Tk/IPC черги тощо).
  setTimeout(() => process.exit(0), 1500)
}

function killProcessOnPort(port) {
  try {
    const { execSync } = require('child_process')
    if (process.platform === 'win32') {
      // Шукаємо PID, що слухає LISTENING на :<port>.
      const out = execSync(`netstat -ano -p tcp`, { encoding: 'utf8' })
      const pids = new Set()
      for (const line of out.split(/\r?\n/)) {
        if (!/LISTENING/i.test(line)) continue
        const m = line.match(/:(\d+)\s+\S+\s+LISTENING\s+(\d+)/i)
        if (m && m[1] === String(port)) pids.add(m[2])
      }
      for (const pid of pids) {
        try {
          execSync(`taskkill /F /T /PID ${pid}`, { stdio: 'ignore' })
          console.log(`[JARVIS] Killed PID ${pid} on port ${port}.`)
        } catch (_) {}
      }
    } else {
      try {
        execSync(`pkill -f "vite"`, { stdio: 'ignore' })
      } catch (_) {}
    }
  } catch (e) {
    console.warn(`[JARVIS] killProcessOnPort(${port}) failed:`, e?.message || e)
  }
}

if (!app || typeof app.whenReady !== 'function') {
  console.error('[JARVIS] Electron app API is unavailable. Start this file with Electron, not Node.js.')
  process.exit(1)
}

configureElectronPaths()
app.whenReady().then(() => createWindow())
app.on('window-all-closed', () => {
  // Користувач сам закрив вікно — поклади весь стек разом.
  shutdownEverything()
})

// Спроба перехопити SIGINT/SIGTERM (Ctrl+C з консолі).
process.on('SIGINT',  () => shutdownEverything())
process.on('SIGTERM', () => shutdownEverything())