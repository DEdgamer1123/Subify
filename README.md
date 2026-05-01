# SRT Automático

Automatización de subtítulos SRT: Audio → Voxtral (transcripción) → Gemini/Claude (corrección) → SRT.

## Requisitos

- Node.js 18+
- Python 3.8+
- ffmpeg en el PATH

## Instalación

```bash
# 1. Instalar dependencias de Electron
npm install

# 2. Instalar dependencias de Python
pip install pydub requests google-auth google-auth-oauthlib google-auth-httplib2 google-api-python-client google-genai

# 3. Correr la app
npm start
```

## Seguridad — API Keys y credenciales

Todas las credenciales se guardan en `~/.srt_automatico/` (fuera del proyecto, nunca en el repo):

| Archivo | Contenido |
|---|---|
| `~/.srt_automatico/config.json` | API keys de Voxtral, Gemini, Claude |
| `~/.srt_automatico/credentials.json` | Credenciales OAuth de Google Drive |
| `~/.srt_automatico/token_drive.json` | Token de sesión de Drive (se genera automáticamente) |

**Nunca subas ninguno de estos archivos a GitHub.**

## Google Drive (opcional)

Para usar la fuente desde Drive necesitas `credentials.json`:

1. Ve a https://console.cloud.google.com
2. Crea un proyecto → activa la **Google Drive API**
3. Credenciales → Crear → ID de cliente OAuth → Aplicación de escritorio
4. Descarga el JSON y renómbralo `credentials.json`
5. Muévelo a `~/.srt_automatico/credentials.json`
6. La primera vez se abrirá el navegador para autorizar — solo esa vez

## Estructura

```
SRT-Automatico/
├── main.js              # Proceso principal Electron
├── preload.js           # Bridge seguro IPC
├── index.html           # Interfaz
├── SRT_Automatico.py    # Backend Python
├── package.json
└── .gitignore

~/.srt_automatico/       # Fuera del repo — nunca se sube a git
├── config.json          # API keys
├── credentials.json     # OAuth Google Drive
└── token_drive.json     # Token de sesión (auto-generado)

## Descargas

Descarga los instaladores desde la sección [Releases](https://github.com/DEdgamer1123/Subify/releases):

- **Subify Setup X.X.X.exe** — Instalador para Windows
- **Subify X.X.X (Portable).exe** — Versión portable (sin instalación)
```