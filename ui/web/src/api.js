const KEY_STORAGE = 'jarvis-api-key'

export function validateServerStatus(status) {
  if (status.api_version !== 1) {
    throw new Error('Перезапустіть сервер Нео: запущена версія несумісна з цим інтерфейсом.')
  }
  return status
}

export function getApiKey() {
  return sessionStorage.getItem(KEY_STORAGE) || ''
}

export function setApiKey(key) {
  if (key) sessionStorage.setItem(KEY_STORAGE, key)
  else sessionStorage.removeItem(KEY_STORAGE)
}

export function websocketUrl(location = window.location) {
  const url = new URL('/ws', location.href)
  url.protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
  return url.href
}

export function websocketProtocols(key = getApiKey()) {
  if (!key) return ['jarvis']
  const bytes = new TextEncoder().encode(key)
  const encoded = btoa(String.fromCharCode(...bytes))
    .replaceAll('+', '-').replaceAll('/', '_').replace(/=+$/, '')
  return ['jarvis', `jarvis-key.${encoded}`]
}

export async function apiFetch(url, options = {}) {
  const headers = new Headers(options.headers)
  const key = getApiKey()
  if (key) headers.set('X-API-Key', key)
  const response = await fetch(url, { ...options, headers })
  if (response.status === 401) {
    window.dispatchEvent(new Event('jarvis-auth-required'))
  }
  if (!response.ok) throw new Error(`Запит відхилено (${response.status})`)
  return response
}
