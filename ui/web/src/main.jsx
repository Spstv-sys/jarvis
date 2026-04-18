import React    from 'react'
import ReactDOM from 'react-dom/client'
import App      from './App'

// ✅ Глобальні стилі — скидаємо браузерні відступи
const style = document.createElement('style')
style.textContent = `
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: #050505; overflow: hidden; }
  ::-webkit-scrollbar { width: 4px; }
  ::-webkit-scrollbar-track { background: #0d0d0d; }
  ::-webkit-scrollbar-thumb { background: #1a1a1a; border-radius: 2px; }
  ::-webkit-scrollbar-thumb:hover { background: #00ffcc33; }
  input::placeholder { color: #2a2a2a; }
`
document.head.appendChild(style)

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
)