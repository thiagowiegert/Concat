# Concat Plus LAN (preview)

Controla un proyecto alojado en Windows desde Android en la misma red local.
El motor Rust de Concat ejecuta la edición, el cálculo de fotogramas y la exportación en la PC.
El teléfono utiliza el editor web del host; no es una transmisión de la interfaz nativa completa.

## Inicio

1. Descomprime **todo** el ZIP de Windows. `concat.exe` abre el editor local y
   `ConcatPlusLAN.exe` inicia el host. No se necesita instalar Python.
2. Elige cómo conectarte: código aleatorio, código personalizado de 8–64 caracteres,
   o desmarca **Requerir código para conectarse**. Confirma el acceso abierto si lo eliges.
3. Permite el host en el Firewall de Windows **solo en redes privadas** si Windows lo pregunta.
4. En Android, abre Ajustes → General → Concat Plus LAN, escribe la dirección que
   muestra la PC (por ejemplo `http://192.168.1.10:7421`) y pulsa Conectar con la PC.
   También puedes abrir esa dirección directamente en Chrome.
5. Introduce el código si está habilitado, crea un proyecto, sube medios y añádelos
   a la línea de tiempo. También puedes copiar medios a la carpeta `library` de la PC.
6. Edita, genera una reproducción con audio y exporta. Descarga el MP4 cuando termine.
   Mantén el host abierto y la PC encendida, conectada y sin suspensión.

## Código personalizado y acceso sin código

El host guarda la elección en `Documents/ConcatPlusLAN/host-settings.json`, dentro
normalmente del perfil del usuario de Windows. El código personalizado permanece
hasta que lo cambies; deja el campo vacío para generar uno aleatorio al iniciar.
El aleatorio sirve durante diez minutos para nuevas conexiones. Las sesiones ya
abiertas duran hasta que cierres el host. Reinicia el host para aplicar otra
configuración, generar otro código o revocar las sesiones anteriores. No cierres
el host mientras exporta: se interrumpirían los trabajos.

Al desactivar el código, cualquier dispositivo de esa LAN puede acceder al editor,
a los medios compartidos y a las exportaciones. No desactiva la conexión LAN.
El ajuste solo se cambia en la PC. Se conserva el código personalizado si luego
quieres volver a habilitarlo. El código se guarda como texto en ese archivo local;
no uses una contraseña reutilizada de otra cuenta.

## Qué incluye

- Crear/abrir proyectos, subir video/audio/imágenes y usar una biblioteca compartida.
- Pistas, mover/recortar/dividir/eliminar clips, velocidad, volumen, fundidos de audio,
  opacidad, escala, rotación, títulos y efectos con sus parámetros predeterminados.
- Deshacer/rehacer y guardado automático en la PC; revisión para detectar ediciones
  simultáneas. Ante un conflicto, pulsa Actualizar conexión antes de repetir el cambio.
- Fotogramas calculados en la PC y reproducción con audio mediante una exportación
  de previsualización de menor resolución. No hay streaming interactivo en tiempo real.
- Exportación H.264 con audio, estado persistido en la PC y descarga con soporte de rangos.
  El trabajador de exportación no depende de que el navegador permanezca conectado.
- El editor nativo conserva sus funciones. Esta primera interfaz remota no incluye
  todos sus paneles avanzados, transcripción, máscaras ni edición de parámetros de efectos.

## Archivos y red

Por defecto todo está bajo `Documents/ConcatPlusLAN`: `projects`, `uploads`,
`library` y `exports`. `engine.log` registra errores del motor; `jobs.json` conserva
el estado. Un reinicio interrumpe los trabajos activos; vuelve a exportarlos.
No abras simultáneamente un proyecto desde el editor nativo y el host.
Las subidas tienen un máximo de 2 GiB por archivo y requieren espacio libre en la PC.
La biblioteca admite hasta 2000 entradas. Los archivos quedan hasta que los borres
manualmente desde la PC; hazlo con el host detenido y conserva copias de los proyectos.

HTTP está pensado únicamente para una LAN privada de confianza: el código y los
videos no están cifrados en tránsito. No abras el puerto en el router ni lo expongas
a Internet. Se puede configurar HTTPS con `--cert certificado.pem --key clave.pem`;
el teléfono debe confiar en ese certificado. No hay nube externa ni cuenta.
Algunas redes de invitados aíslan los dispositivos: usa una red donde puedan comunicarse.

## Desarrollo y pruebas

Python 3.11+ estándar para el host. Compila `concat-cli` según las instrucciones
originales del repositorio y ejecuta:

```text
python lan/host.py --engine RUTA/concat-cli.exe
python -m unittest discover -s lan/tests -v
node --check lan/web/app.js
python lan/tests/integration.py --host RUTA/ConcatPlusLAN.exe --ffmpeg RUTA/ffmpeg.exe --ffprobe RUTA/ffprobe.exe
```

`--no-gui` usa la configuración guardada; `CONCAT_LAN_CODE` permite sobreescribir
el código y `--no-code` desactiva su requisito para esa ejecución.
Las pruebas unitarias usan un motor falso. `CONCAT_TEST_OFFLINE=1` prueba el parser
HTTP en memoria cuando el entorno bloquea sockets, sin verificar transporte LAN.
La integración usa el ejecutable empaquetado y el motor Rust real, comprueba medios,
ediciones, exportación sin peticiones del cliente, audio, descarga y reconexión.

Antes de considerar esta versión estable: probar Windows + Android físicos en LAN,
cerrar/bloquear el teléfono durante exportación, reconectar, comprobar sonido y duración,
cancelar una exportación larga, probar cortes de Wi-Fi y los tres modos de código.
Los emuladores de Android comprueban el arranque de la app, no esta conexión física.

## Licencia y código fuente

Basado en Concat de Jareer Abdullah y sus colaboradores. Se conservan `LICENSE`
y `THIRD_PARTY_NOTICES.md`; los cambios de Concat Plus se publican bajo
AGPL-3.0-or-later. Código fuente y scripts de construcción:
https://github.com/thiagowiegert/Concat (elige la etiqueta exacta de tu release).
La página del editor ofrece el enlace de código fuente. No se modifica el repositorio original.
