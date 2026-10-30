import { useEffect, useState } from 'react'
import { apiFetch } from '../api'

const SOURCE_META = {
  routine:      { icon: '🔁', label: 'рутина',  color: '#a6e22e' },
  followup:     { icon: '↻',  label: 'follow-up', color: '#7ad8ff' },
  time_pattern: { icon: '⏰', label: 'час',     color: '#ffb347' },
  system:       { icon: '⚠',  label: 'система', color: '#ff5555' },
}

const PRIORITY_LABEL = { 1: 'низький', 2: 'звичайний', 3: 'високий', 4: 'терміновий' }

function fmtAgo(ts) {
  if (!ts) return ''
  const sec = Math.max(0, Math.floor(Date.now() / 1000) - ts)
  if (sec < 60) return `${sec}с тому`
  const m = Math.floor(sec / 60)
  if (m < 60) return `${m}хв тому`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}год тому`
  return `${Math.floor(h / 24)}д тому`
}

function fmtTtl(expires_at) {
  if (!expires_at) return ''
  const left = expires_at - Math.floor(Date.now() / 1000)
  if (left <= 0) return 'минула'
  if (left < 60) return `${left}с до закінчення`
  const m = Math.floor(left / 60)
  return `≤${m}хв`
}

export default function AnticipatorBanner({
  anticipator,
  acceptSuggestion,
  dismissSuggestion,
  scanAnticipator,
}) {
  const [expanded, setExpanded] = useState(false)
  const [busy, setBusy] = useState(null) // sid of in-flight action

  // Початкова гідрація знімком, якщо WS не встиг.
  useEffect(() => {
    if (anticipator && (anticipator.pending?.length || anticipator.recent?.length)) return
    apiFetch('/anticipator').then(r => r.json()).catch(() => null)
    // setAnticipator не доступний тут — useJarvis оновить через WS після scan.
  }, [])

  const pending = anticipator?.pending || []
  const stats   = anticipator?.stats   || {}

  const top = pending[0]
  const rest = pending.slice(1)

  const onAccept = async (sid) => {
    setBusy(sid)
    try { await acceptSuggestion(sid) } finally { setBusy(null) }
  }
  const onDismiss = async (sid) => {
    setBusy(sid)
    try { await dismissSuggestion(sid) } finally { setBusy(null) }
  }

  if (!top) {
    return (
      <div style={styles.empty}>
        <span style={{ opacity: 0.6 }}>🪄 жодних проактивних пропозицій</span>
        <button style={styles.scanBtn} onClick={() => scanAnticipator()}>
          🔍 scan now
        </button>
        <span style={styles.statsMini}>
          ✓ {stats.accepted || 0} · ✗ {stats.dismissed || 0} · runs {stats.runs || 0}
        </span>
      </div>
    )
  }

  const meta = SOURCE_META[top.source] || SOURCE_META.routine

  return (
    <div style={{ ...styles.banner, borderColor: meta.color }}>
      <div style={styles.headerRow}>
        <span style={{ ...styles.sourceTag, color: meta.color, borderColor: meta.color }}>
          {meta.icon} {meta.label}
        </span>
        <span style={styles.priority}>
          ⚡ {PRIORITY_LABEL[top.priority] || top.priority}
        </span>
        <span style={styles.confidence}>
          ⌖ {Math.round((top.confidence || 0) * 100)}%
        </span>
        <span style={styles.ttl}>{fmtTtl(top.expires_at)}</span>
        <span style={{ flex: 1 }} />
        <button
          style={styles.scanBtn}
          onClick={() => scanAnticipator()}
          title="Виконати сканування зараз"
        >
          🔍
        </button>
      </div>

      <div style={styles.text}>{top.text}</div>

      {top.command && (
        <div style={styles.command}>
          → <code>{top.command}</code>
        </div>
      )}

      <div style={styles.actions}>
        <button
          style={{ ...styles.btn, ...styles.btnAccept }}
          disabled={busy === top.id}
          onClick={() => onAccept(top.id)}
        >
          ✓ Прийняти{top.command ? ' і виконати' : ''}
        </button>
        <button
          style={{ ...styles.btn, ...styles.btnDismiss }}
          disabled={busy === top.id}
          onClick={() => onDismiss(top.id)}
        >
          ✗ Відкинути
        </button>
        {rest.length > 0 && (
          <button
            style={{ ...styles.btn, ...styles.btnMore }}
            onClick={() => setExpanded(e => !e)}
          >
            {expanded ? '▲' : '▼'} ще {rest.length}
          </button>
        )}
        <span style={styles.statsMini}>
          ✓ {stats.accepted || 0} · ✗ {stats.dismissed || 0}
        </span>
      </div>

      {expanded && rest.length > 0 && (
        <div style={styles.list}>
          {rest.map(s => {
            const m = SOURCE_META[s.source] || SOURCE_META.routine
            return (
              <div key={s.id} style={{ ...styles.listItem, borderColor: m.color }}>
                <div style={styles.listHead}>
                  <span style={{ color: m.color }}>{m.icon} {m.label}</span>
                  <span style={styles.tinyMuted}>
                    ⌖ {Math.round((s.confidence || 0) * 100)}% · {fmtAgo(s.ts)}
                  </span>
                </div>
                <div style={styles.listText}>{s.text}</div>
                <div style={styles.listActions}>
                  <button
                    style={{ ...styles.btnSmall, ...styles.btnAccept }}
                    disabled={busy === s.id}
                    onClick={() => onAccept(s.id)}
                  >
                    ✓
                  </button>
                  <button
                    style={{ ...styles.btnSmall, ...styles.btnDismiss }}
                    disabled={busy === s.id}
                    onClick={() => onDismiss(s.id)}
                  >
                    ✗
                  </button>
                </div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

const styles = {
  banner: {
    border: '1px solid #555',
    borderLeftWidth: 4,
    borderRadius: 6,
    padding: '8px 12px',
    marginBottom: 8,
    background: 'rgba(20,20,28,0.85)',
    color: '#e6e6e6',
    fontSize: 13,
    boxShadow: '0 0 12px rgba(0,0,0,0.4)',
  },
  empty: {
    border: '1px dashed #333',
    borderRadius: 6,
    padding: '6px 12px',
    marginBottom: 8,
    background: 'rgba(15,15,20,0.5)',
    color: '#888',
    fontSize: 12,
    display: 'flex',
    alignItems: 'center',
    gap: 10,
  },
  headerRow: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    marginBottom: 6,
    fontSize: 11,
  },
  sourceTag: {
    border: '1px solid',
    borderRadius: 3,
    padding: '1px 6px',
    fontWeight: 'bold',
    fontSize: 11,
  },
  priority:    { color: '#aaa' },
  confidence:  { color: '#7ad8ff' },
  ttl:         { color: '#888', fontStyle: 'italic' },
  text:        { fontSize: 14, lineHeight: 1.4, marginBottom: 6 },
  command: {
    fontSize: 12,
    color: '#a6e22e',
    marginBottom: 8,
    fontFamily: 'monospace',
  },
  actions: {
    display: 'flex',
    gap: 8,
    alignItems: 'center',
    flexWrap: 'wrap',
  },
  btn: {
    padding: '4px 10px',
    border: '1px solid',
    borderRadius: 3,
    background: 'transparent',
    cursor: 'pointer',
    fontSize: 12,
    fontWeight: 'bold',
  },
  btnAccept:  { color: '#a6e22e', borderColor: '#a6e22e' },
  btnDismiss: { color: '#ff5555', borderColor: '#ff5555' },
  btnMore:    { color: '#7ad8ff', borderColor: '#7ad8ff' },
  btnSmall: {
    padding: '2px 8px',
    border: '1px solid',
    borderRadius: 3,
    background: 'transparent',
    cursor: 'pointer',
    fontSize: 12,
    fontWeight: 'bold',
  },
  scanBtn: {
    padding: '2px 8px',
    border: '1px solid #555',
    borderRadius: 3,
    background: 'transparent',
    color: '#aaa',
    cursor: 'pointer',
    fontSize: 12,
    whiteSpace: 'nowrap',
    flexShrink: 0,
  },
  statsMini: {
    marginLeft: 'auto',
    fontSize: 11,
    color: '#666',
  },
  list: {
    marginTop: 8,
    borderTop: '1px solid #333',
    paddingTop: 8,
    display: 'flex',
    flexDirection: 'column',
    gap: 6,
  },
  listItem: {
    border: '1px solid #444',
    borderLeftWidth: 3,
    borderRadius: 4,
    padding: '6px 8px',
    background: 'rgba(0,0,0,0.25)',
  },
  listHead: {
    display: 'flex',
    justifyContent: 'space-between',
    fontSize: 11,
    marginBottom: 4,
  },
  tinyMuted: { color: '#777' },
  listText:  { fontSize: 12, marginBottom: 6 },
  listActions: { display: 'flex', gap: 6 },
}
