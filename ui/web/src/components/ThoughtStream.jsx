import { useEffect, useRef, useState, useMemo } from 'react'

const STAGE_META = {
  intent:   { label: 'INTENT',   color: '#7df9ff' },
  plan:     { label: 'PLAN',     color: '#ffd166' },
  replan:   { label: 'REPLAN',   color: '#ff9f1c' },
  step:     { label: 'STEP',     color: '#00ff66' },
  critic:   { label: 'CRITIC',   color: '#ef476f' },
  episodic: { label: 'EPISODE',  color: '#a78bfa' },
  wm:       { label: 'WM',       color: '#06d6a0' },
  error:    { label: 'ERROR',    color: '#ff4d4d' },
  info:     { label: 'INFO',     color: '#9aa0a6' },
}

const ALL_STAGES = Object.keys(STAGE_META)

export default function ThoughtStream({ thoughts = [], onClear, accent = '#00ff66' }) {
  const bottomRef = useRef(null)
  const [enabled, setEnabled] = useState(() => new Set(ALL_STAGES))

  const filtered = useMemo(
    () => thoughts.filter(t => enabled.has(t.stage)),
    [thoughts, enabled],
  )

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [filtered.length])

  const toggle = (stage) => {
    setEnabled(prev => {
      const next = new Set(prev)
      if (next.has(stage)) next.delete(stage); else next.add(stage)
      return next
    })
  }

  return (
    <div style={{
      display: 'flex',
      flexDirection: 'column',
      height: '100%',
      border: `1px solid ${accent}33`,
      borderRadius: 6,
      background: 'rgba(0,0,0,0.55)',
      fontFamily: 'JetBrains Mono, Consolas, monospace',
      fontSize: 12,
      overflow: 'hidden',
    }}>
      <div style={{
        padding: '6px 10px',
        borderBottom: `1px solid ${accent}33`,
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
        color: accent,
        letterSpacing: 1.5,
        textTransform: 'uppercase',
        fontSize: 11,
      }}>
        <span>▸ Thought Stream ({filtered.length}/{thoughts.length})</span>
        {onClear && (
          <button
            onClick={onClear}
            style={{
              background: 'transparent',
              border: `1px solid ${accent}55`,
              color: accent,
              padding: '2px 8px',
              fontSize: 10,
              cursor: 'pointer',
              borderRadius: 3,
            }}
          >clear</button>
        )}
      </div>

      {/* Stage filters */}
      <div style={{
        display: 'flex',
        flexWrap: 'wrap',
        gap: 4,
        padding: '4px 8px',
        borderBottom: `1px solid ${accent}22`,
      }}>
        {ALL_STAGES.map(s => {
          const m = STAGE_META[s]
          const on = enabled.has(s)
          return (
            <button key={s} onClick={() => toggle(s)} style={{
              background: on ? m.color + '22' : 'transparent',
              border: `1px solid ${on ? m.color : '#333'}`,
              color: on ? m.color : '#555',
              padding: '1px 6px',
              fontSize: 9,
              cursor: 'pointer',
              borderRadius: 2,
              fontFamily: 'inherit',
              letterSpacing: 1,
            }}>{m.label}</button>
          )
        })}
      </div>

      <div style={{ flex: 1, overflowY: 'auto', padding: '6px 10px' }}>
        {filtered.length === 0 && (
          <div style={{ opacity: 0.5, fontStyle: 'italic' }}>
            (мовчу — нічого не думаю)
          </div>
        )}
        {filtered.map(t => {
          const meta = STAGE_META[t.stage] || STAGE_META.info
          return (
            <div key={t.id} style={{
              display: 'flex',
              gap: 8,
              padding: '3px 0',
              borderBottom: '1px dashed rgba(255,255,255,0.06)',
            }}>
              <span style={{ color: '#666', minWidth: 60 }}>{t.time}</span>
              <span style={{
                color: meta.color,
                minWidth: 64,
                fontWeight: 700,
              }}>{meta.label}</span>
              <span style={{ color: '#ddd', flex: 1, wordBreak: 'break-word' }}>
                {t.text}
              </span>
            </div>
          )
        })}
        <div ref={bottomRef} />
      </div>
    </div>
  )
}
