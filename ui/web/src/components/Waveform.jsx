import { useEffect, useRef } from 'react'

const ACCENT = '#00ffcc'
const N_BARS = 32

export default function Waveform({ volume }) {
  const canvasRef = useRef(null)
  const phaseRef  = useRef(0)
  const rafRef    = useRef(null)
  const volRef    = useRef(0)

  // ✅ Плавне згасання гучності
  useEffect(() => {
    volRef.current = volume
  }, [volume])

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')

    const draw = () => {
      const W = canvas.width  = canvas.offsetWidth
      const H = canvas.height = canvas.offsetHeight
      const midY  = H / 2
      const step  = W / (N_BARS + 2)
      const vol   = volRef.current
      const scale = Math.min(vol / 60, 1.0)

      ctx.clearRect(0, 0, W, H)
      phaseRef.current = (phaseRef.current + 0.18) % (Math.PI * 2)

      for (let i = 0; i < N_BARS; i++) {
        const x     = step + i * step
        const wave  = Math.sin(phaseRef.current + i * 0.35)
        const noise = 0.85 + Math.random() * 0.30
        const h     = (midY - 6) * scale * wave * noise

        const t  = Math.max(0, Math.min(scale, 1))
        const g  = Math.round(255 * t)
        const b  = Math.round(200 * t)

        // ✅ Glow — розмитий широкий шар
        ctx.save()
        ctx.shadowColor = ACCENT
        ctx.shadowBlur  = scale > 0.05 ? 12 : 0
        ctx.strokeStyle = `rgb(0,${g},${b})`
        ctx.lineWidth   = 3
        ctx.lineCap     = 'round'
        ctx.beginPath()
        ctx.moveTo(x, midY - h)
        ctx.lineTo(x, midY + h)
        ctx.stroke()
        ctx.restore()
      }

      rafRef.current = requestAnimationFrame(draw)
    }

    draw()
    return () => cancelAnimationFrame(rafRef.current)
  }, [])

  return (
    <canvas
      ref={canvasRef}
      style={{ width: '100%', height: '100%', display: 'block' }}
    />
  )
}