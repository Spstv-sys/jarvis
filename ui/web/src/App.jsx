import { useState, useEffect, useRef } from 'react'
import { useJarvis }  from './hooks/useJarvis'
import Waveform       from './components/Waveform'
import ChatLog        from './components/ChatLog'
import Metrics        from './components/Metrics'
import StatusBar      from './components/StatusBar'
import TitleBar       from './components/TitleBar'

const ACCENT = '#00ffcc'

function useUptime() {
  const [uptime, setUptime] = useState('00:00:00')
  const start = useRef(Date.now())
  useEffect(() => {
    const id = setInterval(() => {
      const e = Math.floor((Date.now() - start.current) / 1000)
      const h = String(Math.floor(e / 3600)).padStart(2, '0')
      const m = String(Math.floor((e % 3600) / 60)).padStart(2, '0')
      const s = String(e % 60).padStart(2, '0')
      setUptime(`${h}:${m}:${s}`)
    }, 1000)
    return () => clearInterval(id)
  }, [])
  return uptime
}

export default function App() {
  const { messages, metrics, status, provider, connected, sendCommand, clearMessages } = useJarvis()
  const [input, setInput] = useState('')
  const uptime = useUptime()

  const handleSend = () => {
    if (!input.trim()) return
    sendCommand(input.trim())
    setInput('')
  }

  return (
    <div style={{
      display:       'flex',
      flexDirection: 'column',
      height:        '100vh',
      background:    '#050505',
      color:         ACCENT,
      fontFamily:    'Consolas, monospace',
      overflow:      'hidden',
    }}>

      {/* TITLE BAR */}
      <TitleBar />

      {/* ШАПКА */}
      <div style={{
        height:          55,
        background:      '#111',
        borderBottom:    '1px solid #1a1a1a',
        display:         'flex',
        alignItems:      'center',
        justifyContent:  'space-between',
        padding:         '0 24px',
        flexShrink:      0,
        WebkitAppRegion: 'drag',
      }}>
        <span style={{ fontSize: 18, fontWeight: 'bold', letterSpacing: 2 }}>
          ⚡ J.A.R.V.I.S. SYSTEM INTERFACE
        </span>
        <span style={{ color: '#333', fontSize: 11, WebkitAppRegion: 'no-drag' }}>
          UPTIME: {uptime}
        </span>
      </div>

      {/* WAVEFORM */}
      <div style={{
        height:       80,
        margin:       '10px 16px 0',
        background:   '#080808',
        border:       '1px solid #1a1a1a',
        borderRadius: 8,
        flexShrink:   0,
      }}>
        <Waveform volume={metrics.volume} />
      </div>

      {/* ОСНОВНИЙ КОНТЕНТ */}
      <div style={{
        flex:     1,
        display:  'flex',
        gap:      12,
        padding:  '10px 16px',
        overflow: 'hidden',
      }}>
        {/* ЧАТ */}
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 8, overflow: 'hidden' }}>
          <ChatLog messages={messages} />

          {/* ВВІД */}
          <div style={{ display: 'flex', gap: 8, flexShrink: 0 }}>
            <input
              value={input}
              onChange={e => setInput(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && handleSend()}
              placeholder="Введіть команду..."
              style={{
                flex:         1,
                background:   '#0d0d0d',
                border:       '1px solid #222',
                color:        ACCENT,
                fontFamily:   'Consolas',
                fontSize:     13,
                padding:      '10px 14px',
                borderRadius: 6,
                outline:      'none',
              }}
              onFocus={e => e.target.style.borderColor = ACCENT}
              onBlur={e  => e.target.style.borderColor = '#222'}
            />
            <button
              onClick={handleSend}
              style={{
                background:   '#111',
                border:       '1px solid #222',
                color:        ACCENT,
                fontFamily:   'Consolas',
                fontSize:     16,
                padding:      '0 18px',
                borderRadius: 6,
                cursor:       'pointer',
              }}
              onMouseEnter={e => e.target.style.background = '#003d33'}
              onMouseLeave={e => e.target.style.background = '#111'}
            >▶</button>
          </div>
        </div>

        {/* САЙДБАР */}
        <Metrics
          metrics={metrics}
          status={status}
          provider={provider}
          connected={connected}
          msgCount={messages.length}
          onClear={clearMessages}
        />
      </div>

      {/* СТАТУС-БАР */}
      <StatusBar status={status} uptime={uptime} connected={connected} />
    </div>
  )
}