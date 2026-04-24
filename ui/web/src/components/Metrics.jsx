function MetricBar({ label, value, color }) {
  const clamped = value != null ? Math.max(0, Math.min(value, 100)) : 0

  return (
    <div className="metric-block">
      <div className="metric-block__header">
        <span className="metric-label">
          {label}
        </span>
        <span className="metric-value">
          {value != null ? `${Math.round(value)}%` : 'N/A'}
        </span>
      </div>
      <div className="metric-block__track">
        <div
          className="metric-block__bar"
          style={{
            width:      `${clamped}%`,
            background: color,
            boxShadow:  `0 0 6px ${color}55`,
          }}
        />
      </div>
    </div>
  )
}

export default function Metrics({ metrics, status, provider, connected, statusTone, msgCount, onClear }) {
  const { cpu, ram, battery, plugged } = metrics
  void status

  return (
    <div className={`hud-panel hud-sidebar hud-sidebar--${statusTone} hud-reveal hud-reveal-delay-3`}>
      <div className="hud-sidebar__section-head">
        <span className="hud-kicker">SYSTEM LOAD</span>
      </div>

      <MetricBar label="CPU"     value={cpu} color="#ff4444" />
      <MetricBar label="RAM"     value={ram} color="#4499ff" />
      <MetricBar
        label="BATTERY"
        value={battery}
        color="#ffee44"
      />
      {battery != null && (
        <div className="hud-sidebar__battery-note">
          {plugged ? '⚡ charging' : '🔋'}
        </div>
      )}

      <div className="hud-sidebar__divider" />

      <div className={`hud-sidebar__status${connected ? ' is-connected' : ' is-offline'}`}>
        <span className={`metrics-connection-dot ${connected ? 'online' : ''}`}>●</span>{' '}
        {connected ? 'CONNECTED' : 'OFFLINE'}
      </div>

      <div className="hud-sidebar__meta">
        AI: {provider}
      </div>

      <div className="hud-sidebar__divider" />

      <div className="hud-sidebar__meta">
        MSG: {msgCount}
      </div>

      <button
        className="clear-button hud-sidebar__clear"
        onClick={onClear}
      >
        🗑 CLEAR
      </button>
    </div>
  )
}