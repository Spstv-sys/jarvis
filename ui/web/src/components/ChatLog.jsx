import { useEffect, useRef, useState } from 'react'

const ACCENT = '#00ffcc'

function useTypewriter(text, speed = 18) {
  const [displayed, setDisplayed] = useState('')
  const [done, setDone] = useState(false)

  useEffect(() => {
    setDisplayed('')
    setDone(false)
    if (!text) return
    let i = 0
    const id = setInterval(() => {
      i++
      setDisplayed(text.slice(0, i))
      if (i >= text.length) {
        clearInterval(id)
        setDone(true)
      }
    }, speed)
    return () => clearInterval(id)
  }, [text, speed])

  return { displayed, done }
}

function Message({ msg, animate }) {
  const isJarvis = msg.sender !== 'Степан'
  const { displayed, done } = useTypewriter(
    isJarvis && animate ? msg.text : null,
    18
  )

  const text = isJarvis && animate
    ? displayed
    : msg.text

  return (
    <div style={{ marginBottom: 6, lineHeight: 1.5 }}>
      <span style={{ color: '#2a2a2a', fontSize: 11 }}>[{msg.time}] </span>
      <span style={{ color: isJarvis ? ACCENT : '#cccccc', fontWeight: isJarvis ? 'bold' : 'normal' }}>
        {isJarvis ? 'JARVIS ›' : 'USER   ›'}{' '}
      </span>
      <span style={{ color: isJarvis ? '#e0fff8' : '#cccccc' }}>
        {text}
        {isJarvis && animate && !done && (
          <span style={{
            display:         'inline-block',
            width:           8,
            height:          13,
            background:      ACCENT,
            marginLeft:      2,
            verticalAlign:   'middle',
            animation:       'blink 0.7s step-end infinite',
          }} />
        )}
      </span>
    </div>
  )
}

export default function ChatLog({ messages }) {
  const bottomRef = useRef(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  return (
    <div style={{
      flex:         1,
      overflowY:    'auto',
      padding:      '12px 16px',
      fontFamily:   'Consolas, monospace',
      fontSize:     13,
      background:   '#070707',
      border:       '1px solid #1a1a1a',
      borderRadius: 8,
    }}>
      <style>{`
        @keyframes blink { 0%,100%{opacity:1} 50%{opacity:0} }
      `}</style>

      {messages.length === 0 && (
        <div style={{ color: '#222', textAlign: 'center', marginTop: 40 }}>
          — очікування команди —
        </div>
      )}

      {messages.map((msg, i) => (
        <Message
          key={msg.id}
          msg={msg}
          animate={i === messages.length - 1}
        />
      ))}
      <div ref={bottomRef} />
    </div>
  )
}