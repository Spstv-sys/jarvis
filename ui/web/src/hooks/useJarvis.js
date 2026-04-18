import { useEffect, useRef, useState, useCallback } from 'react'

const WS_URL = `ws://localhost:5173/ws`  // через Vite proxy

export function useJarvis() {
  const ws              = useRef(null)
  const reconnectTimer  = useRef(null)

  const [messages, setMessages] = useState([])
  const [metrics,  setMetrics]  = useState({ cpu: 0, ram: 0, battery: null, plugged: null, volume: 0 })
  const [status,   setStatus]   = useState("LOADING")
  const [provider, setProvider] = useState("—")
  const [connected, setConnected] = useState(false)

  const connect = useCallback(() => {
    if (ws.current?.readyState === WebSocket.OPEN) return

    const socket = new WebSocket(WS_URL)

    socket.onopen = () => {
      setConnected(true)
      clearTimeout(reconnectTimer.current)
    }

    socket.onclose = () => {
      setConnected(false)
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

    ws.current = socket
  }, [])

  useEffect(() => {
    connect()
    fetch('/status')
      .then(r => r.json())
      .then(d => { if (d.provider) setProvider(d.provider.toUpperCase()) })
      .catch(() => {})

    return () => {
      clearTimeout(reconnectTimer.current)
      ws.current?.close()
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

  return { messages, metrics, status, provider, connected, sendCommand, clearMessages }
}