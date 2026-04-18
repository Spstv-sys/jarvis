import React from 'react'

export default function TitleBar() {
  const isElectron = !!window.electronAPI
  if (!isElectron) return null

  return (
    <div style={{
      position:        'fixed',
      top:             0,
      right:           0,
      zIndex:          9999,
      display:         'flex',
      gap:             '8px',
      padding:         '6px 10px',
      WebkitAppRegion: 'no-drag',
    }}>
      <button onClick={() => window.electronAPI.minimize()} style={btnStyle('#444')}>─</button>
      <button onClick={() => window.electronAPI.maximize()} style={btnStyle('#444')}>▢</button>
      <button onClick={() => window.electronAPI.close()}    style={btnStyle('#c0392b')}>✕</button>
    </div>
  )
}

const btnStyle = (bg) => ({
  background:     bg,
  color:          '#fff',
  border:         'none',
  borderRadius:   '4px',
  width:          '28px',
  height:         '28px',
  minWidth:       '28px',
  minHeight:      '28px',
  cursor:         'pointer',
  fontSize:       '14px',
  padding:        '0',
  margin:         '0',
  boxSizing:      'border-box',
  display:        'block',
  lineHeight:     '28px',
  textAlign:      'center',
  userSelect:     'none',
  pointerEvents:  'all',
  position:       'relative',
})