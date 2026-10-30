import { useState, useEffect, useRef } from 'react'
import { useJarvis }  from './hooks/useJarvis'
import { useAccent }  from './hooks/useAccent'
import { useMicRecorder } from './hooks/useMicRecorder'
import Waveform       from './components/Waveform'
import ChatLog        from './components/ChatLog'
import ThoughtStream  from './components/ThoughtStream'
import WorkingMemoryPanel from './components/WorkingMemoryPanel'
import GoalStackPanel from './components/GoalStackPanel'
import ReflectionPanel from './components/ReflectionPanel'
import AnticipatorBanner from './components/AnticipatorBanner'
import Metrics        from './components/Metrics'
import StatusBar      from './components/StatusBar'
import TitleBar       from './components/TitleBar'
import MatrixRain     from './components/MatrixRain'

const ACCENT = '#00ff66'
const THEMES = [
  { id: 'matrix',  label: 'Matrix' },
  { id: 'classic', label: 'Classic' },
  { id: 'ice',     label: 'Ice' },
  { id: 'ember',   label: 'Ember' },
]

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
  const { authRequired, authError, authenticate, messages, metrics, status, provider, connected, sendCommand, clearMessages, uploadFile, transcribeAudio, thoughts, clearThoughts, cognition, goalStack, reflection, runReflection, anticipator, scanAnticipator, acceptSuggestion, dismissSuggestion } = useJarvis()
  const [accessKey, setAccessKey] = useState('')
  const [authPending, setAuthPending] = useState(false)
  const [input, setInput] = useState('')
  const [uploading, setUploading] = useState(false)
  const [transcribing, setTranscribing] = useState(false)
  const mic = useMicRecorder()
  const [bootVisible, setBootVisible] = useState(true)
  const [voiceActive, setVoiceActive] = useState(false)
  const [assistantSpeaking, setAssistantSpeaking] = useState(false)
  const [theme, setTheme] = useState(() => window.localStorage.getItem('jarvis-hud-theme') || 'matrix')
  const fileInputRef = useRef(null)
  const voiceHoldRef = useRef(null)
  const speakingHoldRef = useRef(null)
  const uptime = useUptime()
  const accent = useAccent(theme)

  const normalizedStatus = (status || 'LOADING').toUpperCase()
  const statusTone = normalizedStatus.includes('ERROR')
    ? 'error'
    : (!connected || normalizedStatus.includes('OFFLINE'))
        ? 'offline'
        : normalizedStatus.includes('ONLINE') || normalizedStatus.includes('CONNECTED')
            ? 'online'
            : 'loading'

  useEffect(() => {
    const id = window.setTimeout(() => setBootVisible(false), 1850)
    return () => window.clearTimeout(id)
  }, [])

  useEffect(() => {
    window.localStorage.setItem('jarvis-hud-theme', theme)
  }, [theme])

  useEffect(() => {
    if ((metrics.volume ?? 0) > 7) {
      setVoiceActive(true)
      window.clearTimeout(voiceHoldRef.current)
      voiceHoldRef.current = window.setTimeout(() => setVoiceActive(false), 220)
    }
    return () => {
      window.clearTimeout(voiceHoldRef.current)
    }
  }, [metrics.volume])

  useEffect(() => {
    const latest = messages[messages.length - 1]
    if (!latest || latest.sender === 'Степан') return
    setAssistantSpeaking(true)
    window.clearTimeout(speakingHoldRef.current)
    const duration = Math.min(2200, Math.max(800, latest.text.length * 22))
    speakingHoldRef.current = window.setTimeout(() => setAssistantSpeaking(false), duration)

    return () => {
      window.clearTimeout(speakingHoldRef.current)
    }
  }, [messages])

  const handleSend = () => {
    if (!input.trim()) return
    sendCommand(input.trim())
    setInput('')
  }

  const handleAttachClick = () => {
    if (uploading) return
    fileInputRef.current?.click()
  }

  const handleFileChange = async (e) => {
    const file = e.target.files?.[0]
    e.target.value = '' // reset so same file can be selected again
    if (!file) return
    setUploading(true)
    try {
      // Use current input as optional prompt about the file
      const result = await uploadFile(file, input.trim())
      if (result.ok) setInput('')
    } finally {
      setUploading(false)
    }
  }

  const handleMicClick = async () => {
    if (transcribing) return
    if (mic.isRecording) {
      const blob = await mic.stop()
      if (!blob || blob.size < 200) return
      setTranscribing(true)
      try {
        const ext = (blob.type.includes('ogg') ? 'ogg' : blob.type.includes('mp4') ? 'm4a' : 'webm')
        const res = await transcribeAudio(blob, `voice.${ext}`)
        if (res.ok && res.text) {
          setInput(prev => (prev ? prev + ' ' : '') + res.text)
        }
      } finally {
        setTranscribing(false)
      }
    } else {
      mic.start()
    }
  }

  return (
    <div className={[
      'hud-app',
      'hud-shell',
      voiceActive ? 'hud-shell--listening' : '',
      assistantSpeaking ? 'hud-shell--speaking' : '',
      `hud-shell--${statusTone}`,
    ].filter(Boolean).join(' ')} data-theme={theme}>

      {theme === 'matrix' && <MatrixRain />}

      <div className={`hud-startup ${bootVisible ? '' : 'hud-startup--hidden'}`}>
        <div className="hud-startup__scan" />
        <div className="hud-startup__core">
          <div className="hud-startup__eyebrow">WAKE UP, NEO...</div>
          <div className="hud-startup__title">N.E.O.</div>
          <div className="hud-startup__subline">The Matrix has you. Initializing neural uplink…</div>
          <div className="hud-startup__progress">
            <span className="hud-startup__progress-bar" />
          </div>
        </div>
      </div>

      {/* TITLE BAR */}
      <TitleBar />

      {authRequired && (
        <form className="hud-auth hud-panel" onSubmit={async event => {
          event.preventDefault()
          setAuthPending(true)
          try { await authenticate(accessKey) } finally { setAuthPending(false) }
        }}>
          <label htmlFor="access-key">Ключ доступу до Нео</label>
          <input id="access-key" className="hud-input" type="password" required maxLength={4096}
            autoComplete="off" value={accessKey}
            onChange={event => setAccessKey(event.target.value)} />
          <button type="submit" className="hud-control-button" disabled={authPending}>
            {authPending ? 'Підключення…' : 'Підключитися'}
          </button>
          {authError && <p role="alert">{authError}</p>}
        </form>
      )}

      {/* ШАПКА */}
      <div className="hud-topbar hud-app-topbar hud-reveal">
        <div className="hud-topbar__identity">
          <span className="hud-kicker">THE CONSTRUCT</span>
          <span className="hud-title">N.E.O. INTERFACE</span>
          <div className="hud-session-indicator">
            <span className="hud-meta-label">SESSION UPTIME</span>
            <span className="hud-session-value">{uptime}</span>
          </div>
        </div>
        <div className="hud-topbar__controls hud-no-drag">
          <div className="hud-theme-switcher" aria-label="HUD theme switcher">
            {THEMES.map(option => (
              <button
                key={option.id}
                type="button"
                className={`hud-theme-chip${theme === option.id ? ' is-active' : ''}`}
                onClick={() => setTheme(option.id)}
              >
                {option.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* WAVEFORM */}
      <div className="hud-panel waveform-frame hud-reveal hud-reveal-delay-1">
        <Waveform volume={metrics.volume} />
      </div>

      {/* ОСНОВНИЙ КОНТЕНТ */}
      <div className="hud-main hud-reveal hud-reveal-delay-2">
        {/* ЧАТ */}
        <div className="hud-chat-column">
          <AnticipatorBanner
            anticipator={anticipator}
            acceptSuggestion={acceptSuggestion}
            dismissSuggestion={dismissSuggestion}
            scanAnticipator={scanAnticipator}
          />
          <ChatLog messages={messages} />

          {/* ВВІД */}
          <div className="composer-row">
            <input
              ref={fileInputRef}
              type="file"
              accept=".pdf,.docx,.txt,.md,.csv,.py,.json,.log,.jpg,.jpeg,.png,.bmp"
              style={{ display: 'none' }}
              onChange={handleFileChange}
            />
            <button
              className="action-button hud-control-button hud-attach-button"
              onClick={handleAttachClick}
              disabled={uploading}
              title={uploading ? 'Завантаження...' : 'Прикріпити файл (PDF, DOCX, TXT, зображення)'}
              style={{
                color:        uploading ? '#555' : accent,
                cursor:       uploading ? 'wait' : 'pointer',
              }}
            >{uploading ? '⧗' : '📎'}</button>
            <button
              className={`action-button hud-control-button hud-mic-button${mic.isRecording ? ' is-recording' : ''}`}
              onClick={handleMicClick}
              disabled={transcribing}
              title={
                transcribing ? 'Розпізнавання...' :
                mic.isRecording ? 'Зупинити запис і відправити на розпізнавання' :
                mic.error ? `Помилка мікрофону: ${mic.error}` :
                'Голосовий ввід (Whisper)'
              }
              style={{
                color: transcribing ? '#888' : (mic.isRecording ? '#ff4d6d' : accent),
                cursor: transcribing ? 'wait' : 'pointer',
                boxShadow: mic.isRecording ? `0 0 ${6 + mic.level * 14}px rgba(255, 77, 109, ${0.35 + mic.level * 0.5})` : undefined,
              }}
            >{transcribing ? '⧗' : (mic.isRecording ? '■' : '🎤')}</button>
            <input
              className="hud-input hud-command-input"
              value={input}
              onChange={e => setInput(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && handleSend()}
              placeholder="Введіть команду..."
            />
            <button
              className="action-button hud-control-button hud-send-button"
              onClick={handleSend}
            >▶</button>
          </div>
        </div>

        {/* САЙДБАР */}
        <div className="hud-sidebar-column" style={{ display: 'flex', flexDirection: 'column', gap: 10, minWidth: 280, maxWidth: 340 }}>
          <Metrics
            metrics={metrics}
            status={status}
            provider={provider}
            connected={connected}
            statusTone={statusTone}
            msgCount={messages.length}
            onClear={clearMessages}
          />
          <WorkingMemoryPanel cognition={cognition} accent={accent} />
          <GoalStackPanel goalStack={goalStack} accent={accent} />
          <ReflectionPanel reflection={reflection} runReflection={runReflection} />
          <div style={{ flex: 1, minHeight: 240 }}>
            <ThoughtStream thoughts={thoughts} onClear={clearThoughts} accent={accent} />
          </div>
        </div>
      </div>

      {/* СТАТУС-БАР */}
      <StatusBar
        status={status}
        uptime={uptime}
        connected={connected}
        statusTone={statusTone}
        assistantSpeaking={assistantSpeaking}
      />
    </div>
  )
}
