import React, { useEffect, useState } from 'react'
import { apiFetch } from '../api'

const SCOPE_META = {
  skill:   { color: '#ffb347', icon: '🛠' },
  goal:    { color: '#7ad8ff', icon: '🎯' },
  routine: { color: '#a6e22e', icon: '🔁' },
  global:  { color: '#cccccc', icon: '🪞' },
  llm:     { color: '#d291ff', icon: '🧠' },
}

function fmtAgo(ts) {
  if (!ts) return '—'
  const sec = Math.max(0, Math.floor(Date.now() / 1000 - ts))
  if (sec < 60)   return `${sec}s ago`
  if (sec < 3600) return `${Math.floor(sec / 60)}m ago`
  if (sec < 86400) return `${Math.floor(sec / 3600)}h ago`
  return `${Math.floor(sec / 86400)}d ago`
}

function ConfidenceBar({ value }) {
  const v = Math.max(0, Math.min(1, value || 0))
  const color = v >= 0.75 ? '#a6e22e' : v >= 0.5 ? '#ffb347' : '#888'
  return (
    <div style={{
      height: 4, width: 60, background: '#222', borderRadius: 2,
      overflow: 'hidden', display: 'inline-block', verticalAlign: 'middle',
    }}>
      <div style={{ width: `${v * 100}%`, height: '100%', background: color }} />
    </div>
  )
}

function LessonCard({ lesson }) {
  const meta = SCOPE_META[lesson.scope] || SCOPE_META.global
  return (
    <div style={{
      borderLeft: `3px solid ${meta.color}`,
      padding: '6px 8px',
      margin: '4px 0',
      background: 'rgba(255,255,255,0.02)',
      borderRadius: 3,
      fontSize: 12,
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 3 }}>
        <span style={{ color: meta.color, fontWeight: 600 }}>
          {meta.icon} {lesson.scope}
        </span>
        <span style={{ display: 'flex', alignItems: 'center', gap: 6, color: '#888', fontSize: 10 }}>
          <ConfidenceBar value={lesson.confidence} />
          <span>{Math.round((lesson.confidence || 0) * 100)}%</span>
          <span>·</span>
          <span>{fmtAgo(lesson.ts)}</span>
        </span>
      </div>
      <div style={{ color: '#ddd', lineHeight: 1.35 }}>{lesson.text}</div>
    </div>
  )
}

export default function ReflectionPanel({ reflection, runReflection }) {
  const [busy, setBusy] = useState(false)
  const [localStats, setLocalStats] = useState(null)

  useEffect(() => {
    if (!reflection || (!reflection.recent?.length && !reflection.last_run_ts)) {
      // first paint — hydrate via REST
      apiFetch('/reflection').then(r => r.json()).then(d => {
        if (d && (d.recent || d.stats)) setLocalStats(d)
      }).catch(() => {})
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const data = (reflection && (reflection.recent?.length || reflection.last_run_ts))
    ? reflection
    : (localStats || reflection || { recent: [], stats: {}, last_run_ts: null })

  const lessons = data.recent || []
  const stats   = data.stats || {}

  const handleRun = async () => {
    if (busy) return
    setBusy(true)
    try {
      await runReflection()
    } finally {
      setBusy(false)
    }
  }

  return (
    <div style={{
      border: '1px solid #333', borderRadius: 4, padding: 8,
      background: 'rgba(0,0,0,0.25)', fontFamily: 'Consolas, monospace',
    }}>
      <div style={{
        display: 'flex', justifyContent: 'space-between', alignItems: 'center',
        marginBottom: 6,
      }}>
        <span style={{ color: 'var(--hud-accent)', fontWeight: 700, fontSize: 12 }}>
          🪞 Reflector ({lessons.length})
        </span>
        <span style={{ color: '#666', fontSize: 10 }}>
          {data.last_run_ts ? `last: ${fmtAgo(data.last_run_ts)}` : 'never'}
          {stats.runs ? ` · runs:${stats.runs}` : ''}
          {stats.lessons_total ? ` · total:${stats.lessons_total}` : ''}
        </span>
      </div>

      <button
        onClick={handleRun}
        disabled={busy}
        style={{
          width: '100%',
          padding: '4px 8px',
          marginBottom: 6,
          background: busy ? '#333' : 'var(--hud-accent-soft)',
          color: 'var(--hud-accent)',
          border: '1px solid var(--hud-border-strong)',
          fontWeight: 600,
          border: 'none',
          borderRadius: 3,
          cursor: busy ? 'wait' : 'pointer',
          fontSize: 11,
          fontFamily: 'inherit',
        }}
      >
        {busy ? '⏳ розмірковую…' : '🔄 reflect now'}
      </button>

      <div style={{ maxHeight: 220, overflowY: 'auto' }}>
        {lessons.length === 0 ? (
          <div style={{ color: '#666', fontSize: 11, fontStyle: 'italic', padding: '6px 4px' }}>
            (немає уроків — натисни «reflect now» або зачекай фоновий цикл)
          </div>
        ) : (
          lessons.map(l => <LessonCard key={l.id || `${l.ts}-${l.text.slice(0,20)}`} lesson={l} />)
        )}
      </div>
    </div>
  )
}
