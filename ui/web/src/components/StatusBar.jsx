export default function StatusBar({ status, uptime, connected }) {
  const normalized = (status || 'LOADING').toUpperCase()
  const isOnline = normalized.includes('ONLINE')
  const isError  = normalized.includes('ERROR')
  const isLoading = !isOnline && !isError && (normalized.includes('LOAD') || !connected)

  const color = isOnline ? '#00ffcc' : isError ? '#ff4444' : '#ffaa00'
  const label = isLoading ? 'LOADING' : normalized

  return (
    <div style={{
      height:     28,
      background: '#080808',
      borderTop:  '1px solid #1a1a1a',
      display:    'flex',
      alignItems: 'center',
      justifyContent: 'space-between',
      padding:    '0 16px',
      fontFamily: 'Consolas, monospace',
      fontSize:   11,
    }}>
      <style>{`
        @keyframes statusPulse { 0% { opacity: 0.25; } 50% { opacity: 1; } 100% { opacity: 0.25; } }
      `}</style>
      <span style={{ color, display: 'inline-flex', alignItems: 'center', gap: 6 }}>
        <span style={{ animation: isLoading ? 'statusPulse 1s ease-in-out infinite' : 'none' }}>
          ●
        </span>
        {label}
      </span>
      <span style={{ color: '#333' }}>UPTIME: {uptime}</span>
    </div>
  )
}