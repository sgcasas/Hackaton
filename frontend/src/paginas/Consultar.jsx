import { useEffect, useState } from 'react'
import { llamar } from '../api.js'
import { TIPOS } from './Reportar.jsx'

export const ETIQUETA_ESTADO = {
  REGISTRADA: 'Registrada',
  EN_REVISION: 'En revisión',
  EN_ATENCION: 'Siendo atendida',
  SOLUCIONADA: 'Solucionada',
  CERRADA: 'Cerrada',
  RECHAZADO: 'Rechazado',
}
const PASOS = ['REGISTRADA', 'EN_REVISION', 'EN_ATENCION', 'SOLUCIONADA', 'CERRADA']

function codigoDeHash() {
  return new URLSearchParams(window.location.hash.split('?')[1] || '').get('c') || ''
}

function codigosGuardados() {
  try {
    return JSON.parse(localStorage.getItem('rtc-codigos') || '[]')
  } catch {
    return []
  }
}

export default function Consultar() {
  const [codigo, setCodigo] = useState(codigoDeHash())
  const [rep, setRep] = useState(null)
  const [error, setError] = useState('')
  const [cargando, setCargando] = useState(false)

  const buscar = async (c = codigo) => {
    if (!c) return
    setError('')
    setCargando(true)
    try {
      const { datos } = await llamar(`/reportes/${encodeURIComponent(c.trim())}`)
      setRep(datos)
    } catch (e) {
      setRep(null)
      setError(e.message)
    } finally {
      setCargando(false)
    }
  }

  useEffect(() => {
    if (codigo) buscar(codigo)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const idx = rep ? PASOS.indexOf(rep.estado) : -1

  return (
    <section className="tarjeta">
      <h1>Consultar mi reporte</h1>
      <form className="fila" onSubmit={(e) => { e.preventDefault(); buscar() }}>
        <input value={codigo} onChange={(e) => setCodigo(e.target.value)} placeholder="Código del reporte" />
        <button disabled={cargando}>{cargando ? '…' : 'Buscar'}</button>
      </form>
      {codigosGuardados().length > 0 && (
        <p className="sub">Tus últimos: {codigosGuardados().map((c) => (
          <a key={c} href="#/consultar" onClick={() => { setCodigo(c); buscar(c) }} className="chip">{c.slice(0, 8)}…</a>
        ))}</p>
      )}
      {error && <p className="error">{error}</p>}
      {rep && (
        <div>
          {rep.estado === 'RECHAZADO' ? (
            <p className="error">Reporte rechazado: {rep.motivoRechazo === 'CLIENTE_NO_VALIDO' ? 'el DNI y el número no coinciden con un cliente.' : 'superaste el máximo de reportes por día.'}</p>
          ) : (
            <ol className="pasos">
              {PASOS.map((p, i) => (
                <li key={p} className={i < idx ? 'hecho' : i === idx ? 'actual' : ''}>{ETIQUETA_ESTADO[p]}</li>
              ))}
            </ol>
          )}
          <dl className="datos">
            <dt>Tipo</dt><dd>{TIPOS[rep.tipoIA || rep.tipo] || rep.tipo || '-'}</dd>
            <dt>Zona</dt><dd>{rep.zonaId || '-'}</dd>
            <dt>Registrado</dt><dd>{rep.fechaRegistro ? new Date(rep.fechaRegistro).toLocaleString() : '-'}</dd>
            {rep.incidenteId && (<><dt>Incidente de zona</dt><dd>{rep.incidenteId}</dd></>)}
          </dl>
          <h3>Historial</h3>
          <ul className="historial">
            {rep.historial.map((h) => (
              <li key={h.fecha}>
                <span className="fecha">{new Date(h.fecha).toLocaleString()}</span>
                <b>{ETIQUETA_ESTADO[h.estadoNuevo] || h.estadoNuevo}</b>
                {h.comentario && <span> · {h.comentario}</span>}
              </li>
            ))}
          </ul>
          <button className="secundario" onClick={() => buscar()}>Actualizar</button>
        </div>
      )}
    </section>
  )
}
