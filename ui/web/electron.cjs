const { app, BrowserWindow, ipcMain } = require('electron')
const path = require('path')

let win = null

function createWindow() {
  win = new BrowserWindow({
    width: 600,
    height: 600,
    minWidth: 400,
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
  win.loadURL('http://localhost:5173')

  const showTimer = setTimeout(() => win.show(), 3000)
  win.once('ready-to-show', () => {
    clearTimeout(showTimer)
    win.show()
  })

  ipcMain.on('win-minimize', () => win?.minimize())
  ipcMain.on('win-maximize', () => win?.isMaximized() ? win.unmaximize() : win.maximize())
  ipcMain.on('win-close', () => win?.close())
}

app.whenReady().then(() => createWindow())
app.on('window-all-closed', () => app.quit())