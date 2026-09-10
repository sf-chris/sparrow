import { useEffect, useRef } from 'react'

type Handler = (event: Record<string, unknown>) => void

/** One connection per mounted subscriber; reconnect asks for a fresh snapshot. */
export function useWebSocket(onMessage: Handler) {
  const handler = useRef(onMessage)
  handler.current = onMessage
  useEffect(() => {
    let disposed = false
    let socket: WebSocket | undefined
    let timer: ReturnType<typeof setTimeout> | undefined
    const connect = () => {
      if (disposed) return
      socket = new WebSocket(`${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/ws`)
      socket.onopen = () => handler.current({ type: 'reconnected' })
      socket.onmessage = event => {
        try { handler.current(JSON.parse(event.data)) } catch { /* Ignore malformed events. */ }
      }
      socket.onclose = () => { if (!disposed) timer = setTimeout(connect, 3000) }
      socket.onerror = () => socket?.close()
    }
    connect()
    return () => {
      disposed = true
      clearTimeout(timer)
      if (socket) { socket.onclose = null; socket.close() }
    }
  }, [])
}
