export default function StatusBar({ status, uptime }) {
  const isOnline = status.includes('ONLINE')
  const isError  = status.includes('ERROR')

  const color = isOnline ? '#00ffcc' : isError ? '#ff4444' : '#ffaa00'

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
      <span style={{ color }}>● {status.toUpperCase()}</span>
      <span style={{ color: '#333' }}>UPTIME: {uptime}</span>
    </div>
  )
}