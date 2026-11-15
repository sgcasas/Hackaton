// Cliente de la API con failover entre regiones.
// El orden de config.json es la prioridad: primero us-east-1, si falla (timeout,
// error de red o 5xx) pasa a us-west-2 y recuerda cuál funcionó.

let config = null
let preferida = 0
const tokens = {} // region -> IdToken de Cognito
const oyentes = new Set()

export async function cargarConfig() {
  if (!config) {
    const r = await fetch('/config.json', { cache: 'no-store' })
    config = await r.json()
  }
  return config
}

export function regionActual() {
  return config?.apis?.[preferida]?.region ?? '-'
}

export function alCambiarRegion(fn) {
  oyentes.add(fn)
  return () => oyentes.delete(fn)
}

function avisar() {
  oyentes.forEach((fn) => fn(regionActual()))
}

async function fetchConTimeout(url, opciones, ms = 6000) {
  const ctrl = new AbortController()
  const t = setTimeout(() => ctrl.abort(), ms)
  try {
    return await fetch(url, { ...opciones, signal: ctrl.signal })
  } finally {
    clearTimeout(t)
  }
}

export class ErrorApi extends Error {
  constructor(status, mensaje) {
    super(mensaje)
    this.status = status
  }
}

// Llama a la API probando las regiones en orden, empezando por la que funcionó la última vez.
export async function llamar(ruta, { method = 'GET', body, auth = false } = {}) {
  await cargarConfig()
  const n = config.apis.length
  let ultimoError = null
  for (let k = 0; k < n; k++) {
    const i = (preferida + k) % n
    const api = config.apis[i]
    const headers = { 'Content-Type': 'application/json' }
    if (auth) {
      if (!tokens[api.region]) continue // sin sesión en esa región
      headers.Authorization = tokens[api.region]
    }
    try {
      const r = await fetchConTimeout(api.url + ruta, {
        method,
        headers,
        body: body ? JSON.stringify(body) : undefined,
      })
      if (r.status >= 500) throw new ErrorApi(r.status, `Error ${r.status} en ${api.region}`)
      const datos = await r.json().catch(() => ({}))
      if (i !== preferida) {
        preferida = i
        avisar()
      }
      if (!r.ok) throw new ErrorApi(r.status, datos.error || datos.message || `Error ${r.status}`)
      return { datos, region: api.region }
    } catch (e) {
      // Los 4xx son errores "de verdad": no tiene sentido probar la otra región.
      if (e instanceof ErrorApi && e.status < 500) throw e
      console.warn(`Falló ${api.region}, probando la siguiente región`, e)
      ultimoError = e
    }
  }
  throw ultimoError || new ErrorApi(0, 'No hay ninguna región disponible')
}

// ---- Cognito: login contra el pool de CADA región (así el failover no pide login de nuevo) ----
export async function login(email, password) {
  await cargarConfig()
  const resultados = await Promise.allSettled(
    config.apis.map(async (api) => {
      if (!api.userPoolId || !api.clientId) throw new Error('sin Cognito configurado')
      const r = await fetchConTimeout(`https://cognito-idp.${api.region}.amazonaws.com/`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/x-amz-json-1.1',
          'X-Amz-Target': 'AWSCognitoIdentityProviderService.InitiateAuth',
        },
        body: JSON.stringify({
          AuthFlow: 'USER_PASSWORD_AUTH',
          ClientId: api.clientId,
          AuthParameters: { USERNAME: email, PASSWORD: password },
        }),
      })
      const d = await r.json()
      if (!r.ok) throw new Error(d.message || 'Login inválido')
      if (!d.AuthenticationResult) throw new Error(`Cognito pide el reto ${d.ChallengeName}. Usa scripts/crear-operador.ps1`)
      tokens[api.region] = d.AuthenticationResult.IdToken
      return api.region
    }),
  )
  const ok = resultados.filter((r) => r.status === 'fulfilled').map((r) => r.value)
  if (!ok.length) throw new Error(resultados[0]?.reason?.message || 'No se pudo iniciar sesión')
  try {
    sessionStorage.setItem('rtc-tokens', JSON.stringify(tokens))
  } catch { /* el navegador puede no permitir storage */ }
  return ok
}

export function restaurarSesion() {
  try {
    Object.assign(tokens, JSON.parse(sessionStorage.getItem('rtc-tokens') || '{}'))
  } catch { /* nada */ }
  return Object.keys(tokens).length > 0
}

export function logout() {
  Object.keys(tokens).forEach((k) => delete tokens[k])
  try {
    sessionStorage.removeItem('rtc-tokens')
  } catch { /* nada */ }
}
