# ARMA-TE STUDIO — Editor local de subtítulos

Aplicación local para Windows con React, Vite y TypeScript en `frontend/`, y
FastAPI en `backend/`. Incluye gestión durable de proyectos y medios, un editor
visual de subtítulos sincronizado con el video, generación de transcripciones
con Faster-Whisper y exportación SRT, ASS y MP4. Los motores de
`subtitle-engine/transcription/` y `subtitle-engine/designer/` se reutilizan
desde la aplicación.

## Requisitos

- Windows 10/11, Python 3.11 y Node.js 20.19+ (o 22.12+).
- FFmpeg instalado para que `ffprobe` esté disponible en `PATH`. Si el ejecutable
  se encuentra en otra ubicación, configura `ARMA_TE_FFPROBE` con su ruta.

Las dependencias Python se mantienen en los requirements existentes. No se
requiere una biblioteca adicional para subir archivos: las rutas de importación
reciben el contenido del archivo directamente en el cuerpo HTTP.

## Ejecutar en desarrollo

Desde dos terminales PowerShell en la raíz del proyecto:

```powershell
.\scripts\dev-backend.ps1
```

```powershell
.\scripts\dev-frontend.ps1
```

Abre <http://127.0.0.1:5173>. La API y OpenAPI están en
<http://127.0.0.1:8000/api/v1/health> y <http://127.0.0.1:8000/docs>.

## Proyectos y almacenamiento

Por defecto los proyectos se guardan en `input/projects/<uuid>/`. Cada carpeta
contiene `project.json` y `media/`. Los documentos JSON se escriben primero a un
archivo temporal, se vacían a disco y se reemplazan atómicamente. Los cambios
pasan por el coordinador de autosave con debounce; la API espera el flush antes
de confirmar la escritura. Al cerrar FastAPI, el coordinador intenta guardar
cualquier snapshot pendiente. Reiniciar el servidor recupera los proyectos al
leer sus documentos desde disco.

Configura otra ubicación mediante `ARMA_TE_PROJECT_DATA_DIR`. Las rutas
relativas se resuelven desde la carpeta de trabajo del proceso. El debounce se
configura con `ARMA_TE_AUTOSAVE_DEBOUNCE` y el autosave con
`ARMA_TE_AUTOSAVE_ENABLED` (los guardados explícitos siguen siendo durables si
el autosave está desactivado).

## API de la Etapa 2

- `POST /api/v1/projects` — crea un proyecto; JSON `{ "name": "Mi proyecto" }`.
- `GET /api/v1/projects` — lista proyectos.
- `GET /api/v1/projects/{id}` — abre el documento persistido.
- `PATCH /api/v1/projects/{id}` — guarda `name`, `metadata` o `document`.
- `DELETE /api/v1/projects/{id}` — elimina el proyecto y sus medios bajo la raíz de proyectos; la interfaz pide confirmación antes de llamar a este endpoint.
- `POST /api/v1/projects/{id}/media?filename=clip.mp4` — importa MP4; el cuerpo
  es el contenido binario del archivo. Se copia al proyecto, sin editar el
  original. FFprobe proporciona duración, dimensiones, FPS, códecs y audio.
- `GET /api/v1/projects/{id}/media` — sirve el video y acepta rangos de bytes
  (incluidos rangos de sufijo) para búsqueda del reproductor HTML5.
- `POST /api/v1/projects/{id}/transcription?filename=transcription.json` — importa
  JSON en el cuerpo. Valida idioma, segmentos y timestamps opcionales por palabra,
  y conserva el JSON completo del motor de transcripción.
- `POST /api/v1/projects/{project_id}/transcription-jobs` — inicia la generación
  de una transcripción Faster-Whisper para el MP4 del proyecto. Acepta JSON con
  `model_size` (`tiny`, `base`, `small` o `medium`) y `language` (`"es"` o `null`
  para detección automática). Desde la interfaz, los valores iniciales son `small`
  y español; la detección automática es seleccionable.
- `GET /api/v1/transcription-jobs/{job_id}` — consulta el estado del trabajo
  (`queued`, `running`, `succeeded` o `failed`), su mensaje y el error si ocurre.
  La interfaz consulta el estado cada 800 ms. Durante la inferencia muestra
  progreso indeterminado, ya que el motor no informa un porcentaje intermedio.
  Al completarse, vuelve a cargar el proyecto y actualiza el editor con la
  transcripción normalizada, sin recargar la página.

Las cargas de video están limitadas a 4 GiB y las transcripciones a 50 MiB.
Los nombres de archivo y los identificadores de proyecto se validan antes de
construir rutas del sistema.

## Comprobaciones

```powershell
Set-Location backend
..\.venv\Scripts\python.exe -m unittest discover -s tests -v
Set-Location ..
\.venv\Scripts\python.exe -m unittest discover -s subtitle-engine\transcription\tests -v
\.venv\Scripts\python.exe -m unittest discover -s subtitle-engine\designer\tests -v
Set-Location frontend
npm test
npm run typecheck
npm run build
```

## Editor visual

El perfil `project.document.design` incluye `textFormat` (`original`, `uppercase`,
`lowercase` o `title`) y `temporalEvents`. Cada evento usa tiempos en segundos
con intervalo `[start, end)`, coordenadas X/Y normalizadas de 0 a 100 y una
posición `base`, `top`, `center`, `bottom` o `custom`. Al terminar un evento se
restaura automáticamente el perfil base. `overrides` queda reservado para
extender los cambios temporales a tipografía, tamaño y colores.

El reproductor y el overlay comparten el mismo contenedor al entrar en pantalla
completa. Los controles propios mantienen reproducción, pausa y búsqueda sin
depender del fullscreen del control nativo del elemento `<video>`.

La transcripción de trabajo y el perfil visual se guardan en
`project.document.transcription` y `project.document.design`. Los cambios del
editor usan debounce de 550 ms, y se vacían antes de cambiar de proyecto o
importar archivos. La sincronización de reproducción usa un reloj observable;
preview, timeline y control de posición se actualizan sin poner cada tick en el
estado raíz de la aplicación.

Las pruebas React/DOM usan Vitest, Testing Library y jsdom; ejecuta `npm test`
desde `frontend/`. Cubren el modelo de edición, el componente sincronizado, el
guardado y el cambio de proyecto. Las tipografías Montserrat Variable, Anton y
Bebas Neue están vendorizadas en `frontend/src/assets/fonts/` para evitar
dependencias del sistema o de un CDN. Los avisos SIL Open Font License 1.1
también se copian al bundle en `frontend/public/fonts/licenses/`.

FFprobe es una herramienta externa, no una dependencia Python. Si falta al
importar, se conserva el MP4 original dentro del proyecto y la respuesta/UI
muestran el error de metadatos; instala FFmpeg o define `ARMA_TE_FFPROBE` para
obtener las propiedades. Un MP4 que FFprobe sí identifica como inválido se
rechaza y no reemplaza un medio previo del proyecto.

## Exportación SRT, ASS y MP4

Los adaptadores de `backend/app/exports/` usan los serializadores existentes en `subtitle-engine/designer/`; no modifican `transcription/` ni `designer/`.

- `POST /api/v1/projects/{id}/exports` recibe `{"format":"srt|ass|mp4","quality":"high|balanced|compact"}`. SRT/ASS descargan el archivo en la respuesta; MP4 crea un trabajo y responde HTTP 202.
- `GET /api/v1/jobs/{job_id}` consulta estado, error estructurado y progreso comunicado por FFmpeg. Si FFmpeg no informa tiempo procesado o no hay duración conocida, el progreso se mantiene en cero.
- `POST /api/v1/jobs/{job_id}/cancel` solicita cancelación cooperativa. Un trabajo en ejecución solo pasa a cancelado después de detener FFmpeg.
- `GET /api/v1/projects/{id}/exports/{filename}` descarga un resultado guardado en la carpeta del proyecto.

Los resultados se guardan en `input/projects/<uuid>/exports/` con nombre único. MP4 usa un archivo temporal y solo lo publica cuando termina correctamente. El video de origen no se reemplaza. FFmpeg debe estar en `PATH` o configurarse mediante `ARMA_TE_FFMPEG`; FFprobe se configura mediante `ARMA_TE_FFPROBE`. La compilación de FFmpeg debe incluir el filtro `subtitles` de libass.

SRT respeta los límites de los segmentos editables, tiempos y formato de texto elegido; el formato no tiene colores, karaoke ni posición. ASS usa los tiempos individuales, estilos y eventos de posición temporal. El MP4 conserva la resolución y FPS del origen, codifica video H.264 y audio AAC, y admite calidades alta, equilibrada y compacta.

El editor incluye Montserrat, Anton y Bebas Neue en WOFF2 con sus avisos SIL OFL. La exportación MP4 convierte temporalmente la fuente seleccionada a TTF con FontTools (dependencia backend `fonttools[woff]`) y entrega esa carpeta a FFmpeg/libass; no instala la fuente en Windows ni modifica los WOFF2. La selección se valida con un render ASS de prueba y falla con error si libass recurre a otra familia. En las pruebas locales libass confirma `Anton-Regular`. La comparación con el preview aún muestra una diferencia de ancho en el fotograma validado; por tanto, la fidelidad visual tipográfica sigue pendiente de calibración antes de considerar resuelta la incidencia.

Los estados de trabajos viven en memoria. Los archivos exportados permanecen en el proyecto tras reiniciar, pero el historial no se recupera y un render interrumpido no se reanuda automáticamente.
