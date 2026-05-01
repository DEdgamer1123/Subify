# ============================================================
#  SRT_Automatico.py — Transcripción con Voxtral + OpenRouter
#
#  Flujo: Audio → FLAC → Voxtral (words) → Agrupar → LLM → SRT
# ============================================================

import os, sys, json, glob, re, requests, time
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)
sys.stderr.reconfigure(encoding='utf-8', line_buffering=True)
sys.stderr.reconfigure(encoding='utf-8', line_buffering=True)

# ── Config desde variables de entorno (Electron) ───────────
VOXTRAL_KEY       = os.environ.get('SRT_VOXTRAL_KEY',       '')
OPENROUTER_KEY    = os.environ.get('SRT_OPENROUTER_KEY',    '')
OPENROUTER_MODEL  = os.environ.get('SRT_OPENROUTER_MODEL',  'minimax/minimax-m2.5:free')
USAR_CORRECCION  = os.environ.get('SRT_USAR_CORRECCION',  '0') == '1'
DESDE_PC          = os.environ.get('SRT_DESDE_PC',          '1') == '1'
CARPETA_AUDIO     = os.environ.get('SRT_CARPETA_AUDIO',     '')
DRIVE_ID          = os.environ.get('SRT_DRIVE_ID',          '')
CARPETA_SALIDA    = os.environ.get('SRT_CARPETA_SALIDA',    '')
CREDS_PATH        = os.environ.get('SRT_CREDS_PATH',        '')
TOKEN_PATH        = os.environ.get('SRT_TOKEN_PATH',        '')
GUARDAR_AUDIO     = os.environ.get('SRT_GUARDAR_AUDIO',     '0') == '1'
SOBRESCRIBIR_SRT  = os.environ.get('SRT_SOBRESCRIBIR_SRT',  '0') == '1'
SOBRESCRIBIR_WAV  = os.environ.get('SRT_SOBRESCRIBIR_WAV',  '0') == '1'

FORMATOS_AUDIO = ['.wav', '.mp3', '.mp4', '.m4a', '.ogg', '.flac']

WORKDIR = os.environ.get('SRT_WORKDIR') or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), '..', '..', 'srt_trabajo'
)
os.makedirs(WORKDIR, exist_ok=True)


# ══════════════════════════════════════════════════════════════
#  EXTRACTOR DE NÚMERO DE EPISODIO
# ══════════════════════════════════════════════════════════════

def extraer_numero_ep(nombre):
    stem = Path(nombre).stem.lower()
    m = re.search(r'(?:s\d+e|episode|episodio|capitulo|chapter|ep|cap|e)[\s._-]*0*(\d+)', stem, re.I)
    if m:
        return int(m.group(1))
    m = re.search(r'[_\-.]0*(\d+)(?:[_\-.]|$)', stem)
    if m:
        return int(m.group(1))
    m = re.match(r'^0*(\d+)', stem)
    if m:
        return int(m.group(1))
    m = re.search(r'0*(\d+)', stem)
    if m:
        return int(m.group(1))
    return None


def construir_mapa_ep(carpeta, extensiones):
    mapa = {}
    if not os.path.isdir(carpeta):
        return mapa
    for f in os.listdir(carpeta):
        if Path(f).suffix.lower() in extensiones:
            ep = extraer_numero_ep(f)
            if ep is not None:
                mapa[ep] = os.path.join(carpeta, f)
    return mapa


# ══════════════════════════════════════════════════════════════
#  GOOGLE DRIVE
# ══════════════════════════════════════════════════════════════

def conectar_drive():
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
    import google.auth.exceptions

    SCOPES = ['https://www.googleapis.com/auth/drive.readonly']
    creds  = None

    if TOKEN_PATH and os.path.exists(TOKEN_PATH):
        creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except google.auth.exceptions.RefreshError:
                print('⚠️  Token de Drive expirado/revocado. Re-autenticando...')
                creds = None
                if TOKEN_PATH and os.path.exists(TOKEN_PATH):
                    os.remove(TOKEN_PATH)

        if not creds or not creds.valid:
            if not CREDS_PATH or not os.path.exists(CREDS_PATH):
                raise Exception(
                    'No se encontró credentials.json.\n'
                    'Consulta la pestaña Ayuda para configurar Google Drive.'
                )
            flow  = InstalledAppFlow.from_client_secrets_file(CREDS_PATH, SCOPES)
            creds = flow.run_local_server(port=0)

        if TOKEN_PATH:
            with open(TOKEN_PATH, 'w') as f:
                f.write(creds.to_json())

    return build('drive', 'v3', credentials=creds)


def listar_audios_drive(service, carpeta_id):
    archivos, page_token = [], None
    while True:
        params = {
            'q':        f"'{carpeta_id}' in parents and trashed=false",
            'fields':   'nextPageToken, files(id, name, size)',
            'pageSize': 100,
        }
        if page_token:
            params['pageToken'] = page_token
        result = service.files().list(**params).execute()
        for f in result.get('files', []):
            if Path(f['name']).suffix.lower() in FORMATOS_AUDIO:
                archivos.append(f)
        page_token = result.get('nextPageToken')
        if not page_token:
            break
    return archivos


def descargar_drive(service, file_id, nombre):
    from googleapiclient.http import MediaIoBaseDownload
    ruta = os.path.join(WORKDIR, nombre)
    req  = service.files().get_media(fileId=file_id)
    with open(ruta, 'wb') as f:
        dl = MediaIoBaseDownload(f, req)
        done = False
        while not done:
            _, done = dl.next_chunk()
    return ruta


# ══════════════════════════════════════════════════════════════
#  CONVERSIÓN A FLAC
# ══════════════════════════════════════════════════════════════

def convertir_a_flac(ruta):
    from pydub import AudioSegment
    if ruta.lower().endswith('.flac'):
        return ruta
    flac_path = os.path.join(WORKDIR, Path(ruta).stem + '.flac')
    audio = AudioSegment.from_file(ruta)
    audio = audio.set_channels(1).set_frame_rate(16000)
    audio.export(flac_path, format='flac')
    mb_a = os.path.getsize(ruta)      / 1024 / 1024
    mb_b = os.path.getsize(flac_path) / 1024 / 1024
    print(f'     FLAC: {mb_a:.1f} MB → {mb_b:.1f} MB')
    return flac_path


# ══════════════════════════════════════════════════════════════
#  TRANSCRIPCIÓN CON VOXTRAL (timestamps por palabra)
# ══════════════════════════════════════════════════════════════

def segundos_a_ts(seg):
    seg = max(0.0, float(seg))
    h, rem = divmod(seg, 3600)
    m, s   = divmod(rem, 60)
    ms     = round((s - int(s)) * 1000)
    return f'{int(h):02d}:{int(m):02d}:{int(s):02d},{ms:03d}'


def transcribir_a_palabras(ruta_audio, nombre):
    """
    Transcribe y devuelve lista de palabras con timestamps.
    cada segmento es una palabra individual (como CURL confirmó).
    """
    import sys
    import subprocess
    import tempfile
    import os
    url = 'https://api.mistral.ai/v1/audio/transcriptions'
    
    # Usar curl.exe directamente (como CURL funcionó)
    cmd = [
        'C:\\Windows\\System32\\curl.exe',
        '-X', 'POST',
        url,
        '-H', f'Authorization: Bearer {VOXTRAL_KEY}',
        '-F', f'file=@{ruta_audio}',
        '-F', 'model=voxtral-mini-latest',
        '-F', 'timestamp_granularities=word',
    ]
    
    # Crear archivo temporal para respuesta
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as tmp:
        tmp_path = tmp.name
    
    try:
        cmd.extend(['-o', tmp_path])
        
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode != 0:
            raise Exception(f'CURL failed: {result.stderr}')
        
        # Leer respuesta
        with open(tmp_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        segments = data.get('segments', [])
        print(f"     [VOXTRAL] segmentos/palabras: {len(segments)}")
        
        words = []
        for seg in segments:
            texto = seg.get('text', '').strip()
            if texto:
                words.append({
                    'text': texto,
                    'start': seg.get('start', 0),
                    'end': seg.get('end', 0)
                })
        
        print(f"     [VOXTRAL] Total words: {len(words)}")
        if words:
            print(f"     [VOXTRAL] Primera: {words[0]}")
        
        if not words:
            raise Exception('Voxtral no devolvió palabras.')
        
        return words
    
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


# ══════════════════════════════════════════════════════════════
#  FUNCIONES HELPER PARA SIGNOS
# ══════════════════════════════════════════════════════════════

def buscar_signo_en_lista(palabras, inicio, tipo='cierre'):
    """
    Busca signo de CIERRE (? !) o APERTURA (¿ ¡) desde inicio hacia DELANTE.
    Retorna índice o None.
    """
    for idx in range(inicio, len(palabras)):
        palabra = palabras[idx].get('text', '').strip()
        if tipo == 'cierre':
            if palabra.endswith('?') or palabra.endswith('!'):
                return idx
        else:  # tipo == 'apertura'
            if palabra.startswith('¿') or palabra.startswith('¡'):
                return idx
    return None


# ══════════════════════════════════════════════════════════════
#  PREPARAR BLOQUES (agrupar por pausa + límite 38 chars)
# ══════════════════════════════════════════════════════════════

def preparar_bloques(words, gap_threshold=0.3, max_chars=38):
    """
    Agrupa palabras en bloques (4 pasos en secuencia):
    1. Agrupar por pausa >= gap_threshold (0.3s)
    2a. Separar por PUNTOS (.) - siempre válido
    2b. Separar por COMAS (,) - con reglas
    3. Verificar max_chars y partir en partes iguales
    
    Retorna lista de bloques, cada bloque es lista de palabras.
    """
    if not words:
        return []

    words = [w for w in words if w.get('text', '').strip()]
    if not words:
        return []

    # ======== Paso 1: AGRUPAR por pausa ========
    bloques = [[words[0]]]
    
    for w in words[1:]:
        pausa = w['start'] - bloques[-1][-1]['end']
        
        if pausa >= gap_threshold:
            bloques.append([w])
        else:
            bloques[-1].append(w)

# ======== Paso 1b: SEPARAR por SIGNOS (¿, ¡, ?, !):] ========
    bloques_signos = []
    indices_procesados = set()  # Trackear por índice en lugar de string
    
    for bloque in bloques:
        partes = []
        bloque_actual = []
        bloque_procesado_por_apertura = False  # Trackear si este bloque ya fue procesado por signo de apertura
        
        for i, w in enumerate(bloque):  # enumerate para obtener índice
            palabra = w['text'].strip()
            bloque_actual.append(w)
            
            # TERMINAR subtítulo si termina en ? o !
            if palabra.endswith('?') or palabra.endswith('!'):
                # Si este bloque ya fue procesado por signo de apertura, no guardar de nuevo
                if bloque_procesado_por_apertura:
                    # Ya fue procesado, no guardar (evitar duplicación)
                    bloque_procesado_por_apertura = False  # Resetear para prochain bloque
                else:
                    # Es solo CIERRE -> guardar y crear nuevo subtítulo
                    partes.append(list(bloque_actual))
                # Limpiar los índices dentro de este bloque (los menores o iguales a i)
                indices_procesados = {idx for idx in indices_procesados if idx > i}
                bloque_actual = []
            
            # NUEVO subtítulo si empieza con ¿ o ¡
            elif palabra.startswith('¿') or palabra.startswith('¡'):
                # NUEVA LÓGICA: procesar hacia DELANTE buscando CIERRE
                idx_cierre = buscar_signo_en_lista(bloque, i + 1, 'cierre')
                
                if idx_cierre:
                    # Encontramos CIERRE antes -> crear bloque hasta ahí (incluye el CIERRE)
                    bloque_hasta_cierre = bloque[i:idx_cierre + 1]
                    partes.append(list(bloque_hasta_cierre))
                    
                    # Trackear índices hasta el cierre
                    for j in range(i, idx_cierre + 1):
                        indices_procesados.add(j)
                    
                    # Marcar como procesado
                    bloque_procesado_por_apertura = True
                    
                    # El resto将继续 naturalmente en siguientes iteraciones (solo comments in english)
                    # Limpiar bloque_actual y continuar desde el CIERRE + 1
                    bloque_actual = []
                    
                    # Verificar si después del CIERRE hay OTRO signo de APERTURA
                    idx_siguiente_apertura = buscar_signo_en_lista(bloque, idx_cierre + 1, 'apertura')
                    if idx_siguiente_apertura:
                        # Hay nuevo signo de apertura -> resetear flag para próximo processing
                        bloque_procesado_por_apertura = False
                else:
                    # No hay CIERRE en el resto -> mantener flujo original (procesar todo)
                    palabras_restantes = bloque[i + 1:]
                    
                    # VERIFICACIÓN 1: El texto total excede max_chars?
                    texto_total = palabra + ' ' + unir_texto(palabras_restantes)
                    
                    if len(texto_total) <= max_chars:
                        # NO excede, mantener todo junto
                        partes.append(list(bloque_actual) + palabras_restantes)
                    elif len(palabras_restantes) >= 2:
                        # VERIFICACIÓN 2: Hay ≥2 palabras para dividir
                        n = len(palabras_restantes)
                        tamano_parte1 = (n + 1) // 2
                        
                        parte1 = [bloque_actual[-1]] + palabras_restantes[:tamano_parte1]
                        parte2 = palabras_restantes[tamano_parte1:]
                        
                        texto1 = unir_texto(parte1)
                        texto2 = unir_texto(parte2) if parte2 else ""
                        
                        if len(texto1) <= max_chars and (not parte2 or len(texto2) <= max_chars):
                            partes.append(parte1)
                            if parte2:
                                partes.append(parte2)
                        else:
                            partes.append(list(bloque_actual) + palabras_restantes)
                    else:
                        partes.append(list(bloque_actual))
                    
                    # Trackear TODOS los índices
                    for j in range(i, len(bloque)):
                        indices_procesados.add(j)
                    
                    bloque_procesado_por_apertura = True
                    bloque_actual = []
        
        # Agregar lo que quede sin signos (solo si no fue procesado)
        if bloque_actual:
            # Verificar si el bloque tiene algún índice ya procesado
            tiene_indice_procesado = False
            for idx, palabra in enumerate(bloque):
                if idx in indices_procesados:
                    tiene_indice_procesado = True
                    break
            if not tiene_indice_procesado:
                partes.append(list(bloque_actual))
        
        # Agregar todas las partes
        for parte in partes:
            if parte:
                bloques_signos.append(parte)

    # ======== Paso 2a: SEPARAR por PUNTOS (.) ========
    bloques_puntos = []
    
    for bloque in bloques_signos:
        # Separar por CADA punto - crear nuevo bloque después de cada punto
        partes = []
        bloque_actual = []
        
        for w in bloque:
            palabra = w['text'].strip()
            bloque_actual.append(w)
            
            # Si la palabra termina en punto, crear nuevo bloque
            if palabra.endswith('.'):
                partes.append(list(bloque_actual))
                bloque_actual = []
        
        # Si la última palabra no terminaba en punto, agregarla
        if bloque_actual:
            partes.append(list(bloque_actual))
        
        # Agregar todas las partes
        for parte in partes:
            if parte:
                bloques_puntos.append(parte)

    # ======== Paso 2b: SEPARAR por COMAS (,) ========
    bloques_comas = []
    
    for bloque in bloques_puntos:
        texto_bloque = unir_texto(bloque)
        
        idx_coma = buscar_corte_coma(texto_bloque)
        
        if idx_coma >= 0:
            parte1, parte2 = dividir_bloque(bloque, idx_coma, max_chars)
            if parte1:
                bloques_comas.append(parte1)
            if parte2:
                bloques_comas.append(parte2)
        else:
            bloques_comas.append(bloque)

    # ======== Paso 3: VERIFICAR max_chars y DIVIDIR ========
    bloques_finales = []
    
    for bloque in bloques_comas:
        texto = unir_texto(bloque)
        
        if len(texto) > max_chars:
            partes = dividir_por_limite_mejorado(bloque, max_chars)
            for parte in partes:
                bloques_finales.append(parte)
        else:
            bloques_finales.append(bloque)

    return bloques_finales


def buscar_corte_coma(texto):
    """
    Busca coma (,) con reglas:
    - ≥2 palabras ANTES de la coma (sin palabras con punto)
    - Y DESPUÉS: ≥2 palabras O (1 palabra sin punto)
    """
    for i, c in enumerate(texto):
        if c == ',':
            palabras_antes = [p for p in texto[:i].strip().split() if not p.endswith('.')]
            despues = texto[i+1:].strip().split()
            tiene_punto = len(despues) == 1 and despues[0].endswith('.')
            
            if len(palabras_antes) >= 2 and (len(despues) >= 2 or not tiene_punto):
                return i + 1
    
    return -1


def unir_texto(bloque):
    """Une palabras respetando puntuación adjunta."""
    if not bloque:
        return ''
    texto = bloque[0]['text'].strip()
    for w in bloque[1:]:
        palabra = w['text'].strip()
        if texto.endswith(('.', ',', '!', '?', ';', ':')):
            texto = texto + ' ' + palabra
        else:
            texto = texto + ' ' + palabra
    return texto


def buscar_corte_natural(texto):
    """
    Busca coma/punto como fin de oración.
    - Primero busca coma (,)
    - Si no hay, busca punto (.)
    - Coma: ≥2 palabras ANTES y DESPUÉS según reglas
    - Punto: siempre válido
    """
    # Primero: buscar coma (,)
    for i, c in enumerate(texto):
        if c == ',':
            palabras_antes = [p for p in texto[:i].strip().split() if not p.endswith('.')]
            despues = texto[i+1:].strip().split()
            tiene_punto = len(despues) == 1 and despues[0].endswith('.')
            
            if len(palabras_antes) >= 2 and (len(despues) >= 2 or not tiene_punto):
                return i + 1
    
    # Segundo: buscar punto (.)
    for i, c in enumerate(texto):
        if c == '.':
            return i + 1
    
    return -1


def dividir_bloque(bloque, idx_corte, max_chars):
    """
    Divide bloque en idx_corte, respetando palabras completas.
    idx_corte es la posición donde queremos cortar (después de coma/punto).
    """
    caracteres_acumulados = 0
    
    for i, w in enumerate(bloque):
        palabra = w['text'].strip()
        palabra_con_espacio = ' ' + palabra if i > 0 else palabra
        
        caracteres_acumulados += len(palabra_con_espacio)
        
        if caracteres_acumulados > idx_corte:
            parte1 = bloque[:i]
            parte2 = bloque[i:]
            
            if not parte1:
                parte1 = [bloque[0]]
                parte2 = bloque[1:]
            
            return parte1, parte2
    
    return bloque, []


def dividir_por_limite(bloque, max_chars):
    """Quita palabras desde el final hasta que <= max_chars."""
    bloque_actual = list(bloque)

    while bloque_actual:
        texto_actual = unir_texto(bloque_actual)
        if len(texto_actual) <= max_chars:
            break
        bloque_actual.pop()

    if bloque_actual:
        palabras_quitadas = len(bloque) - len(bloque_actual)
        return bloque_actual, bloque[-palabras_quitadas:] if palabras_quitadas > 0 else []
    return [], bloque


def dividir_por_limite_mejorado(bloque, max_chars):
    """
    Divide bloque en partes IGUALES (+1 al primero si impar).
    Intenta con 2, 3, 4... partes hasta que funcione.
    """
    n = len(bloque)
    if n <= 1:
        return [bloque]
    
    # Intentar con 2 partes, luego 3, luego 4...
    for num_partes in range(2, n + 1):
        tamano_base = n // num_partes
        resto = n % num_partes
        
        partes = []
        indice = 0
        
        for p in range(num_partes):
            # Las primeras 'resto' partes tienen +1 palabra
            tamano = tamano_base + (1 if p < resto else 0)
            parte = bloque[indice:indice + tamano]
            texto_parte = unir_texto(parte)
            
            if len(texto_parte) > max_chars:
                # Esta parte excede, no sirve esta configuración
                partes = None
                break
            
            partes.append(parte)
            indice += tamano
        
        # Si todas las partes cumplen, retornar
        if partes and all(len(unir_texto(p)) <= max_chars for p in partes):
            return partes
    
    # Si no puede dividir, retornar el bloque original
    return [bloque]


# ══════════════════════════════════════════════════════════════
#  CORRECCIÓN CON LLM (OpenRouter)
# ══════════════════════════════════════════════════════════════

def corregir_con_llm(textos, key, model):
    """Envía todos los textos a OpenRouter en 1 solo request para corrección."""
    if not key or not textos:
        return textos

    url = 'https://openrouter.ai/api/v1/chat/completions'
    headers = {
        'Authorization': f'Bearer {key}',
        'Content-Type': 'application/json',
        'HTTP-Referer': 'https://subify.app',
        'X-Title': 'Subify'
    }

    textos_texto = '\n'.join(f'{i+1}. {t}' for i, t in enumerate(textos))

    prompt = f"""Eres un corrector de subtítulos en español.
Para cada línea numerada:
- Aplica mayúsculas solo al inicio de oraciones (después de punto)
- Corrige solo mayúsculas faltantes al inicio (no pongas TODO EN MAYÚSCULAS)
- Corrige solo tildes faltantes (á, é, í, ó, ú)
- NO cambies palabras, NO agregues signos, NO agregues puntuación
- Respeta la puntuación existente
- Devuelve SOLO las líneas corregidas, en mismo formato numerado

Lista de textos:
{textos_texto}

Devuelve solo las líneas corregidas, sin explicación:"""

    payload = {
        'model': model,
        'messages': [{'role': 'user', 'content': prompt}],
        'temperature': 0.1,
        'max_tokens': 4000,
    }

    for intento in range(3):
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=60)
            if resp.status_code == 200:
                data = resp.json()
                contenido = data['choices'][0]['message']['content'].strip()
                
                resultados = []
                for linea in contenido.split('\n'):
                    linea = linea.strip()
                    if linea and not linea[0].isdigit():
                        continue
                    num = ''
                    for i, c in enumerate(linea):
                        if c.isdigit():
                            num += c
                        else:
                            break
                    if num:
                        texto = linea[len(num):].strip()
                        if texto.startswith('.'):
                            texto = texto[1:].strip()
                        resultados.append(texto)
                
                if len(resultados) == len(textos):
                    return resultados
                else:
                    print(f'     ⚠️  Recibí {len(resultados)}, esperaba {len(textos)}, usando original')
                    return textos
            else:
                print(f'     ⚠️  Intento {intento+1} falló: {resp.status_code}')
        except Exception as e:
            print(f'     ⚠️  Intento {intento+1} error: {e}')

        if intento < 2:
            time.sleep(3 + intento * 2)

    print('     ⚠️  Todos los intentos fallaron, usando texto original')
    return textos


# ══════════════════════════════════════════════════════════════
#  ENSAMBLAR SRT
# ══════════════════════════════════════════════════════════════

def ensamblar_srt(bloques, textos_corregidos):
    """Genera contenido SRT uniendo timestamps + texto corregido."""
    lineas = []

    for i, (bloque, texto) in enumerate(zip(bloques, textos_corregidos), 1):
        start = segundos_a_ts(bloque[0]['start'])
        end = segundos_a_ts(bloque[-1]['end'])

        lineas.append(str(i))
        lineas.append(f'{start} --> {end}')
        lineas.append(texto.strip())
        lineas.append('')

    return '\n'.join(lineas)


# ══════════════════════════════════════════════════════════════
#  LIMPIEZA
# ══════════════════════════════════════════════════════════════

def limpiar(*rutas):
    for r in rutas:
        try:
            if r and os.path.exists(r) and r != CARPETA_AUDIO:
                os.remove(r)
        except Exception:
            pass


# ══════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════

def main():
    import shutil

    drive_service = None
    if DESDE_PC:
        print(f'🔍 Buscando audios en: {CARPETA_AUDIO}')
        rutas = []
        for ext in FORMATOS_AUDIO:
            rutas += glob.glob(os.path.join(CARPETA_AUDIO, f'*{ext}'))
            rutas += glob.glob(os.path.join(CARPETA_AUDIO, f'*{ext.upper()}'))
        archivos = [
            {'ruta': r, 'nombre': Path(r).name, 'tam': os.path.getsize(r)}
            for r in sorted(set(rutas))
        ]
    else:
        print('🔍 Conectando a Google Drive...')
        drive_service = conectar_drive()
        print('✅ Conectado.')
        drive_lista = listar_audios_drive(drive_service, DRIVE_ID)
        archivos = [
            {'id': a['id'], 'nombre': a['name'], 'tam': int(a.get('size', 0))}
            for a in drive_lista
        ]

    if not archivos:
        print('⚠️  No se encontraron archivos de audio.')
        sys.exit(0)

    os.makedirs(CARPETA_SALIDA, exist_ok=True)

    total_archivos = len(archivos)
    total_bytes    = sum(a['tam'] for a in archivos)

    print(f'📁 {total_archivos} archivo(s) encontrado(s).')
    print(f'📂 SRT → {CARPETA_SALIDA}')
    print(f'STATS:{total_archivos}:{total_bytes}')
    if SOBRESCRIBIR_SRT: print('⚙️  Modo: sobreescribir SRT existentes')
    if SOBRESCRIBIR_WAV: print('⚙️  Modo: sobreescribir audios existentes')
    if OPENROUTER_KEY: print(f'⚙️  Corrección IA: {OPENROUTER_MODEL}')
    print()

    EXTS_AUDIO = set(FORMATOS_AUDIO)
    EXTS_SRT   = {'.srt'}

    mapa_srt_local   = construir_mapa_ep(CARPETA_SALIDA, EXTS_SRT)
    mapa_audio_local = construir_mapa_ep(CARPETA_SALIDA, EXTS_AUDIO) if GUARDAR_AUDIO else {}

    ok    = 0
    error = 0

    for i, arch in enumerate(archivos, 1):
        nombre     = arch['nombre']
        tam_mb     = arch['tam'] / 1024 / 1024
        ep_nube    = extraer_numero_ep(nombre)

        print(f'[{i}/{total_archivos}] 📄 {nombre} ({tam_mb:.1f} MB){f"  →  ep {ep_nube}" if ep_nube else ""}')

        ruta_orig     = None
        ruta_flac     = None
        guardar_audio = not DESDE_PC and GUARDAR_AUDIO

        try:
            if ep_nube is not None:
                if SOBRESCRIBIR_WAV and ep_nube in mapa_audio_local:
                    ruta_vieja = mapa_audio_local.pop(ep_nube)
                    limpiar(ruta_vieja)
                    print(f'     🗑️  Audio previo eliminado: "{Path(ruta_vieja).name}"')
                if SOBRESCRIBIR_SRT and ep_nube in mapa_srt_local:
                    ruta_vieja = mapa_srt_local.pop(ep_nube)
                    limpiar(ruta_vieja)
                    print(f'     🗑️  SRT previo eliminado: "{Path(ruta_vieja).name}"')

            if DESDE_PC:
                ruta_orig = arch['ruta']
            else:
                if guardar_audio:
                    if ep_nube is not None and ep_nube in mapa_audio_local and not SOBRESCRIBIR_WAV:
                        ruta_audio_guardado = mapa_audio_local[ep_nube]
                        print(f'     ⏭️  Audio ya existe como "{Path(ruta_audio_guardado).name}", saltando descarga.')
                        ruta_orig = ruta_audio_guardado
                    else:
                        print('     → Descargando de Drive...')
                        ruta_orig = descargar_drive(drive_service, arch['id'], nombre)
                        destino_audio = os.path.join(CARPETA_SALIDA, nombre)
                        shutil.copy2(ruta_orig, destino_audio)
                        print(f'     → Audio guardado: {destino_audio}')
                        if ep_nube is not None:
                            mapa_audio_local[ep_nube] = destino_audio
                else:
                    print('     → Descargando de Drive...')
                    ruta_orig = descargar_drive(drive_service, arch['id'], nombre)

            ruta_srt = None
            if ep_nube is not None and ep_nube in mapa_srt_local and not SOBRESCRIBIR_SRT:
                ruta_srt_existente = mapa_srt_local[ep_nube]
                if os.path.getsize(ruta_srt_existente) > 0:
                    print(f'     ⏭️  SRT ya existe como "{Path(ruta_srt_existente).name}", saltando transcripción.\n')
                    ok += 1
                    continue

            nombre_srt = Path(nombre).stem + '.srt'
            ruta_srt   = os.path.join(CARPETA_SALIDA, nombre_srt)

            print('     → Convirtiendo a FLAC...')
            ruta_flac   = convertir_a_flac(ruta_orig)
            nombre_flac = Path(ruta_flac).name

            print('     → Transcribiendo con Voxtral...')
            palabras = transcribir_a_palabras(ruta_flac, nombre_flac)
            print(f'     → {len(palabras)} palabras detectadas')

            print('     → Agrupando bloques...')
            bloques = preparar_bloques(palabras, gap_threshold=0.3, max_chars=38)
            print(f'     → {len(bloques)} bloques generados')

            textos_bloques = [unir_texto(b) for b in bloques]

            if USAR_CORRECCION and textos_bloques:
                print('     → Corrigiendo con IA (1 request)...')
                textos_corregidos = corregir_con_llm(textos_bloques, OPENROUTER_KEY, OPENROUTER_MODEL)
            else:
                textos_corregidos = textos_bloques

            srt_contenido = ensamblar_srt(bloques, textos_corregidos)

            with open(ruta_srt, 'w', encoding='utf-8') as f:
                f.write(srt_contenido)

            if ep_nube is not None:
                mapa_srt_local[ep_nube] = ruta_srt

            print('     ✅ Listo.\n')
            ok += 1

        except Exception as e:
            print(f'     ❌ Error: {e}\n')
            error += 1

        finally:
            if DESDE_PC:
                limpiar(ruta_flac)
            else:
                if guardar_audio:
                    limpiar(ruta_flac)
                else:
                    limpiar(ruta_orig, ruta_flac)

    print('=' * 55)
    print(f'✅ Completados: {ok}   ❌ Errores: {error}')
    print(f'📂 SRT guardados en: {CARPETA_SALIDA}')

    if error > 0:
        sys.exit(1)


if __name__ == '__main__':
    main()