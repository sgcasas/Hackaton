import { useEffect, useState } from 'react'
import { alCambiarRegion, cargarConfig, regionActual } from './api.js'
import Reportar from './paginas/Reportar.jsx'
import Consultar from './paginas/Consultar.jsx'
import Panel from './paginas/Panel.jsx'

const PAGINAS = {
  reportar: { titulo: 'Reportar', comp: Reportar },
  consultar: { titulo: 'Consultar', comp: Consultar },
  operador: { titulo: 'Operador', comp: Panel },
}

function paginaDeHash() {
  const h = window.location.hash.replace('#/', '').split('?')[0]
  return PAGINAS[h] ? h : 'reportar'
}

export default function App() {
  const [pagina, setPagina] = useState(paginaDeHash())
  const [region, setRegion] = useState('-')

  useEffect(() => {
    cargarConfig().then(() => setRegion(regionActual()))
    const off = alCambiarRegion(setRegion)
    const onHash = () => setPagina(paginaDeHash())
    window.addEventListener('hashchange', onHash)
    return () => {
      off()
      window.removeEventListener('hashchange', onHash)
    }
  }, [])

  const Comp = PAGINAS[pagina].comp
  return (
    <div className="app">
      <header className="cabecera">
        <div className="marca">
          <span className="logo">📡</span> Red TeleCorp
        </div>
        <nav>
          {Object.entries(PAGINAS).map(([k, p]) => (
            <a key={k} href={`#/${k}`} className={k === pagina ? 'activo' : ''}>
              {p.titulo}
            </a>
          ))}
        </nav>
        <span className={`region ${region === 'us-east-1' ? '' : 'alterna'}`} title="Región de AWS que está respondiendo">
          {region}
        </span>
      </header>
      <main>
        <Comp />
      </main>
    </div>
  )
}
