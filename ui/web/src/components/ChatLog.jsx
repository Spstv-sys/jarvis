import { memo, useEffect, useRef, useState } from 'react'

const ACCENT = '#00ff66'

function useTypewriter(text, speed = 28) {
  const [displayed, setDisplayed] = useState('')
  const [done, setDone] = useState(false)

  useEffect(() => {
    setDisplayed('')
    setDone(false)
    if (!text) return
    let i = 0
    const id = setInterval(() => {
      const chunk = Math.min(
        8,
        Math.max(2, Math.ceil(text.length / 28))
      )
      i += chunk
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

const Message = memo(function Message({ msg, animate }) {
  const isJarvis = msg.sender !== 'Степан'
  const { displayed, done } = useTypewriter(
    isJarvis && animate ? msg.text : null,
    18
  )

  const text = isJarvis && animate
    ? displayed
    : msg.text

  return (
    <div className="chat-message">
      <span className="chat-message__time">[{msg.time}] </span>
      <span className={`chat-message__speaker${isJarvis ? ' is-jarvis' : ' is-user'}`}>
        {isJarvis ? 'NEO    ›' : 'USER   ›'}{' '}
      </span>
      <span className={`chat-message__text${isJarvis ? ' is-jarvis' : ' is-user'}`}>
        {text}
        {isJarvis && animate && !done && (
          <span className="chat-cursor" />
        )}
      </span>
    </div>
  )
})

export default function ChatLog({ messages }) {
  const bottomRef = useRef(null)
  const prefersReducedMotion = useRef(
    typeof window !== 'undefined' && window.matchMedia
      ? window.matchMedia('(prefers-reduced-motion: reduce)').matches
      : false
  )

  useEffect(() => {
    bottomRef.current?.scrollIntoView({
      behavior: prefersReducedMotion.current ? 'auto' : 'smooth',
    })
  }, [messages])

  return (
    <div className="hud-panel chat-log">
      {messages.length === 0 && (
        <div className="chat-log__empty">
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