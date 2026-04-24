export default function StatusBar({ status, uptime, connected, statusTone, assistantSpeaking }) {
  const normalized = (status || 'LOADING').toUpperCase()
  const isOnline = normalized.includes('ONLINE')
  const isError  = normalized.includes('ERROR')
  const isLoading = !isOnline && !isError && (normalized.includes('LOAD') || !connected)

  const color = isOnline ? '#00ffcc' : isError ? '#ff4444' : '#ffaa00'
  const label = isLoading ? 'LOADING' : normalized

  return (
    <div className={`hud-statusbar hud-statusbar--${statusTone}${assistantSpeaking ? ' hud-statusbar--speaking' : ''}`}>
      <span className="hud-statusbar__state" style={{ color }}>
        <span className={[
          isLoading ? 'status-dot-loading' : '',
          isOnline ? 'status-dot-online' : '',
          isError ? 'status-dot-error' : '',
        ].filter(Boolean).join(' ')}>
          ●
        </span>
        {label}
      </span>
      {assistantSpeaking && (
        <span className="hud-statusbar__speaking">
          <span className="hud-statusbar__equalizer" aria-hidden="true">
            <span />
            <span />
            <span />
            <span />
          </span>
          VOICE OUTPUT ACTIVE
        </span>
      )}
      <span className="hud-statusbar__uptime">UPTIME: {uptime}</span>
    </div>
  )
}