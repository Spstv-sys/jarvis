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

  win.setTitle('J.A.R.V.I.S.')

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
}

if (!app || typeof app.whenReady !== 'function') {
  console.error('[JARVIS] Electron app API is unavailable. Start this file with Electron, not Node.js.')
  process.exit(1)
}

configureElectronPaths()
app.whenReady().then(() => createWindow())
app.on('window-all-closed', () => app.quit())