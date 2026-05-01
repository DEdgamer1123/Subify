const { contextBridge, ipcRenderer } = require('electron')

contextBridge.exposeInMainWorld('api', {
  // Config
  readConfig:    ()      => ipcRenderer.invoke('config:read'),
  writeConfig:   (cfg)   => ipcRenderer.invoke('config:write', cfg),

  // Ventana
  minimize:      ()      => ipcRenderer.send('window:minimize'),
  maximize:      ()      => ipcRenderer.send('window:maximize'),
  close:         ()      => ipcRenderer.send('window:close'),

  // Diálogos
  elegirCarpeta: ()      => ipcRenderer.invoke('dialog:carpeta'),
  elegirSalida:  ()      => ipcRenderer.invoke('dialog:salida'),
  abrirCarpeta:  (ruta)  => ipcRenderer.send('shell:openFolder', ruta),

  // Nueva serie
  checkSerie:        ()  => ipcRenderer.invoke('serie:check'),
  guardarYLimpiar:   ()  => ipcRenderer.invoke('serie:guardarYLimpiar'),
  limpiarSinGuardar: ()  => ipcRenderer.invoke('serie:limpiarSinGuardar'),

  // Flujo Voxtral (transcripción)
  runPython:   (args) => ipcRenderer.send('python:run', args),
  onStdout:    (cb)   => ipcRenderer.on('python:stdout', (_, d) => cb(d)),
  onStderr:    (cb)   => ipcRenderer.on('python:stderr', (_, d) => cb(d)),
  onDone:      (cb)   => ipcRenderer.on('python:done',   (_, c) => cb(c)),

  // Claude Web manual (pestaña independiente) — ELIMINADO

  removeListeners: () => {
    ipcRenderer.removeAllListeners('python:stdout')
    ipcRenderer.removeAllListeners('python:stderr')
    ipcRenderer.removeAllListeners('python:done')
  },

  // ── Estadísticas de carpeta (peso + conteo) ───────────────
  folderStats: (args) => ipcRenderer.invoke('folder:stats', args),

  // ── Drive: forzar re-autenticación eliminando token ───────
  driveResetToken: () => ipcRenderer.invoke('drive:resetToken'),

  // ── Renombrado ────────────────────────────────────────────
  renameScan:  (args) => ipcRenderer.invoke('rename:scan',  args),
  renameLocal: (args) => ipcRenderer.invoke('rename:local', args),
  renameDrive: (args) => ipcRenderer.invoke('rename:drive', args),
})