'use strict'

/**
 * renamer.js
 * Módulo de lógica pura para:
 *   - Detectar número de episodio en un nombre de archivo
 *   - Renombrar audios y SRTs en disco
 *   - Procesar archivos desde carpeta local (PC)
 *   - Procesar archivos descargados desde Google Drive
 *
 * No tiene dependencias de Electron ni del proceso principal.
 * Importado desde main.js.
 */

const fs   = require('fs')
const path = require('path')

// ─────────────────────────────────────────────────────────────
//  CONSTANTES
// ─────────────────────────────────────────────────────────────

/** Extensiones de audio que reconocemos */
const AUDIO_EXTS = new Set(['.wav', '.mp3', '.mp4', '.m4a', '.ogg', '.flac', '.aac', '.wma'])

/** Extensiones de subtítulos que renombramos junto con el audio */
const SUB_EXTS = new Set(['.srt', '.vtt'])

// ─────────────────────────────────────────────────────────────
//  parseEpisodeNumber(filename) → number | null
// ─────────────────────────────────────────────────────────────

/**
 * Intenta extraer el número de episodio de un nombre de archivo.
 *
 * Estrategia (orden de prioridad):
 *   1. Prefijos explícitos: ep, episode, episodio, cap, capitulo, chapter, e, s\d+e
 *      ej. "ep12_audio.wav" → 12, "S02E05.wav" → 5 (número de ep, no temporada)
 *   2. Secuencias de dígitos precedidas de guion bajo, guion o punto
 *      ej. "audio_003.wav" → 3, "track-07.mp3" → 7
 *   3. Secuencias de dígitos al principio del nombre
 *      ej. "01 intro.wav" → 1
 *   4. Cualquier secuencia de dígitos encontrada (último recurso)
 *      ej. "miArchivo42.wav" → 42
 *
 * @param {string} filename  Nombre de archivo (con o sin extensión)
 * @returns {number|null}    Número de episodio, o null si no se detecta
 */
function parseEpisodeNumber(filename) {
  // Trabajamos solo con el stem (sin extensión), en minúsculas
  const stem = path.parse(filename).name.toLowerCase()

  // 1. Prefijos explícitos con número
  const prefixPatterns = [
    /(?:s\d+e|episode|episodio|capitulo|chapter|ep|cap|e)[\s._-]*0*(\d+)/i,
  ]
  for (const re of prefixPatterns) {
    const m = stem.match(re)
    if (m) return parseInt(m[1], 10)
  }

  // 2. Dígitos tras separador (_  -  .)
  const afterSep = stem.match(/[_\-.]0*(\d+)(?:[_\-.]|$)/)
  if (afterSep) return parseInt(afterSep[1], 10)

  // 3. Dígitos al inicio del stem
  const atStart = stem.match(/^0*(\d+)/)
  if (atStart) return parseInt(atStart[1], 10)

  // 4. Cualquier secuencia de dígitos (último recurso)
  const anywhere = stem.match(/0*(\d+)/)
  if (anywhere) return parseInt(anywhere[1], 10)

  return null
}

// ─────────────────────────────────────────────────────────────
//  formatEpisodeNumber(n) → string
// ─────────────────────────────────────────────────────────────

/**
 * Formatea un número de episodio con cero a la izquierda si < 10.
 * ej. 1 → "01", 12 → "12", 100 → "100"
 */
function formatEpisodeNumber(n) {
  if (n < 10) return `0${n}`
  return String(n)
}

// ─────────────────────────────────────────────────────────────
//  buildRenameMap(files, opts) → Array<{ from, to, ep }>
// ─────────────────────────────────────────────────────────────

/**
 * Construye el mapa de renombrado para una lista de archivos.
 *
 * @param {string[]} files  Lista de nombres de archivo (solo nombre, sin ruta)
 * @param {object}   opts
 * @param {string}   opts.dir        Carpeta donde viven los archivos
 * @param {Set}      opts.validExts  Conjunto de extensiones a procesar
 * @returns {{ ok: Array<{from,to,ep}>, skipped: string[] }}
 *   ok      → archivos con número detectado, listos para renombrar
 *   skipped → archivos sin número detectado (no se tocan)
 */
function buildRenameMap(files, { dir, validExts }) {
  const ok      = []
  const skipped = []

  for (const file of files) {
    const ext = path.extname(file).toLowerCase()
    if (!validExts.has(ext)) continue        // extensión no relevante

    const ep = parseEpisodeNumber(file)
    if (ep === null) {
      skipped.push(file)
      continue
    }

    const newName = `${formatEpisodeNumber(ep)}${ext.toUpperCase() === ext ? ext : ext}`
    ok.push({
      from: path.join(dir, file),
      to:   path.join(dir, newName),
      ep,
      originalName: file,
      newName,
    })
  }

  // Ordenar por número de episodio
  ok.sort((a, b) => a.ep - b.ep)

  return { ok, skipped }
}

// ─────────────────────────────────────────────────────────────
//  renameFiles(map) → { renamed, errors }
// ─────────────────────────────────────────────────────────────

/**
 * Ejecuta el renombrado en disco para un mapa construido por buildRenameMap.
 * Si el destino ya existe y es diferente del origen, no sobreescribe.
 *
 * @param {Array<{from,to}>} map
 * @returns {{ renamed: string[], errors: Array<{file,reason}> }}
 */
function renameFiles(map) {
  const renamed = []
  const errors  = []

  for (const { from, to, originalName, newName } of map) {
    try {
      // Nada que hacer si ya tiene el nombre correcto
      if (from === to) {
        renamed.push(newName)
        continue
      }

      // No sobreescribir un archivo distinto que ya existe en destino
      if (fs.existsSync(to) && from !== to) {
        errors.push({ file: originalName, reason: `El destino "${newName}" ya existe` })
        continue
      }

      fs.renameSync(from, to)
      renamed.push(newName)
    } catch (err) {
      errors.push({ file: originalName, reason: err.message })
    }
  }

  return { renamed, errors }
}

// ─────────────────────────────────────────────────────────────
//  processLocalFiles(opts) → result
// ─────────────────────────────────────────────────────────────

/**
 * Renombra audios y SRTs en disco.
 *
 * Si audioDir y srtDir son la misma carpeta (caso Drive: todo en carpetaSalida),
 * hace un único escaneo y renombra ambos tipos en un solo paso.
 * Si son carpetas distintas (caso PC: audios en una, SRTs en otra),
 * procesa cada carpeta por separado.
 *
 * @param {object}  opts
 * @param {string}  opts.audioDir      Carpeta con los audios (puede ser null si no hay audios que renombrar)
 * @param {string}  [opts.srtDir]      Carpeta con los SRT (si omite, usa audioDir)
 * @param {boolean} opts.renameAudio   Si renombrar audios
 * @param {boolean} opts.renameSrt     Si renombrar SRTs
 */
function processLocalFiles({ audioDir, srtDir, renameAudio = true, renameSrt = true }) {
  const result = {
    audio: { renamed: [], errors: [], skipped: [] },
    srt:   { renamed: [], errors: [], skipped: [] },
  }

  const resolvedSrtDir = srtDir || audioDir

  // ── Caso: misma carpeta para audios y SRTs ───────────────────
  // Hacer un único escaneo y renombrar todos los tipos en una pasada.
  if (audioDir && resolvedSrtDir && audioDir === resolvedSrtDir) {
    if (!fs.existsSync(audioDir)) return result

    const files   = fs.readdirSync(audioDir)
    const allExts = new Set()
    if (renameAudio) AUDIO_EXTS.forEach(e => allExts.add(e))
    if (renameSrt)   SUB_EXTS.forEach(e => allExts.add(e))

    const { ok, skipped } = buildRenameMap(files, { dir: audioDir, validExts: allExts })
    const { renamed, errors } = renameFiles(ok)

    // Separar resultados por tipo para el informe
    for (const name of renamed) {
      const ext = path.extname(name).toLowerCase()
      if (AUDIO_EXTS.has(ext)) result.audio.renamed.push(name)
      else if (SUB_EXTS.has(ext)) result.srt.renamed.push(name)
    }
    for (const err of errors) {
      const ext = path.extname(err.file).toLowerCase()
      if (AUDIO_EXTS.has(ext)) result.audio.errors.push(err)
      else if (SUB_EXTS.has(ext)) result.srt.errors.push(err)
    }
    result.audio.skipped = skipped.filter(f => AUDIO_EXTS.has(path.extname(f).toLowerCase()))
    result.srt.skipped   = skipped.filter(f => SUB_EXTS.has(path.extname(f).toLowerCase()))

    return result
  }

  // ── Caso: carpetas distintas (PC) ────────────────────────────
  if (renameAudio && audioDir && fs.existsSync(audioDir)) {
    const files = fs.readdirSync(audioDir)
    const { ok, skipped } = buildRenameMap(files, { dir: audioDir, validExts: AUDIO_EXTS })
    const { renamed, errors } = renameFiles(ok)
    result.audio = { renamed, errors, skipped }
  }

  if (renameSrt && resolvedSrtDir && fs.existsSync(resolvedSrtDir)) {
    const files = fs.readdirSync(resolvedSrtDir)
    const { ok, skipped } = buildRenameMap(files, { dir: resolvedSrtDir, validExts: SUB_EXTS })
    const { renamed, errors } = renameFiles(ok)
    result.srt = { renamed, errors, skipped }
  }

  return result
}

// ─────────────────────────────────────────────────────────────
//  processDriveFiles(opts) → result
// ─────────────────────────────────────────────────────────────

/**
 * Renombra archivos tras descarga desde Google Drive.
 * Cuando el usuario guarda audios y SRTs en la misma carpeta,
 * ambos tipos se renombran en un único escaneo.
 */
function processDriveFiles({ audioDir, srtDir, renameAudio = true, renameSrt = true }) {
  return processLocalFiles({ audioDir, srtDir, renameAudio, renameSrt })
}

// ─────────────────────────────────────────────────────────────
//  scanFolder(dir) → { audios, srts, unknown }
// ─────────────────────────────────────────────────────────────

/**
 * Escanea una carpeta y clasifica los archivos encontrados.
 * Útil para mostrar un preview en la UI antes de renombrar.
 *
 * @param {string} dir
 * @returns {{
 *   audios:  Array<{ file, ep, newName }>,
 *   srts:    Array<{ file, ep, newName }>,
 *   unknown: string[]   // archivos sin número detectado
 * }}
 */
function scanFolder(dir) {
  if (!fs.existsSync(dir)) return { audios: [], srts: [], unknown: [] }

  const files   = fs.readdirSync(dir)
  const audios  = []
  const srts    = []
  const unknown = []

  for (const file of files) {
    const ext = path.extname(file).toLowerCase()
    const ep  = parseEpisodeNumber(file)

    if (ep === null) {
      unknown.push(file)
      continue
    }

    const newName = `${formatEpisodeNumber(ep)}${ext}`

    if (AUDIO_EXTS.has(ext)) {
      audios.push({ file, ep, newName })
    } else if (SUB_EXTS.has(ext)) {
      srts.push({ file, ep, newName })
    }
  }

  audios.sort((a, b) => a.ep - b.ep)
  srts.sort((a, b) => a.ep - b.ep)

  return { audios, srts, unknown }
}

// ─────────────────────────────────────────────────────────────
//  EXPORTS
// ─────────────────────────────────────────────────────────────

module.exports = {
  parseEpisodeNumber,
  formatEpisodeNumber,
  renameFiles,
  processLocalFiles,
  processDriveFiles,
  scanFolder,
}