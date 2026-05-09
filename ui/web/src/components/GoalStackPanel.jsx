const STATUS_META = {
  pending: { sym: '○', color: '#666' },
  running: { sym: '▶', color: '#7df9ff' },
  done:    { sym: '✓', color: '#00ff66' },
  failed:  { sym: '✗', color: '#ef476f' },
  skipped: { sym: '–', color: '#888' },
}

function fmtDuration(s) {
  if (!s) return ''
  if (s < 1) return `${Math.round(s * 1000)}ms`
  return `${s.toFixed(1)}s`
}

function GoalCard({ goal, accent }) {
  const { steps = [], progress = {}, verdict, success, replan_count = 0 } = goal
  const pct = progress.total ? (progress.done / progress.total) * 100 : 0
  return (
    <div style={{
      border: `1px solid ${accent}55`,
      borderRadius: 4,
      padding: '6px 8px',
      marginBottom: 6,
      background: 'rgba(0,0,0,0.35)',
    }}>
      <div style={{
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
        gap: 8,
      }}>
        <div style={{ color: '#fff', fontSize: 11, flex: 1, wordBreak: 'break-word' }}>
          🎯 {goal.text}
        </div>
        <div style={{ fontSize: 9, color: '#888' }}>
          {progress.done}/{progress.total}
          {replan_count > 0 && <span style={{ color: '#ff9f1c' }}> ↻{replan_count}</span>}
        </div>
      </div>

      {/* progress bar */}
      <div style={{
        height: 3,
        background: 'rgba(255,255,255,0.1)',
        marginTop: 4,
        marginBottom: 6,
        borderRadius: 2,
        overflow: 'hidden',
      }}>
        <div style={{
          height: '100%',
          width: `${pct}%`,
          background: progress.failed > 0 ? '#ef476f' : accent,
          transition: 'width 200ms ease',
        }} />
      </div>

      {/* steps */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
        {steps.map(s => {
          const m = STATUS_META[s.status] || STATUS_META.pending
          return (
            <div key={s.n} style={{
              display: 'flex', gap: 6, fontSize: 10,
              opacity: s.status === 'pending' ? 0.55 : 1,
            }}>
              <span style={{ color: m.color, minWidth: 12 }}>{m.sym}</span>
              <span style={{ color: '#aaa', minWidth: 14 }}>{s.n}</span>
              <span style={{ color: '#a78bfa', minWidth: 90 }}>{s.skill}</span>
              <span style={{ color: '#ddd', flex: 1, wordBreak: 'break-word' }}>
                {s.input.slice(0, 60)}{s.input.length > 60 ? '…' : ''}
              </span>
              {s.duration_sec > 0 && (
                <span style={{ color: '#666', fontSize: 9 }}>{fmtDuration(s.duration_sec)}</span>
              )}
            </div>
          )
        })}
      </div>

      {verdict && (
        <div style={{
          marginTop: 6,
          paddingTop: 4,
          borderTop: '1px dashed rgba(255,255,255,0.08)',
          fontSize: 10,
          color: success ? '#00ff66' : '#ef476f',
        }}>
          {success ? '✓' : '✗'} {verdict}
        </div>
      )}
    </div>
  )
}

export default function GoalStackPanel({ goalStack, accent = '#00ff66' }) {
  const { active = [], history = [] } = goalStack || {}
  return (
    <div style={{
      border: `1px solid ${accent}33`,
      borderRadius: 6,
      background: 'rgba(0,0,0,0.55)',
      fontFamily: 'JetBrains Mono, Consolas, monospace',
      padding: '6px 10px',
      maxHeight: 300,
      overflowY: 'auto',
    }}>
      <div style={{
        color: accent, letterSpacing: 1.5, textTransform: 'uppercase',
        fontSize: 10, marginBottom: 6,
      }}>▸ Goal Stack {active.length > 0 && `(${active.length} active)`}</div>

      {active.length === 0 && history.length === 0 && (
        <div style={{ opacity: 0.5, fontStyle: 'italic', fontSize: 11 }}>
          (немає активних планів)
        </div>
      )}

      {active.map(g => <GoalCard key={g.id} goal={g} accent={accent} />)}

      {history.length > 0 && (
        <>
          <div style={{
            color: '#666', fontSize: 9, marginTop: 6, marginBottom: 4,
            textTransform: 'uppercase', letterSpacing: 1,
          }}>recent ({history.length})</div>
          {history.slice().reverse().slice(0, 3).map(g => (
            <GoalCard key={g.id} goal={g} accent={g.success ? '#00ff66' : '#ef476f'} />
          ))}
        </>
      )}
    </div>
  )
}
