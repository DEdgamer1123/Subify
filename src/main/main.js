const { app, BrowserWindow, ipcMain, dialog, shell, Menu } = require('electron')
const path   = require('path')
const os     = require('os')
const fs     = require('fs')
const { spawn } = require('child_process')

// ── Módulo de renombrado ───────────────────────────────────────────
// Ubicado junto a main.js en src/main/ (o en la misma carpeta que main.js)
const renamer = require('./renamer')

// ── Rutas del proyecto ─────────────────────────────────────────────
// __dirname = RAIZ/src/main  →  ROOT = RAIZ
const ROOT = path.join(__dirname, '..', '..')

// Scripts Python están en src/scripts/
const SCRIPT_PY  = path.join(ROOT, 'src', 'scripts', 'SRT_Automatico.py')
const WORKDIR    = path.join(ROOT, 'srt_trabajo')

// ── Config segura (fuera del repo) ────────────────────────────────
const CONFIG_DIR  = path.join(os.homedir(), '.srt_automatico')
const CONFIG_FILE = path.join(CONFIG_DIR, 'config.json')
const TOKEN_FILE  = path.join(CONFIG_DIR, 'token_drive.json')
const CREDS_PATH  = path.join(CONFIG_DIR, 'credentials.json')

function leerConfig() {
  try {
    if (!fs.existsSync(CONFIG_DIR)) fs.mkdirSync(CONFIG_DIR, { recursive: true })
    if (!fs.existsSync(CONFIG_FILE)) return {}
    return JSON.parse(fs.readFileSync(CONFIG_FILE, 'utf8'))
  } catch { return {} }
}

function guardarConfig(data) {
  if (!fs.existsSync(CONFIG_DIR)) fs.mkdirSync(CONFIG_DIR, { recursive: true })
  fs.writeFileSync(CONFIG_FILE, JSON.stringify(data, null, 2), 'utf8')
}

// ── Ventana principal ──────────────────────────────────────────────
function createWindow() {
  const win = new BrowserWindow({
    width:           920,
    height:          700,
    minWidth:        780,
    minHeight:       580,
    frame:           false,
    titleBarStyle:   'hidden',
    title:           'Subify',
    backgroundColor: '#080914',
    webPreferences: {
      preload:          path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration:  false,
    },
    icon: path.join(ROOT, 'assets', 'logo.ico'),
  })
  win.loadFile(path.join(ROOT, 'src', 'renderer', 'index.html'))
}

app.whenReady().then(() => {
  Menu.setApplicationMenu(null)
  createWindow()
})
app.on('window-all-closed', () => { if (process.platform !== 'darwin') app.quit() })

// ── IPC: config ───────────────────────────────────────────────────
ipcMain.handle('config:read',  ()       => leerConfig())
ipcMain.handle('config:write', (_, cfg) => { guardarConfig(cfg); return true })

// ── IPC: ventana ──────────────────────────────────────────────────
ipcMain.on('window:minimize', e => BrowserWindow.fromWebContents(e.sender).minimize())
ipcMain.on('window:maximize', e => {
  const win = BrowserWindow.fromWebContents(e.sender)
  win.isMaximized() ? win.unmaximize() : win.maximize()
})
ipcMain.on('window:close', e => BrowserWindow.fromWebContents(e.sender).close())

// ── IPC: diálogos ─────────────────────────────────────────────────
ipcMain.handle('dialog:carpeta', async () => {
  const result = await dialog.showOpenDialog({ properties: ['openDirectory'] })
  return result.canceled ? null : result.filePaths[0]
})
ipcMain.handle('dialog:salida', async () => {
  const result = await dialog.showOpenDialog({ properties: ['openDirectory'] })
  return result.canceled ? null : result.filePaths[0]
})

// ── IPC: abrir carpeta/URL ────────────────────────────────────────
ipcMain.on('shell:openFolder', (_, ruta) => shell.openPath(ruta))

// ── IPC: Nueva serie ──────────────────────────────────────────────
ipcMain.handle('serie:check', () => {
  // Verifica si hay SRT en voxtral_output para advertir al usuario
  const voxtralOutput = path.join(WORKDIR, 'voxtral_output')
  if (!fs.existsSync(voxtralOutput)) return { tieneSRT: false, cantidad: 0 }
  const srts = fs.readdirSync(voxtralOutput).filter(f => f.endsWith('.srt'))
  return { tieneSRT: srts.length > 0, cantidad: srts.length }
})

ipcMain.handle('serie:guardarYLimpiar', async (event) => {
  // Diálogo para elegir carpeta destino
  const resultado = await dialog.showOpenDialog({
    title:       'Guardar SRT de la serie actual en...',
    properties:  ['openDirectory', 'createDirectory'],
    buttonLabel: 'Guardar aquí',
  })
  if (resultado.canceled) return { ok: false, motivo: 'cancelado' }

  const destino       = resultado.filePaths[0]
  const voxtralOutput = path.join(WORKDIR, 'voxtral_output')

  try {
    // Copiar todos los SRT al destino
    const srts = fs.readdirSync(voxtralOutput).filter(f => f.endsWith('.srt'))
    for (const srt of srts) {
      fs.copyFileSync(
        path.join(voxtralOutput, srt),
        path.join(destino, srt)
      )
    }
    // Limpiar carpeta de trabajo
    limpiarWorkdir(voxtralOutput)
    return { ok: true, guardados: srts.length, destino }
  } catch (e) {
    return { ok: false, motivo: e.message }
  }
})

ipcMain.handle('serie:limpiarSinGuardar', () => {
  const voxtralOutput = path.join(WORKDIR, 'voxtral_output')
  try {
    limpiarWorkdir(voxtralOutput)
    return { ok: true }
  } catch (e) {
    return { ok: false, motivo: e.message }
  }
})

// ── IPC: limpiar token de Drive (fuerza re-autenticación) ─────────
// Útil cuando el token fue revocado y el usuario necesita reconectar.
ipcMain.handle('drive:resetToken', () => {
  try {
    if (fs.existsSync(TOKEN_FILE)) {
      fs.unlinkSync(TOKEN_FILE)
      console.log('[drive:resetToken] Token eliminado:', TOKEN_FILE)
      return { ok: true }
    }
    return { ok: true, msg: 'No había token guardado' }
  } catch (e) {
    return { ok: false, error: e.message }
  }
})

function limpiarWorkdir(carpeta) {
  if (!fs.existsSync(carpeta)) return
  for (const archivo of fs.readdirSync(carpeta)) {
    fs.rmSync(path.join(carpeta, archivo), { force: true })
  }
}

// ── IPC: ejecutar Voxtral (transcripción) ────────────────────────
ipcMain.on('python:run', (event, args) => {
  const python = process.platform === 'win32' ? 'python' : 'python3'

  // Debug: verificar que el script existe antes de ejecutar
  console.log('[python:run] SCRIPT_PY:', SCRIPT_PY)
  console.log('[python:run] existe:', fs.existsSync(SCRIPT_PY))
  console.log('[python:run] desdePC:', args.desdePC)
  console.log('[python:run] carpetaAudio:', args.carpetaAudio)
  console.log('[python:run] driveId:', args.driveId)
  console.log('[python:run] carpetaSalida:', args.carpetaSalida)

  if (!fs.existsSync(SCRIPT_PY)) {
    event.sender.send('python:stderr', `❌ Script no encontrado: ${SCRIPT_PY}\n`)
    event.sender.send('python:done', 1)
    return
  }

  const env = {
    ...process.env,
    SRT_VOXTRAL_KEY:      args.voxtralKey,
    SRT_DESDE_PC:         args.desdePC ? '1' : '0',
    SRT_CARPETA_AUDIO:    args.carpetaAudio    || '',
    SRT_DRIVE_ID:         args.driveId         || '',
    SRT_CARPETA_SALIDA:   args.carpetaSalida   || '',
    SRT_GUARDAR_AUDIO:    args.guardarAudio    ? '1' : '0',
    SRT_SOBRESCRIBIR_SRT: args.sobrescribirSrt ? '1' : '0',
    SRT_SOBRESCRIBIR_WAV: args.sobrescribirWav ? '1' : '0',
    SRT_CREDS_PATH:       CREDS_PATH,
    SRT_TOKEN_PATH:       TOKEN_FILE,
    SRT_WORKDIR:          WORKDIR,
    SRT_OPENROUTER_KEY:   args.openrouterKey   || '',
    SRT_OPENROUTER_MODEL: args.openrouterModel || 'minimax/minimax-m2.5:free',
    SRT_USAR_CORRECCION: args.usarCorreccion ? '1' : '0',
  }

  const proc = spawn(python, [SCRIPT_PY], { env })
  proc.stdout.on('data', d => event.sender.send('python:stdout', d.toString()))
  proc.stderr.on('data', d => event.sender.send('python:stderr', d.toString()))
  proc.on('close',  c  => event.sender.send('python:done', c))
  proc.on('error', err => {
    event.sender.send('python:stderr', `Error: ${err.message}\n`)
    event.sender.send('python:done', 1)
  })
})

// ══════════════════════════════════════════════════════════════
//  HELPERS — duración desde SRT
//  Lee el timestamp final del último bloque de cada .srt
//  Formato: HH:MM:SS,mmm --> HH:MM:SS,mmm
// ══════════════════════════════════════════════════════════════

function parseSrtTimestamp(ts) {
  // "01:02:03,456" → segundos
  const m = ts.trim().match(/(\d+):(\d+):(\d+)[,.](\d+)/)
  if (!m) return 0
  return parseInt(m[1]) * 3600 + parseInt(m[2]) * 60 + parseInt(m[3]) + parseInt(m[4]) / 1000
}

function duracionDesdeSrt(fullPath) {
  try {
    const content = fs.readFileSync(fullPath, 'utf8')
    // Extraer todos los timestamps "fin" de la línea "inicio --> fin"
    const matches = [...content.matchAll(/\d+:\d+:\d+[,.]\d+\s+-->\s+(\d+:\d+:\d+[,.]\d+)/g)]
    if (!matches.length) return 0
    const ultimoFin = matches[matches.length - 1][1]
    return parseSrtTimestamp(ultimoFin)
  } catch (_) { return 0 }
}

// ══════════════════════════════════════════════════════════════
//  IPC: ESTADÍSTICAS DE CARPETA
//  - audioDir: carpeta con archivos de audio (para peso + conteo + duración)
//  - srtDir:   carpeta con SRTs (si no hay audios, calcula duración desde SRT)
//  El peso reportado es SOLO de audios (binario, igual que Windows Explorer).
// ══════════════════════════════════════════════════════════════

ipcMain.handle('folder:stats', async (_, { dir, srtDir }) => {
  const AUDIO_EXTS = new Set(['.wav', '.mp3', '.mp4', '.m4a', '.ogg', '.flac', '.aac', '.wma'])
  const SRT_EXT    = '.srt'

  const resolvedDir    = dir    || null
  const resolvedSrtDir = srtDir || dir   // si no se pasa srtDir, buscar SRTs en el mismo dir

  if (!resolvedDir || !fs.existsSync(resolvedDir)) {
    return { ok: false, error: 'Carpeta no encontrada', archivos: 0, totalBytes: 0, duracionSeg: 0 }
  }

  try {
    let mm = null
    try { mm = require('music-metadata') } catch (_) {}

    let totalBytes  = 0
    let archivos    = 0
    let duracionSeg = 0

    // ── 1. Leer archivos de audio ──────────────────────────
    const entries = fs.readdirSync(resolvedDir)
    const audioFiles = entries.filter(f => AUDIO_EXTS.has(path.extname(f).toLowerCase()))

    for (const file of audioFiles) {
      const fullPath = path.join(resolvedDir, file)
      const stat     = fs.statSync(fullPath)
      totalBytes += stat.size
      archivos++

      if (mm) {
        try {
          const meta = await mm.parseFile(fullPath, { duration: true })
          duracionSeg += meta.format.duration || 0
        } catch (_) {}
      }
    }

    // ── 2. Si no se obtuvo duración de audio, calcular desde SRTs ──
    // Esto cubre el caso Drive (sin audios en disco) y el caso donde
    // music-metadata falla en algunos WAV.
    if (duracionSeg === 0 && resolvedSrtDir && fs.existsSync(resolvedSrtDir)) {
      const srtFiles = fs.readdirSync(resolvedSrtDir).filter(f => f.toLowerCase().endsWith(SRT_EXT))
      let durSrt = 0
      for (const srt of srtFiles) {
        durSrt += duracionDesdeSrt(path.join(resolvedSrtDir, srt))
      }
      if (durSrt > 0) {
        duracionSeg = durSrt
        console.log(`[folder:stats] Duración calculada desde ${srtFiles.length} SRT(s): ${durSrt.toFixed(0)}s`)
      }
    }

    // ── 3. Si no hay audios en disco pero sí SRTs, al menos contar episodios ──
    if (archivos === 0 && resolvedSrtDir && fs.existsSync(resolvedSrtDir)) {
      archivos = fs.readdirSync(resolvedSrtDir).filter(f => f.toLowerCase().endsWith(SRT_EXT)).length
    }

    console.log(`[folder:stats] dir=${resolvedDir} archivos=${archivos} bytes=${totalBytes} duracion=${duracionSeg.toFixed(0)}s`)
    return { ok: true, archivos, totalBytes, duracionSeg }
  } catch (err) {
    return { ok: false, error: err.message, archivos: 0, totalBytes: 0, duracionSeg: 0 }
  }
})

// ══════════════════════════════════════════════════════════════
//  IPC: RENOMBRADO — llamado DESPUÉS de que Python termina
// ══════════════════════════════════════════════════════════════

/**
 * rename:local
 * Renombra audios y/o SRTs en carpetas locales (fuente = PC).
 */
ipcMain.handle('rename:local', (_, { audioDir, srtDir, renameAudio, renameSrt }) => {
  console.log('[rename:local] audioDir:', audioDir, '| srtDir:', srtDir)
  try {
    const result = renamer.processLocalFiles({
      audioDir,
      srtDir:      srtDir  || null,
      renameAudio: renameAudio !== false,
      renameSrt:   renameSrt  !== false,
    })
    console.log('[rename:local] renamed audio:', result.audio.renamed)
    console.log('[rename:local] renamed srt:  ', result.srt.renamed)
    console.log('[rename:local] errors:       ', [...result.audio.errors, ...result.srt.errors])
    return { ok: true, ...result }
  } catch (err) {
    console.error('[rename:local] ERROR:', err.message)
    return { ok: false, error: err.message }
  }
})

/**
 * rename:drive
 * Renombra audios descargados de Drive y SRTs generados.
 * Se llama DESPUÉS de que python:done dispara con code=0.
 */
ipcMain.handle('rename:drive', (_, { audioDir, srtDir, renameAudio, renameSrt }) => {
  console.log('[rename:drive] audioDir:', audioDir, '| srtDir:', srtDir)
  try {
    const result = renamer.processDriveFiles({
      audioDir,
      srtDir:      srtDir  || null,
      renameAudio: renameAudio !== false,
      renameSrt:   renameSrt  !== false,
    })
    console.log('[rename:drive] renamed audio:', result.audio.renamed)
    console.log('[rename:drive] renamed srt:  ', result.srt.renamed)
    console.log('[rename:drive] errors:       ', [...result.audio.errors, ...result.srt.errors])
    return { ok: true, ...result }
  } catch (err) {
    console.error('[rename:drive] ERROR:', err.message)
    return { ok: false, error: err.message }
  }
})

/**
 * rename:scan
 * Preview sin tocar disco — muestra cómo quedarían los archivos.
 */
ipcMain.handle('rename:scan', (_, { dir }) => {
  try {
    return { ok: true, ...renamer.scanFolder(dir) }
  } catch (err) {
    return { ok: false, error: err.message }
  }
})