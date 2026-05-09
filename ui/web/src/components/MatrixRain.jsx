import { useEffect, useRef } from 'react'

/**
 * Класичний "цифровий дощ" з Matrix.
 * Малюється на повноекранному canvas позаду інтерфейсу.
 */
export default function MatrixRain({
  color = '#00ff66',
  fontSize = 14,
  fps = 24,
  opacity = 0.55,
}) {
  const canvasRef = useRef(null)

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')

    let width = 0
    let height = 0
    let columns = 0
    let drops = []

    const CHARS =
      'ｱｲｳｴｵｶｷｸｹｺｻｼｽｾｿﾀﾁﾂﾃﾄﾅﾆﾇﾈﾉﾊﾋﾌﾍﾎﾏﾐﾑﾒﾓﾔﾕﾖﾗﾘﾙﾚﾛﾜﾝ' +
      '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ@#$%&*+=<>{}[]/\\|'

    const pickChar = () => CHARS[Math.floor(Math.random() * CHARS.length)]

    const resize = () => {
      const ratio = window.devicePixelRatio || 1
      width  = window.innerWidth
      height = window.innerHeight
      canvas.width  = Math.floor(width  * ratio)
      canvas.height = Math.floor(height * ratio)
      canvas.style.width  = width  + 'px'
      canvas.style.height = height + 'px'
      ctx.setTransform(ratio, 0, 0, ratio, 0, 0)
      columns = Math.ceil(width / fontSize)
      drops = Array.from({ length: columns }, () =>
        Math.floor(Math.random() * (height / fontSize)) * -1
      )
    }

    resize()
    window.addEventListener('resize', resize)

    const FRAME_MS = 1000 / fps
    let lastTime = 0
    let rafId = 0

    const draw = (now = 0) => {
      if (now - lastTime < FRAME_MS) {
        rafId = requestAnimationFrame(draw)
        return
      }
      lastTime = now

      // Прозорий чорний шар — створює "хвіст" що згасає
      ctx.fillStyle = 'rgba(0, 0, 0, 0.07)'
      ctx.fillRect(0, 0, width, height)

      ctx.font = `${fontSize}px "Cascadia Mono", Consolas, monospace`

      for (let i = 0; i < columns; i++) {
        const x = i * fontSize
        const y = drops[i] * fontSize
        const ch = pickChar()

        // Голова (яскраво-біла) + тіло (зелене)
        ctx.fillStyle = '#d8ffe1'
        ctx.shadowColor = color
        ctx.shadowBlur = 6
        ctx.fillText(ch, x, y)
        ctx.shadowBlur = 0
        ctx.fillStyle = color
        ctx.fillText(pickChar(), x, y - fontSize)

        if (y > height && Math.random() > 0.975) {
          drops[i] = 0
        }
        drops[i]++
      }

      rafId = requestAnimationFrame(draw)
    }

    rafId = requestAnimationFrame(draw)

    return () => {
      cancelAnimationFrame(rafId)
      window.removeEventListener('resize', resize)
    }
  }, [color, fontSize, fps])

  return (
    <canvas
      ref={canvasRef}
      className="matrix-rain"
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 0,
        pointerEvents: 'none',
        opacity,
      }}
    />
  )
}
