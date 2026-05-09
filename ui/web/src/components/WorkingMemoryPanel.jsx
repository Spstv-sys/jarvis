export default function WorkingMemoryPanel({ cognition, accent = '#00ff66' }) {
  const { wm_context = '', wm_entities = [], intent_stats = {} } = cognition || {}
  return (
    <div style={{
      border: `1px solid ${accent}33`,
      borderRadius: 6,
      background: 'rgba(0,0,0,0.55)',
      fontFamily: 'JetBrains Mono, Consolas, monospace',
      fontSize: 11,
      padding: '6px 10px',
      color: '#ddd',
      maxHeight: 220,
      overflowY: 'auto',
    }}>
      <div style={{
        color: accent,
        letterSpacing: 1.5,
        textTransform: 'uppercase',
        fontSize: 10,
        marginBottom: 4,
      }}>▸ Working Memory</div>

      {wm_context ? (
        <pre style={{
          margin: 0,
          padding: 6,
          background: 'rgba(0,255,102,0.04)',
          borderLeft: `2px solid ${accent}66`,
          whiteSpace: 'pre-wrap',
          color: '#cfe',
          fontSize: 11,
        }}>{wm_context}</pre>
      ) : (
        <div style={{ opacity: 0.5, fontStyle: 'italic' }}>(пусто)</div>
      )}

      {wm_entities.length > 0 && (
        <div style={{ marginTop: 6 }}>
          <div style={{ color: '#888', fontSize: 10 }}>ENTITIES</div>
          {wm_entities.map((e, i) => (
            <div key={i} style={{ color: '#a78bfa' }}>
              · {e.kind || '?'}: <span style={{ color: '#fff' }}>{e.value || JSON.stringify(e)}</span>
            </div>
          ))}
        </div>
      )}

      {Object.keys(intent_stats).length > 0 && (
        <div style={{ marginTop: 6, display: 'flex', gap: 10, flexWrap: 'wrap', color: '#9aa0a6', fontSize: 10 }}>
          {Object.entries(intent_stats).map(([k, v]) => (
            <span key={k}><span style={{ color: '#7df9ff' }}>{k}</span>:{typeof v === 'object' ? JSON.stringify(v) : v}</span>
          ))}
        </div>
      )}
    </div>
  )
}
