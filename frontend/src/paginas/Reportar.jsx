import { useState } from 'react'
import { llamar } from '../api.js'
import ZONAS from '../zonas.json'

// Enum `tipo` del contrato (README §2)
export const TIPOS = {
  SIN_SENAL: 'Sin señal',
  SIN_INTERNET: 'Sin internet',
  LENTITUD: 'Internet lento',
  SIN_LLAMADAS: 'No puedo llamar / se cortan',
  SIN_SMS: 'No llegan SMS',
  OTRO: 'Otro',
}

function ahoraLocal() {
  const d = new Date()
  d.setMinutes(d.getMinutes() - d.getTimezoneOffset())
  return d.toISOString().slice(0, 16)
}

export function guardarCodigo(codigo) {
  try {
    const previos = JSON.parse(localStorage.getItem('rtc-codigos') || '[]')
    localStorage.setItem('rtc-codigos', JSON.stringify([codigo, ...previos.filter((c) => c !== codigo)].slice(0, 5)))
  } catch { /* storage no disponible */ }
}

export default function Reportar() {
  const [f, setF] = useState({
    dni: '', numero: '', lat: '', lon: '', fechaHoraUsuario: ahoraLocal(),
    tipo: 'SIN_SENAL', descripcion: '', email: true, sms: false,
  })
  const [enviando, setEnviando] = useState(false)
  const [resultado, setResultado] = useState(null)
  const [error, setError] = useState('')

  const cambiar = (k) => (e) => setF({ ...f, [k]: e.target.value })

  const usarUbicacion = () => {
    if (!navigator.geolocation) return setError('Tu navegador no da la ubicación')
    navigator.geolocation.getCurrentPosition(
      (p) => setF((x) => ({ ...x, lat: p.coords.latitude.toFixed(5), lon: p.coords.longitude.toFixed(5) })),
      () => setError('No se pudo obtener tu ubicación. Elige una zona de la lista.'),
    )
  }

  const elegirZona = (e) => {
    const z = ZONAS.find((x) => x.id === e.target.value)
    if (z) {
      // Un poco de ruido para que no todos caigan exactamente en el mismo punto.
      const r = () => (Math.random() - 0.5) * 0.004
      setF({ ...f, lat: (z.lat + r()).toFixed(5), lon: (z.lng + r()).toFixed(5) })
    }
  }

  const enviar = async (e) => {
    e.preventDefault()
    setError('')
    setResultado(null)
    if (!f.lat || !f.lon) return setError('Falta la ubicación')
    const canales = [f.email && 'email', f.sms && 'sms'].filter(Boolean)
    if (!canales.length) return setError('Elige al menos un canal para avisarte')
    setEnviando(true)
    try {
      // Formato de contracts/schemas/reporte.sqs.schema.json (codigo y fechaRegistro los pone API Gateway).
      const { datos, region } = await llamar('/reportes', {
        method: 'POST',
        body: {
          dni: f.dni.trim(),
          numero: f.numero.trim(),
          canales,
          lat: Number(f.lat),
          lon: Number(f.lon),
          fechaHoraUsuario: new Date(f.fechaHoraUsuario).toISOString(),
          tipo: f.tipo,
          descripcion: f.descripcion.trim(),
        },
      })
      guardarCodigo(datos.codigo)
      setResultado({ ...datos, region })
    } catch (err) {
      setError(err.status === 400 ? 'Revisa los datos: DNI de 8 dígitos, número de 9 dígitos que empiece con 9 y una descripción.' : err.message)
    } finally {
      setEnviando(false)
    }
  }

  return (
    <section className="tarjeta">
      <h1>Reportar un problema</h1>
      <p className="sub">Cuéntanos qué está fallando. Te avisaremos por correo o SMS cómo avanza.</p>
      <form onSubmit={enviar} className="formulario">
        <label>DNI<input required pattern="[0-9]{8}" value={f.dni} onChange={cambiar('dni')} placeholder="70000001" /></label>
        <label>Número del servicio<input required pattern="9[0-9]{8}" value={f.numero} onChange={cambiar('numero')} placeholder="987000001" /></label>
        <fieldset>
          <legend>Ubicación aproximada</legend>
          <div className="fila">
            <button type="button" className="secundario" onClick={usarUbicacion}>📍 Usar mi ubicación</button>
            <select defaultValue="" onChange={elegirZona}>
              <option value="" disabled>…o elige una zona</option>
              {ZONAS.map((z) => <option key={z.id} value={z.id}>{z.nombre}</option>)}
            </select>
          </div>
          <div className="fila">
            <input value={f.lat} onChange={cambiar('lat')} placeholder="Latitud" />
            <input value={f.lon} onChange={cambiar('lon')} placeholder="Longitud" />
          </div>
        </fieldset>
        <label>¿Desde cuándo?<input type="datetime-local" required value={f.fechaHoraUsuario} onChange={cambiar('fechaHoraUsuario')} /></label>
        <label>Tipo de problema
          <select value={f.tipo} onChange={cambiar('tipo')}>
            {Object.entries(TIPOS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
        </label>
        <label>Descripción<textarea required minLength={3} maxLength={1000} rows={3} value={f.descripcion} onChange={cambiar('descripcion')} placeholder="Ej.: desde las 8 a. m. no tengo señal en mi casa" /></label>
        <fieldset>
          <legend>¿Cómo te avisamos?</legend>
          <div className="fila">
            <label className="check"><input type="checkbox" checked={f.email} onChange={(e) => setF({ ...f, email: e.target.checked })} /> Correo</label>
            <label className="check"><input type="checkbox" checked={f.sms} onChange={(e) => setF({ ...f, sms: e.target.checked })} /> SMS</label>
          </div>
        </fieldset>
        <button disabled={enviando}>{enviando ? 'Enviando…' : 'Enviar reporte'}</button>
      </form>
      {error && <p className="error">{error}</p>}
      {resultado && (
        <div className="exito">
          <p>✅ Recibimos tu reporte.</p>
          <p>Tu código es <code>{resultado.codigo}</code></p>
          <p className="sub">Atendido por {resultado.region}. <a href={`#/consultar?c=${resultado.codigo}`}>Ver estado</a></p>
        </div>
      )}
    </section>
  )
}
