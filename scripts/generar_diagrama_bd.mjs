#!/usr/bin/env node
/**
 * Genera el diagrama entidad-relación de la base de datos a partir de los
 * modelos SQLAlchemy (`app/models/models.py`), sin dependencias externas.
 *
 * Salidas (por defecto en `docs/`):
 *   - diagrama_bd.mmd   Diagrama Mermaid (pegar en GitHub, Notion, draw.io, etc.)
 *   - diagrama_bd.html  Visor autónomo con zoom, paneo y descarga en SVG
 *
 * Uso:
 *   node scripts/generar_diagrama_bd.mjs
 *   node scripts/generar_diagrama_bd.mjs --models app/models/models.py --out docs
 */

import { readFileSync, writeFileSync, mkdirSync } from 'node:fs'
import { dirname, resolve, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const AQUI = dirname(fileURLToPath(import.meta.url))
const RAIZ = resolve(AQUI, '..')

const TIPOS = {
  BigInteger: 'bigint',
  SmallInteger: 'smallint',
  Integer: 'integer',
  Numeric: 'numeric',
  Float: 'float',
  Double: 'double',
  String: 'varchar',
  Text: 'text',
  Unicode: 'varchar',
  UnicodeText: 'text',
  Boolean: 'boolean',
  Date: 'date',
  DateTime: 'timestamp',
  TIMESTAMP: 'timestamp',
  Time: 'time',
  JSON: 'json',
  JSONB: 'jsonb',
  UUID: 'uuid',
  LargeBinary: 'bytea',
  BYTEA: 'bytea',
  ARRAY: 'array',
  Enum: 'enum',
  CHAR: 'char',
  VARCHAR: 'varchar',
}

function leerArgs(argv) {
  const args = { models: 'app/models/models.py', out: 'docs' }
  for (let i = 0; i < argv.length; i += 1) {
    if (argv[i] === '--models' && argv[i + 1]) args.models = argv[++i]
    else if (argv[i] === '--out' && argv[i + 1]) args.out = argv[++i]
  }
  return args
}

/** Devuelve el contenido entre el paréntesis que abre en `inicio`. */
function contenidoParentesis(texto, inicio) {
  let nivel = 0
  let enCadena = false
  let comilla = ''
  for (let i = inicio; i < texto.length; i += 1) {
    const c = texto[i]
    if (enCadena) {
      if (c === '\\') i += 1
      else if (c === comilla) enCadena = false
      continue
    }
    if (c === '"' || c === "'") {
      enCadena = true
      comilla = c
    } else if (c === '(') {
      nivel += 1
    } else if (c === ')') {
      nivel -= 1
      if (nivel === 0) return texto.slice(inicio + 1, i)
    }
  }
  return ''
}

function llamadas(texto, nombre) {
  const resultado = []
  const patron = new RegExp(`\\b${nombre}\\s*\\(`, 'g')
  let m
  while ((m = patron.exec(texto)) !== null) {
    const abre = texto.indexOf('(', m.index)
    resultado.push({ indice: m.index, args: contenidoParentesis(texto, abre) })
  }
  return resultado
}

function tipoDeColumn(args) {
  const m = args.match(/^\s*([A-Za-z_][A-Za-z0-9_]*)/)
  if (!m) return 'text'
  return TIPOS[m[1]] ?? m[1].toLowerCase()
}

function nombreAntesDe(texto, indice) {
  const salto = texto.lastIndexOf('\n', indice)
  const prefijo = texto.slice(salto + 1, indice)
  const m = prefijo.match(/([A-Za-z_][A-Za-z0-9_]*)\s*(?::\s*.*?)?=\s*$/)
  return m ? m[1] : null
}

/** Quita argumentos con nombre (`name="..."`) para quedarse solo con las columnas. */
function soloArgumentosPosicionales(args) {
  return args.replace(/\b[A-Za-z_][A-Za-z0-9_]*\s*=\s*("[^"]*"|'[^']*'|[^,)]+)/g, '')
}

function columnasDeLlamada(args) {
  return [...soloArgumentosPosicionales(args).matchAll(/["']([^"']+)["']/g)].map((m) => m[1])
}

function clavesDeTablaArgs(args) {
  const pk = new Set()
  const uk = new Set()
  for (const llamada of llamadas(args, 'PrimaryKeyConstraint')) {
    for (const col of columnasDeLlamada(llamada.args)) pk.add(col)
  }
  for (const llamada of llamadas(args, 'UniqueConstraint')) {
    // Solo las restricciones de una columna se pueden marcar como UK en Mermaid;
    // las compuestas no implican unicidad individual.
    const columnas = columnasDeLlamada(llamada.args)
    if (columnas.length === 1) uk.add(columnas[0])
  }
  return { pk, uk }
}

/** Argumentos de una asignación tipo `__table_args__ = ( ... )`. */
function argsDeAsignacion(texto, nombre) {
  const patron = new RegExp(`\\b${nombre}\\s*=\\s*\\(`)
  const m = patron.exec(texto)
  if (!m) return ''
  return contenidoParentesis(texto, m.index + m[0].length - 1)
}

function relacionesDeTablaArgs(args) {
  const relaciones = []
  for (const llamada of llamadas(args, 'ForeignKeyConstraint')) {
    const grupos = [...llamada.args.matchAll(/\[([^\]]+)\]/g)]
      .map((m) => [...m[1].matchAll(/"([^"]+)"/g)].map((x) => x[1]))
    if (grupos.length < 2) continue
    grupos[0].forEach((columna, i) => {
      const referencia = grupos[1][i]
      if (!referencia) return
      const [tabla] = referencia.split('.')
      relaciones.push({ columna, tablaPadre: tabla })
    })
  }
  return relaciones
}

function normalizarEntidad(tabla) {
  return tabla.replaceAll(/[^A-Za-z0-9_]/g, '_')
}

function parsearModelos(codigo) {
  const tablas = new Map()

  const agregar = (tabla) => {
    const clave = normalizarEntidad(tabla)
    if (!tablas.has(clave)) {
      tablas.set(clave, { tabla: clave, columnas: [], relaciones: [] })
    }
    return tablas.get(clave)
  }

  // Tablas de asociación declaradas con Table(...)
  for (const llamada of llamadas(codigo, 'Table')) {
    const nombre = llamada.args.match(/^\s*["']([^"']+)["']/)?.[1]
    if (!nombre) continue
    const entidad = agregar(nombre)
    for (const columna of llamadas(llamada.args, 'Column')) {
      const m = columna.args.match(/^\s*["']([^"']+)["']\s*,\s*([A-Za-z_][A-Za-z0-9_]*)/)
      if (!m) continue
      const [, nombreColumna, tipo] = m
      const fk = columna.args.match(/ForeignKey\(\s*["']([^"']+)["']/)
      const referencia = fk?.[1]?.split('.')[0]
      const esPk = /primary_key\s*=\s*True/.test(columna.args)
      entidad.columnas.push({
        nombre: nombreColumna,
        tipo: TIPOS[tipo] ?? tipo.toLowerCase(),
        pk: esPk,
        uk: /unique\s*=\s*True/.test(columna.args),
        nullable: !esPk && !/nullable\s*=\s*False/.test(columna.args),
      })
      if (referencia) {
        entidad.relaciones.push({
          columna: nombreColumna,
          tablaPadre: normalizarEntidad(referencia),
          nullable: !esPk,
        })
      }
    }
  }

  // Clases declarativas
  const clases = [...codigo.matchAll(/^class\s+(\w+)\s*\(\s*Base\s*\)\s*:/gm)]
  clases.forEach((match, indice) => {
    const desde = match.index
    const hasta = indice + 1 < clases.length ? clases[indice + 1].index : codigo.length
    const cuerpo = codigo.slice(desde, hasta)
    const nombreTabla = cuerpo.match(/__tablename__\s*=\s*["']([^"']+)["']/)?.[1]
    if (!nombreTabla) return
    const entidad = agregar(nombreTabla)

    const tableArgs = argsDeAsignacion(cuerpo, '__table_args__')
    const { pk, uk } = clavesDeTablaArgs(tableArgs)
    const relacionesTabla = relacionesDeTablaArgs(tableArgs)

    for (const columna of llamadas(cuerpo, 'mapped_column')) {
      const nombreColumna = nombreAntesDe(cuerpo, columna.indice)
      if (!nombreColumna) continue
      const esPk = /primary_key\s*=\s*True/.test(columna.args) || pk.has(nombreColumna)
      const esUk = /unique\s*=\s*True/.test(columna.args) || uk.has(nombreColumna)
      const nullable = !esPk && !/nullable\s*=\s*False/.test(columna.args)
      entidad.columnas.push({
        nombre: nombreColumna,
        tipo: tipoDeColumn(columna.args),
        pk: esPk,
        uk: esUk,
        nullable,
      })
      for (const fk of columna.args.matchAll(/ForeignKey\(\s*["']([^"']+)["']/g)) {
        entidad.relaciones.push({
          columna: nombreColumna,
          tablaPadre: normalizarEntidad(fk[1].split('.')[0]),
          nullable,
        })
      }
    }
    for (const relacion of relacionesTabla) {
      const columna = entidad.columnas.find((c) => c.nombre === relacion.columna)
      entidad.relaciones.push({ ...relacion, nullable: columna?.nullable ?? true })
    }
  })

  return [...tablas.values()].filter((t) => t.columnas.length > 0)
}

function generarMermaid(tablas) {
  const lineas = [
    '%% Diagrama entidad-relación generado desde app/models/models.py',
    `%% Tablas: ${tablas.length}`,
    'erDiagram',
  ]

  for (const entidad of tablas) {
    lineas.push(`    ${entidad.tabla} {`)
    for (const columna of entidad.columnas) {
      const claves = [columna.pk && 'PK', columna.uk && 'UK'].filter(Boolean).join(', ')
      lineas.push(
        `        ${columna.tipo} ${columna.nombre}${claves ? ` ${claves}` : ''}`,
      )
    }
    lineas.push('    }')
  }

  const vistas = new Set()
  for (const entidad of tablas) {
    for (const relacion of entidad.relaciones) {
      if (relacion.tablaPadre === entidad.tabla) continue
      const clave = `${entidad.tabla}|${relacion.tablaPadre}`
      if (vistas.has(clave)) continue
      vistas.add(clave)
      const cardinalidad = relacion.nullable ? '}o--o|' : '}o--||'
      lineas.push(
        `    ${entidad.tabla} ${cardinalidad} ${relacion.tablaPadre} : "${relacion.columna}"`,
      )
    }
  }

  return { mermaid: `${lineas.join('\n')}\n`, relaciones: vistas.size }
}

function escaparHtml(texto) {
  return texto
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
}

function generarHtml(mermaid, estadisticas) {
  const fuente = JSON.stringify(mermaid)
  return `<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Diagrama de la base de datos · SI2</title>
<style>
  :root { color-scheme: light; }
  * { box-sizing: border-box; }
  html, body { height: 100%; margin: 0; }
  body { font-family: ui-sans-serif, system-ui, "Segoe UI", Roboto, sans-serif; background: #f5f7fb; color: #0b1c30; }
  header { display: flex; flex-wrap: wrap; align-items: center; gap: 12px; padding: 12px 18px;
           background: #064e3b; color: #fff; }
  header h1 { font-size: 15px; margin: 0; font-weight: 600; letter-spacing: .2px; }
  header .stats { font-size: 12px; color: #b9f3d8; }
  header .acciones { margin-left: auto; display: flex; gap: 6px; }
  button { border: 1px solid rgba(255,255,255,.28); background: rgba(255,255,255,.12); color: #fff;
           border-radius: 8px; padding: 6px 10px; font-size: 12px; font-weight: 600; cursor: pointer; }
  button:hover { background: rgba(255,255,255,.22); }
  button:disabled { opacity: .4; cursor: default; }
  #viewport { position: relative; width: 100%; height: calc(100% - 52px); overflow: hidden;
              cursor: grab; touch-action: none; }
  #viewport.arrastrando { cursor: grabbing; }
  #canvas { position: absolute; top: 0; left: 0; transform-origin: 0 0; will-change: transform; }
  #canvas svg { display: block; max-width: none !important; height: auto !important; }
  #estado { position: absolute; left: 50%; top: 40%; transform: translate(-50%, -50%);
            font-size: 14px; color: #475569; text-align: center; max-width: 460px; line-height: 1.5; }
  #estado.error { color: #b91c1c; }
  #pista { position: absolute; bottom: 10px; left: 50%; transform: translateX(-50%);
           font-size: 11px; color: #64748b; background: rgba(255,255,255,.85);
           border: 1px solid #e2e8f0; border-radius: 999px; padding: 4px 12px; pointer-events: none; }
</style>
</head>
<body>
<header>
  <h1>Diagrama entidad-relación · SI2</h1>
  <span class="stats">${estadisticas}</span>
  <div class="acciones">
    <button id="menos" title="Reducir">−</button>
    <button id="mas" title="Ampliar">+</button>
    <button id="ajustar" title="Ajustar a la pantalla">Ajustar</button>
    <button id="svg" title="Descargar el diagrama como SVG">Descargar SVG</button>
  </div>
</header>
<div id="viewport">
  <div id="canvas"></div>
  <div id="estado">Cargando diagrama…</div>
  <div id="pista">Rueda del ratón para zoom · arrastra para mover</div>
</div>
<script type="module">
const FUENTE = ${fuente}
const estado = document.getElementById('estado')
const viewport = document.getElementById('viewport')
const canvas = document.getElementById('canvas')

let escala = 1, desplazamientoX = 0, desplazamientoY = 0

function aplicar() {
  canvas.style.transform = 'translate(' + desplazamientoX + 'px,' + desplazamientoY + 'px) scale(' + escala + ')'
}

function ajustar() {
  const svg = canvas.querySelector('svg')
  if (!svg) return
  const ancho = svg.viewBox?.baseVal?.width || svg.getBoundingClientRect().width || 1
  const alto = svg.viewBox?.baseVal?.height || svg.getBoundingClientRect().height || 1
  escala = Math.min(viewport.clientWidth / ancho, viewport.clientHeight / alto) * 0.94
  escala = Math.max(0.05, Math.min(escala, 4))
  desplazamientoX = (viewport.clientWidth - ancho * escala) / 2
  desplazamientoY = (viewport.clientHeight - alto * escala) / 2
  aplicar()
}

function zoom(factor, centroX = viewport.clientWidth / 2, centroY = viewport.clientHeight / 2) {
  const nueva = Math.max(0.05, Math.min(escala * factor, 6))
  const proporcion = nueva / escala
  desplazamientoX = centroX - (centroX - desplazamientoX) * proporcion
  desplazamientoY = centroY - (centroY - desplazamientoY) * proporcion
  escala = nueva
  aplicar()
}

viewport.addEventListener('wheel', (evento) => {
  evento.preventDefault()
  const rect = viewport.getBoundingClientRect()
  zoom(evento.deltaY < 0 ? 1.12 : 0.89, evento.clientX - rect.left, evento.clientY - rect.top)
}, { passive: false })

let arrastrando = false
let ultimoX = 0
let ultimoY = 0
viewport.addEventListener('pointerdown', (evento) => {
  if (evento.button !== 0) return
  arrastrando = true
  ultimoX = evento.clientX
  ultimoY = evento.clientY
  viewport.classList.add('arrastrando')
  viewport.setPointerCapture(evento.pointerId)
})
viewport.addEventListener('pointermove', (evento) => {
  if (!arrastrando) return
  desplazamientoX += evento.clientX - ultimoX
  desplazamientoY += evento.clientY - ultimoY
  ultimoX = evento.clientX
  ultimoY = evento.clientY
  aplicar()
})
function soltar(evento) {
  arrastrando = false
  viewport.classList.remove('arrastrando')
  if (evento?.pointerId !== undefined && viewport.hasPointerCapture?.(evento.pointerId)) {
    viewport.releasePointerCapture(evento.pointerId)
  }
}
viewport.addEventListener('pointerup', soltar)
viewport.addEventListener('pointercancel', soltar)

document.getElementById('mas').addEventListener('click', () => zoom(1.2))
document.getElementById('menos').addEventListener('click', () => zoom(0.83))
document.getElementById('ajustar').addEventListener('click', ajustar)
document.getElementById('svg').addEventListener('click', () => {
  const svg = canvas.querySelector('svg')
  if (!svg) return
  const copia = svg.cloneNode(true)
  copia.setAttribute('xmlns', 'http://www.w3.org/2000/svg')
  const blob = new Blob([new XMLSerializer().serializeToString(copia)], { type: 'image/svg+xml' })
  const enlace = document.createElement('a')
  enlace.href = URL.createObjectURL(blob)
  enlace.download = 'diagrama_bd.svg'
  enlace.click()
  URL.revokeObjectURL(enlace.href)
})

try {
  const { default: mermaid } = await import('https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs')
  mermaid.initialize({
    startOnLoad: false,
    theme: 'default',
    securityLevel: 'loose',
    themeVariables: { fontFamily: 'ui-sans-serif, system-ui, Segoe UI, Roboto, sans-serif' },
    er: { useMaxWidth: false, layoutDirection: 'TB' },
  })
  const { svg } = await mermaid.render('diagrama_bd', FUENTE)
  canvas.innerHTML = svg
  estado.remove()
  ajustar()
  globalThis.addEventListener('resize', () => { ajustar() })
} catch (error) {
  estado.className = 'error'
  estado.innerHTML = 'No se pudo renderizar el diagrama.<br/>' +
    'Verifica tu conexión a Internet (Mermaid se carga desde un CDN) o abre <code>diagrama_bd.mmd</code> en un editor compatible.'
  console.error(error)
}
</script>
</body>
</html>
`
}

function principal() {
  const args = leerArgs(process.argv.slice(2))
  const rutaModelos = resolve(RAIZ, args.models)
  const rutaSalida = resolve(RAIZ, args.out)
  const codigo = readFileSync(rutaModelos, 'utf8')
  const tablas = parsearModelos(codigo)
  if (tablas.length === 0) {
    console.error('No se encontraron tablas en ' + rutaModelos)
    process.exitCode = 1
    return
  }
  const { mermaid, relaciones } = generarMermaid(tablas)
  const columnas = tablas.reduce((total, tabla) => total + tabla.columnas.length, 0)
  const estadisticas = `${tablas.length} tablas · ${columnas} columnas · ${relaciones} relaciones`

  mkdirSync(rutaSalida, { recursive: true })
  const rutaMmd = join(rutaSalida, 'diagrama_bd.mmd')
  const rutaHtml = join(rutaSalida, 'diagrama_bd.html')
  writeFileSync(rutaMmd, mermaid, 'utf8')
  writeFileSync(rutaHtml, generarHtml(mermaid, estadisticas), 'utf8')

  console.log('Diagrama generado')
  console.log('  ' + estadisticas)
  console.log('  Mermaid: ' + rutaMmd)
  console.log('  Visor:   ' + rutaHtml)
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  principal()
}

export { parsearModelos, generarMermaid, generarHtml }
