import { useCallback, useEffect, useState } from 'react'
import { llamar, login, logout, restaurarSesion } from '../api.js'
import { ETIQUETA_ESTADO } from './Consultar.jsx'
import { TIPOS } from './Reportar.jsx'

const ESTADOS = ['REGISTRADA', 'EN_REVISION', 'EN_ATENCION', 'SOLUCIONADA', 'CERRADA']
// Estados del incidente (README §2)
const ESTADO_INCIDENTE = {
  ABIERTO: 'Abierto', EN_ATENCION: 'En atención', ESPERANDO_OPERADOR: 'Esperando operador',
  RESTABLECIDO: 'Restablecido', CERRADO: 'Cerrado',
}

export default function Panel() {
  const [sesion, setSesion] = useState(restaurarSesion())
  if (!sesion) return <Login onOk={() => setSesion(true)} />
  return <Tablero onSalir={() => { logout(); setSesion(false) }} />
}

function Login({ onOk }) {
  const [email, setEmail] = useState('')
  const [clave, setClave] = useState('')
  const [error, setError] = useState('')
  const [cargando, setCargando] = useState(false)
  const entrar = async (e) => {
    e.preventDefault()
    setError('')
    setCargando(true)
    try {
      await login(email, clave)
      onOk()
    } catch (err) {
      setError(err.message)
    } finally {
      setCargando(false)
    }
  }
  return (
    <section className="tarjeta angosta">
      <h1>Panel del operador</h1>
      <form onSubmit={entrar} className="formulario">
        <label>Correo<input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} /></label>
        <label>Contraseña<input type="password" required value={clave} onChange={(e) => setClave(e.target.value)} /></label>
        <button disabled={cargando}>{cargando ? 'Entrando…' : 'Entrar'}</button>
      </form>
      {error && <p className="error">{error}</p>}
    </section>
  )
}

const PESTANAS = { incidencias: 'Incidencias', incidentes: 'Incidentes', antenas: 'Antenas', analitica: 'Analítica' }

function Tablero({ onSalir }) {
  const [tab, setTab] = useState('incidencias')
  return (
    <section className="tarjeta ancha">
      <div className="fila entre">
        <div className="pestanas">
          {Object.entries(PESTANAS).map(([k, v]) => (
            <button key={k} className={tab === k ? '' : 'secundario'} onClick={() => setTab(k)}>{v}</button>
          ))}
        </div>
        <button className="secundario" onClick={onSalir}>Salir</button>
      </div>
      {tab === 'incidencias' && <Incidencias />}
      {tab === 'incidentes' && <Incidentes />}
      {tab === 'antenas' && <Antenas />}
      {tab === 'analitica' && <Analitica />}
    </section>
  )
}

// Carga una ruta autenticada y la refresca cada 10 s.
function useDatos(ruta) {
  const [datos, setDatos] = useState(null)
  const [error, setError] = useState('')
  const recargar = useCallback(async () => {
    try {
      const r = await llamar(ruta, { auth: true })
      setDatos(r.datos)
      setError('')
    } catch (e) {
      setError(e.status === 401 ? 'La sesión venció: sal y vuelve a entrar.' : e.message)
    }
  }, [ruta])
  useEffect(() => {
    recargar()
    const t = setInterval(recargar, 10000)
    return () => clearInterval(t)
  }, [recargar])
  return { datos, error, recargar }
}

function fecha(s) {
  return s ? new Date(s).toLocaleString() : '-'
}

function Incidencias() {
  const [filtro, setFiltro] = useState('')
  const { datos, error, recargar } = useDatos(`/operador/incidencias${filtro ? `?estado=${filtro}` : ''}`)
  const [msg, setMsg] = useState('')

  const cambiar = async (codigo, estado) => {
    const comentario = window.prompt(`Comentario para pasar a ${ETIQUETA_ESTADO[estado]}:`, '') ?? null
    if (comentario === null) return
    try {
      await llamar(`/operador/incidencias/${codigo}/estado`, { method: 'PATCH', auth: true, body: { estado, comentario } })
      setMsg(`${codigo.slice(0, 8)} → ${ETIQUETA_ESTADO[estado]}`)
      recargar()
    } catch (e) {
      setMsg(e.message)
    }
  }

  return (
    <div>
      <div className="fila">
        <select value={filtro} onChange={(e) => setFiltro(e.target.value)}>
          <option value="">Todos los estados</option>
          {[...ESTADOS, 'RECHAZADO'].map((s) => <option key={s} value={s}>{ETIQUETA_ESTADO[s]}</option>)}
        </select>
        <span className="sub">{datos ? `${datos.length} reportes` : 'Cargando…'}</span>
        {msg && <span className="sub">· {msg}</span>}
      </div>
      {error && <p className="error">{error}</p>}
      <div className="tabla-scroll">
        <table>
          <thead><tr><th>Código</th><th>Registro</th><th>Zona</th><th>Antena</th><th>Tipo (IA)</th><th>Urgencia</th><th>Prioridad</th><th>Estado</th><th>Incidente</th><th>Mover a</th></tr></thead>
          <tbody>
            {(datos || []).map((i) => (
              <tr key={i.codigo}>
                <td><code title={i.codigo}>{i.codigo.slice(0, 8)}</code></td>
                <td>{fecha(i.fechaRegistro)}</td>
                <td>{i.zonaId || '-'}</td>
                <td>{i.antenaId || '-'}</td>
                <td title={i.tipoIA ? `usuario: ${TIPOS[i.tipo] || i.tipo}` : 'sin IA: tipo del usuario'}>
                  {TIPOS[i.tipoIA || i.tipo] || i.tipo || '-'}{i.tipoIA ? ' 🤖' : ''}
                </td>
                <td><span className={`badge u-${(i.urgenciaIA || '').toLowerCase()}`}>{i.urgenciaIA || '-'}</span></td>
                <td><span className={`badge p-badge-${i.prioridad}`}>{i.prioridad || '-'}</span></td>
                <td><span className={`badge e-${i.estado}`}>{ETIQUETA_ESTADO[i.estado] || i.estado}</span></td>
                <td>{i.incidenteId || '-'}</td>
                <td>
                  {i.estado !== 'CERRADA' && i.estado !== 'RECHAZADO' && (
                    <select value="" onChange={(e) => cambiar(i.codigo, e.target.value)}>
                      <option value="">…</option>
                      {ESTADOS.slice(ESTADOS.indexOf(i.estado) + 1).map((s) => <option key={s} value={s}>{ETIQUETA_ESTADO[s]}</option>)}
                    </select>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function Incidentes() {
  const { datos, error, recargar } = useDatos('/operador/incidentes')
  // README §5: SendTaskSuccess con { resolucion: RESTABLECIDO | SIN_FALLA, comentario }
  const resolver = async (id, resolucion) => {
    const sugerido = resolucion === 'RESTABLECIDO' ? 'Se reinició la estación base' : 'Sin falla en la red'
    const comentario = window.prompt('Comentario de la revisión:', sugerido) ?? null
    if (comentario === null) return
    try {
      await llamar(`/operador/incidentes/${id}/resolver`, { method: 'POST', auth: true, body: { resolucion, comentario } })
      recargar()
    } catch (e) {
      window.alert(e.message)
    }
  }
  return (
    <div>
      {error && <p className="error">{error}</p>}
      {datos && datos.length === 0 && <p className="sub">Todavía no hay incidentes.</p>}
      <div className="lista-incidentes">
        {(datos || []).map((inc) => {
          const id = inc.pk.replace('INCID#', '') // la llave manda: INCID#<nombreEjecucion>
          return (
            <article key={inc.pk} className={`incidente p-${inc.prioridad || ''}`}>
              <div className="fila entre">
                <b>{id}</b>
                <span className={`badge p-badge-${inc.prioridad}`}>{inc.prioridad || 'sin prioridad'}</span>
              </div>
              <p className="sub">
                Zona {inc.zonaId || '-'} · {inc.evento || inc.origen || '-'} · detectado por {inc.detectadoPor || '-'} · <b>{ESTADO_INCIDENTE[inc.estado] || inc.estado || '-'}</b>
              </p>
              <p className="sub">
                Inicio {fecha(inc.inicio)} {inc.fin ? `· Fin ${fecha(inc.fin)}` : ''} · Afectados {inc.afectados ?? '-'}
                {' '}· Reportes {Array.isArray(inc.reportes) ? inc.reportes.length : inc.reportes ?? '-'}
                {inc.antenas?.length ? ` · Antenas ${inc.antenas.join(', ')}` : ''}
              </p>
              {inc.resumenIA && <p className="ia">🤖 {inc.resumenIA}{inc.causaProbable ? ` Causa probable: ${inc.causaProbable}` : ''}</p>}
              {inc.esperandoOperador && (
                <div className="fila">
                  <span className="sub">La Step Function espera tu revisión:</span>
                  <button onClick={() => resolver(id, 'RESTABLECIDO')}>Restablecido</button>
                  <button className="secundario" onClick={() => resolver(id, 'SIN_FALLA')}>Sin falla</button>
                </div>
              )}
            </article>
          )
        })}
      </div>
    </div>
  )
}

function Antenas() {
  const { datos, error } = useDatos('/operador/antenas')
  const lista = (datos || []).slice().sort((a, b) => a.pk.localeCompare(b.pk))
  return (
    <div>
      {error && <p className="error">{error}</p>}
      <div className="rejilla-antenas">
        {lista.map((a) => (
          <div key={a.pk} className={`antena est-${a.estadoActual || 'SIN_DATOS'} ${Number(a.anomaliaScore) >= 0.6 ? 'est-ANOMALIA' : ''}`} title={`Zona ${a.zonaId} · desde ${fecha(a.desde)}`}>
            <b>{a.antenaId} <span className="sub">{a.zonaId}</span></b>
            <span>{a.estadoActual || 'sin datos'}</span>
            <span className="sub">{a.ultimoGB != null ? `${a.ultimoGB} GB/min` : ''}{Number(a.anomaliaScore) > 0 ? ` · anomalía ${Number(a.anomaliaScore).toFixed(2)}` : ''}</span>
            {a.estructural && <span className="badge">estructural</span>}
          </div>
        ))}
      </div>
    </div>
  )
}

function Barras({ titulo, ruta, etiqueta = (x) => x }) {
  const { datos, error } = useDatos(ruta)
  const filas = datos?.datos || []
  const max = Math.max(1, ...filas.map((f) => Number(f.total)))
  return (
    <div className="barras">
      <h3>{titulo} <span className="sub">({datos?.fuente === 'agregados' ? 'SF diaria' : 'en vivo'})</span></h3>
      {error && <p className="error">{error}</p>}
      {filas.length === 0 && <p className="sub">Sin datos todavía.</p>}
      {filas.slice(0, 10).map((f) => (
        <div key={f.clave} className="barra">
          <span className="clave">{etiqueta(f.clave)}</span>
          <span className="relleno" style={{ width: `${(100 * f.total) / max}%` }} />
          <span className="total">{f.total}</span>
        </div>
      ))}
    </div>
  )
}

function Analitica() {
  return (
    <div className="analitica">
      <Barras titulo="Zonas con más reportes" ruta="/operador/analitica/ZONA" />
      <Barras titulo="Tipos de problema" ruta="/operador/analitica/TIPO" etiqueta={(k) => TIPOS[k] || k} />
      <Barras titulo="Antenas con más incidentes" ruta="/operador/analitica/ANTENA" />
    </div>
  )
}
