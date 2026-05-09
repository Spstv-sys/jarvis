import { useEffect, useRef } from 'react'

const N_BARS = 32

function getCSSAccent() {
  const el = document.querySelector('.hud-shell') || document.documentElement
  return getComputedStyle(el).getPropertyValue('--hud-accent').trim() || '#00ff66'
}

export default function Waveform({ volume }) {
  const canvasRef = useRef(null)
  const phaseRef  = useRef(0)
  const rafRef    = useRef(null)
  const volRef    = useRef(0)
  const heightsRef = useRef(Array.from({ length: N_BARS }, () => 0))

  // ✅ Плавне згасання гучності
  useEffect(() => {
    volRef.current = volume
  }, [volume])

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')

    // Throttle: візуально ~30 FPS достатньо (volume приходить раз на секунду),
    // але жива анімація хвилі оновлюється. Економить CPU/GPU вдвічі проти 60 FPS.
    const FRAME_MS = 33
    let lastTime = 0

    const draw = (now = 0) => {
      if (now - lastTime < FRAME_MS) {
        rafRef.current = requestAnimationFrame(draw)
        return
      }
      lastTime = now

      const ratio = window.devicePixelRatio || 1
      const W = canvas.offsetWidth
      const H = canvas.offsetHeight
      canvas.width = Math.floor(W * ratio)
      canvas.height = Math.floor(H * ratio)
      ctx.setTransform(ratio, 0, 0, ratio, 0, 0)

      const midY  = H / 2
      const step  = W / (N_BARS + 2)
      const vol   = volRef.current
      const scale = Math.min(vol / 60, 1.0)

      ctx.clearRect(0, 0, W, H)
      phaseRef.current = (phaseRef.current + 0.18) % (Math.PI * 2)

      for (let i = 0; i < N_BARS; i++) {
        const x     = step + i * step
        const envelope = 0.45 + 0.55 * Math.sin(((i + 1) / N_BARS) * Math.PI)
        const wave = Math.abs(
          Math.sin(phaseRef.current + i * 0.22) * 0.7 +
          Math.sin(phaseRef.current * 0.55 + i * 0.11) * 0.3
        )
        const target = (midY - 8) * scale * envelope * (0.18 + wave)
        const prev = heightsRef.current[i]
        const h = prev + (target - prev) * 0.2
        heightsRef.current[i] = h

        const accent = getCSSAccent()

        // ✅ Glow — розмитий широкий шар
        ctx.save()
        ctx.shadowColor = accent
        ctx.shadowBlur  = scale > 0.05 ? 12 : 0
        ctx.strokeStyle = scale > 0.05 ? accent : accent + '55'
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
      className="waveform-canvas"
      style={{ width: '100%', height: '100%', display: 'block' }}
    />
  )
}