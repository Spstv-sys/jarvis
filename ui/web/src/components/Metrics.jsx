export default function Metrics({ provider, connected, statusTone, msgCount, onClear }) {
  return (
    <div className={`hud-panel hud-sidebar hud-sidebar--compact hud-sidebar--${statusTone} hud-reveal hud-reveal-delay-3`}>
      <div className={`hud-sidebar__status hud-sidebar__status--inline${connected ? ' is-connected' : ' is-offline'}`}>
        <span className={`metrics-connection-dot ${connected ? 'online' : ''}`}>●</span>
        <span>{connected ? 'ONLINE' : 'OFFLINE'}</span>
        <span className="hud-sidebar__sep">·</span>
        <span className="hud-sidebar__meta-inline">{provider}</span>
        <span className="hud-sidebar__sep">·</span>
        <span className="hud-sidebar__meta-inline">MSG {msgCount}</span>
        <button
          className="clear-button hud-sidebar__clear hud-sidebar__clear--inline"
          onClick={onClear}
          title="Очистити чат"
        >
          🗑
        </button>
      </div>
    </div>
  )
}