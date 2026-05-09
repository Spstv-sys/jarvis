import { useEffect, useRef, useState, useCallback } from 'react'

const WS_URL = `ws://localhost:5173/ws`  // через Vite proxy

export function useJarvis() {
  const ws              = useRef(null)
  const reconnectTimer  = useRef(null)
  const intentionalClose = useRef(false)

  const [messages, setMessages] = useState([])
  const [metrics,  setMetrics]  = useState({ cpu: 0, ram: 0, battery: null, plugged: null, volume: 0 })
  const [status,   setStatus]   = useState("LOADING")
  const [provider, setProvider] = useState("—")
  const [connected, setConnected] = useState(false)
  const [thoughts, setThoughts]   = useState([])
  const [cognition, setCognition] = useState({ wm_context: '', wm_entities: [], intent_stats: {} })
  const [goalStack, setGoalStack] = useState({ active: [], history: [] })
  const [reflection, setReflection] = useState({ recent: [], stats: {}, last_run_ts: null, enabled: true })
  const [anticipator, setAnticipator] = useState({ pending: [], recent: [], stats: {}, enabled: true, interval_sec: 0 })

  const connect = useCallback(() => {
    // Не створюємо паралельне з'єднання, якщо сокет уже існує
    // (CONNECTING або OPEN). Це критично для React.StrictMode у dev,
    // де ефекти монтуються двічі та інакше з'являлися б два WS-клієнти,
    // через що сервер дублював би всі broadcast-повідомлення в UI.
    if (ws.current && ws.current.readyState <= WebSocket.OPEN) return

    intentionalClose.current = false
    const socket = new WebSocket(WS_URL)
    ws.current = socket

    socket.onopen = () => {
      setConnected(true)
      setStatus(prev => (prev && prev.toUpperCase().includes('ERROR')) ? prev : 'CONNECTED')
      clearTimeout(reconnectTimer.current)
    }

    socket.onclose = () => {
      setConnected(false)
      setStatus(prev => (prev && prev.toUpperCase().includes('ERROR')) ? prev : 'OFFLINE')
      if (ws.current === socket) ws.current = null
      if (intentionalClose.current) return
      reconnectTimer.current = setTimeout(connect, 2000)
    }

    socket.onmessage = (e) => {
      const data = JSON.parse(e.data)

      if (data.type === 'metrics') {
        setMetrics({
          cpu:     data.cpu,
          ram:     data.ram,
          battery: data.battery,
          plugged: data.plugged,
          volume:  data.volume,
        })
        return
      }

      if (data.type === 'goal_stack') {
        setGoalStack({ active: data.active || [], history: data.history || [] })
        return
      }

      if (data.type === 'reflection') {
        setReflection({
          recent:      data.recent || [],
          stats:       data.stats  || {},
          last_run_ts: data.last_run_ts ?? null,
          enabled:     data.enabled !== false,
          interval_sec: data.interval_sec || 0,
        })
        return
      }

      if (data.type === 'anticipator') {
        setAnticipator({
          pending:      data.pending || [],
          recent:       data.recent  || [],
          stats:        data.stats   || {},
          enabled:      data.enabled !== false,
          interval_sec: data.interval_sec || 0,
        })
        return
      }

      if (data.type === 'cognition') {
        setCognition({
          wm_context:   data.wm_context || '',
          wm_entities:  data.wm_entities || [],
          intent_stats: data.intent_stats || {},
        })
        return
      }

      if (data.type === 'thought') {
        setThoughts(prev => [...prev.slice(-150), {
          id:    data.ts ? `${data.ts}-${Math.random()}` : Date.now() + Math.random(),
          ts:    data.ts || (Date.now() / 1000),
          stage: data.stage || 'info',
          text:  data.text || '',
          meta:  data.meta || {},
          time:  new Date(((data.ts || (Date.now() / 1000)) * 1000)).toLocaleTimeString('uk-UA', { hour12: false }),
        }])
        return
      }

      if (data.type === 'message') {
        if (data.sender === 'STATUS') {
          setStatus(data.text)
          if (data.text.includes('інтелект:')) {
            const m = data.text.match(/інтелект:\s*(\S+)/)
            if (m) setProvider(m[1].toUpperCase())
          }
          return
        }
        setMessages(prev => [...prev.slice(-200), {
          id:     Date.now() + Math.random(),
          sender: data.sender,
          text:   data.text,
          time:   new Date().toLocaleTimeString('uk-UA', { hour12: false }),
        }])
      }
    }
  }, [])

  useEffect(() => {
    connect()
    fetch('/status')
      .then(r => r.json())
      .then(d => {
        if (d.provider) setProvider(d.provider.toUpperCase())
        if (typeof d.ready === 'boolean') {
          setStatus(d.ready ? 'SYSTEM ONLINE' : 'LOADING')
        }
      })
      .catch(() => {})

    return () => {
      clearTimeout(reconnectTimer.current)
      intentionalClose.current = true
      ws.current?.close()
      ws.current = null
    }
  }, [connect])

  const sendCommand = useCallback((text) => {
    if (!text.trim()) return
    if (ws.current?.readyState === WebSocket.OPEN) {
      ws.current.send(JSON.stringify({ type: 'command', text }))
    } else {
      fetch('/command', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ text }),
      }).catch(() => {})
    }
  }, [])

  const clearMessages = useCallback(() => setMessages([]), [])

  const uploadFile = useCallback(async (file, prompt = '') => {
    if (!file) return { ok: false, error: 'no file' }
    const fd = new FormData()
    fd.append('file', file)
    if (prompt) fd.append('prompt', prompt)
    try {
      const res = await fetch('/upload', { method: 'POST', body: fd })
      if (!res.ok) {
        const detail = await res.text().catch(() => res.statusText)
        setMessages(prev => [...prev.slice(-200), {
          id:     Date.now() + Math.random(),
          sender: 'STATUS',
          text:   `❌ Завантаження не вдалося (${res.status}): ${detail}`,
          time:   new Date().toLocaleTimeString('uk-UA', { hour12: false }),
        }])
        return { ok: false, status: res.status, error: detail }
      }
      return { ok: true, data: await res.json() }
    } catch (e) {
      return { ok: false, error: String(e) }
    }
  }, [])

  const clearThoughts = useCallback(() => setThoughts([]), [])

  const runReflection = useCallback(async (windowHours = null) => {
    try {
      const url = windowHours ? `/reflection/run?window_hours=${windowHours}` : '/reflection/run'
      const res = await fetch(url, { method: 'POST' })
      return await res.json()
    } catch (e) {
      return { error: String(e), lessons: [] }
    }
  }, [])

  const scanAnticipator = useCallback(async () => {
    try {
      const res = await fetch('/anticipator/scan', { method: 'POST' })
      return await res.json()
    } catch (e) {
      return { error: String(e), new: [] }
    }
  }, [])

  const acceptSuggestion = useCallback(async (sid) => {
    try {
      const res = await fetch(`/anticipator/${encodeURIComponent(sid)}/accept`, { method: 'POST' })
      return await res.json()
    } catch (e) {
      return { ok: false, error: String(e) }
    }
  }, [])

  const dismissSuggestion = useCallback(async (sid) => {
    try {
      const res = await fetch(`/anticipator/${encodeURIComponent(sid)}/dismiss`, { method: 'POST' })
      return await res.json()
    } catch (e) {
      return { ok: false, error: String(e) }
    }
  }, [])

  const transcribeAudio = useCallback(async (blob, filename = 'voice.webm') => {
    if (!blob) return { ok: false, error: 'no blob' }
    const fd = new FormData()
    fd.append('file', blob, filename)
    try {
      const res = await fetch('/transcribe', { method: 'POST', body: fd })
      if (!res.ok) {
        const detail = await res.text().catch(() => res.statusText)
        return { ok: false, status: res.status, error: detail }
      }
      const data = await res.json()
      return { ok: true, text: data.text || '' }
    } catch (e) {
      return { ok: false, error: String(e) }
    }
  }, [])

  return { messages, metrics, status, provider, connected, sendCommand, clearMessages, uploadFile, thoughts, clearThoughts, cognition, goalStack, reflection, runReflection, anticipator, scanAnticipator, acceptSuggestion, dismissSuggestion, transcribeAudio }
}