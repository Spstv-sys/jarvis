import { useEffect, useState } from 'react'

/**
 * Returns current --hud-accent CSS variable value, re-reading
 * whenever the theme changes (data-theme attribute on .hud-shell).
 */
export function useAccent(theme) {
  const [accent, setAccent] = useState('#00ff66')

  useEffect(() => {
    const read = () => {
      const el = document.querySelector('.hud-shell') || document.documentElement
      const val = getComputedStyle(el).getPropertyValue('--hud-accent').trim()
      if (val) setAccent(val)
    }
    // Defer until after the DOM applies the new data-theme attribute
    const id = window.requestAnimationFrame(read)
    return () => window.cancelAnimationFrame(id)
  }, [theme])

  return accent
}
