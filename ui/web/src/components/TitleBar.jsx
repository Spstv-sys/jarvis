import React from 'react'

export default function TitleBar() {
  const isElectron = !!window.electronAPI
  if (!isElectron) return null

  return (
    <div className="hud-window-controls">
      <button className="window-button hud-window-button" onClick={() => window.electronAPI.minimize()} style={{ '--window-button-bg': '#444' }}>─</button>
      <button className="window-button hud-window-button" onClick={() => window.electronAPI.maximize()} style={{ '--window-button-bg': '#444' }}>▢</button>
      <button className="window-button hud-window-button" onClick={() => window.electronAPI.close()} style={{ '--window-button-bg': '#c0392b' }}>✕</button>
    </div>
  )
}
