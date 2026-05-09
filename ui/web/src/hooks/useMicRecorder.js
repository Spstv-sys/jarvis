import { useCallback, useEffect, useRef, useState } from 'react'

/**
 * Toggle mic recording via MediaRecorder.
 * - start(): request mic, begin recording
 * - stop(): stop and resolve with a Blob (audio/webm or audio/ogg)
 * - state: 'idle' | 'requesting' | 'recording' | 'stopping'
 * - error: last error string (or null)
 * - level: short-window peak amplitude 0..1 for UI feedback
 */
export function useMicRecorder() {
  const [state, setState] = useState('idle')
  const [error, setError] = useState(null)
  const [level, setLevel] = useState(0)

  const recorderRef = useRef(null)
  const streamRef   = useRef(null)
  const chunksRef   = useRef([])
  const resolveRef  = useRef(null)
  const audioCtxRef = useRef(null)
  const analyserRef = useRef(null)
  const rafRef      = useRef(null)

  const cleanup = useCallback(() => {
    if (rafRef.current) {
      cancelAnimationFrame(rafRef.current)
      rafRef.current = null
    }
    try { audioCtxRef.current?.close() } catch {}
    audioCtxRef.current = null
    analyserRef.current = null
    if (streamRef.current) {
      streamRef.current.getTracks().forEach(t => t.stop())
      streamRef.current = null
    }
    recorderRef.current = null
    chunksRef.current = []
    setLevel(0)
  }, [])

  useEffect(() => () => cleanup(), [cleanup])

  const pickMime = () => {
    if (typeof MediaRecorder === 'undefined') return ''
    const candidates = [
      'audio/webm;codecs=opus',
      'audio/webm',
      'audio/ogg;codecs=opus',
      'audio/ogg',
      'audio/mp4',
    ]
    for (const m of candidates) {
      try { if (MediaRecorder.isTypeSupported(m)) return m } catch {}
    }
    return ''
  }

  const start = useCallback(async () => {
    if (state !== 'idle') return
    setError(null)
    setState('requesting')
    try {
      if (!navigator.mediaDevices?.getUserMedia) {
        throw new Error('Браузер не підтримує доступ до мікрофону')
      }
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      streamRef.current = stream

      const mimeType = pickMime()
      const recorder = mimeType ? new MediaRecorder(stream, { mimeType }) : new MediaRecorder(stream)
      recorderRef.current = recorder
      chunksRef.current = []

      recorder.ondataavailable = (e) => {
        if (e.data && e.data.size > 0) chunksRef.current.push(e.data)
      }
      recorder.onstop = () => {
        const type = recorder.mimeType || mimeType || 'audio/webm'
        const blob = new Blob(chunksRef.current, { type })
        const r = resolveRef.current
        resolveRef.current = null
        cleanup()
        setState('idle')
        r?.(blob)
      }
      recorder.onerror = (e) => {
        setError(String(e.error || e))
        const r = resolveRef.current
        resolveRef.current = null
        cleanup()
        setState('idle')
        r?.(null)
      }

      // Lightweight level meter
      try {
        const AC = window.AudioContext || window.webkitAudioContext
        const ac = new AC()
        audioCtxRef.current = ac
        const src = ac.createMediaStreamSource(stream)
        const an = ac.createAnalyser()
        an.fftSize = 512
        src.connect(an)
        analyserRef.current = an
        const buf = new Uint8Array(an.fftSize)
        const tick = () => {
          if (!analyserRef.current) return
          analyserRef.current.getByteTimeDomainData(buf)
          let peak = 0
          for (let i = 0; i < buf.length; i++) {
            const v = Math.abs(buf[i] - 128)
            if (v > peak) peak = v
          }
          setLevel(Math.min(1, peak / 96))
          rafRef.current = requestAnimationFrame(tick)
        }
        rafRef.current = requestAnimationFrame(tick)
      } catch {}

      recorder.start(250)
      setState('recording')
    } catch (e) {
      setError(e?.message || String(e))
      cleanup()
      setState('idle')
    }
  }, [state, cleanup])

  const stop = useCallback(() => {
    if (state !== 'recording') return Promise.resolve(null)
    setState('stopping')
    return new Promise((resolve) => {
      resolveRef.current = resolve
      try {
        recorderRef.current?.stop()
      } catch {
        cleanup()
        setState('idle')
        resolve(null)
      }
    })
  }, [state, cleanup])

  const cancel = useCallback(() => {
    if (state === 'idle') return
    resolveRef.current = null
    try { recorderRef.current?.stop() } catch {}
    cleanup()
    setState('idle')
  }, [state, cleanup])

  return { state, error, level, start, stop, cancel, isRecording: state === 'recording' }
}
