import { useEffect, useRef } from 'react'

function MetricBar({ label, value, color }) {
  const barRef = useRef(null)
  const curRef = useRef(0)
  const rafRef = useRef(null)
  const tgtRef = useRef(value)

  useEffect(() => { tgtRef.current = value }, [value])

  useEffect(() => {
    const animate = () => {
      const cur = curRef.current
      const tgt = tgtRef.current
      const nxt = cur + (tgt - cur) * 0.12
      curRef.current = nxt
      if (barRef.current) barRef.current.style.width = `${nxt}%`
      rafRef.current = requestAnimationFrame(animate)
    }
    animate()
    return () => cancelAnimationFrame(rafRef.current)
  }, [])

  return (
    <div style={{ marginBottom: 16 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
        <span style={{ color: '#444', fontSize: 10, fontFamily: 'Consolas', letterSpacing: 1 }}>
          {label}
        </span>
        <span style={{ color: '#666', fontSize: 11, fontFamily: 'Consolas' }}>
          {value != null ? `${Math.round(value)}%` : 'N/A'}
        </span>
      </div>
      <div style={{ background: '#1a1a1a', borderRadius: 4, height: 6, overflow: 'hidden' }}>
        <div
          ref={barRef}
          style={{
            height:     '100%',
            width:      '0%',
            background: color,
            borderRadius: 4,
            boxShadow:  `0 0 6px ${color}55`,
            transition: 'none',
          }}
        />
      </div>
    </div>
  )
}

export default function Metrics({ metrics, status, provider, connected, msgCount, onClear }) {
  const { cpu, ram, battery, plugged } = metrics

  return (
    <div style={{
      width:      160,
      background: '#0d0d0d',
      border:     '1px solid #1a1a1a',
      borderRadius: 8,
      padding:    '16px 14px',
      display:    'flex',
      flexDirection: 'column',
      gap: 4,
      fontFamily: 'Consolas, monospace',
    }}>
      <MetricBar label="CPU"     value={cpu} color="#ff4444" />
      <MetricBar label="RAM"     value={ram} color="#4499ff" />
      <MetricBar
        label="BATTERY"
        value={battery}
        color="#ffee44"
      />
      {battery != null && (
        <div style={{ color: '#444', fontSize: 10, textAlign: 'right', marginTop: -10 }}>
          {plugged ? '⚡ charging' : '🔋'}
        </div>
      )}

      <div style={{ borderTop: '1px solid #1a1a1a', margin: '10px 0' }} />

      <div style={{ color: connected ? '#00ffcc' : '#ff4444', fontSize: 11, textAlign: 'center' }}>
        {connected ? '● CONNECTED' : '● OFFLINE'}
      </div>

      <div style={{ color: '#333', fontSize: 10, textAlign: 'center' }}>
        AI: {provider}
      </div>

      <div style={{ borderTop: '1px solid #1a1a1a', margin: '10px 0' }} />

      <div style={{ color: '#333', fontSize: 10, textAlign: 'center' }}>
        MSG: {msgCount}
      </div>

      <button
        onClick={onClear}
        style={{
          marginTop:   8,
          background:  'transparent',
          border:      '1px solid #2a1010',
          color:       '#ff4444',
          fontFamily:  'Consolas',
          fontSize:    11,
          padding:     '5px 0',
          borderRadius: 4,
          cursor:      'pointer',
        }}
        onMouseEnter={e => e.target.style.background = '#1a0505'}
        onMouseLeave={e => e.target.style.background = 'transparent'}
      >
        🗑 CLEAR
      </button>
    </div>
  )
}