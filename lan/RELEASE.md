Concat Plus LAN permite controlar desde Android un proyecto alojado en Windows.
El motor de la PC procesa las ediciones, previsualizaciones y exportaciones.
Incluye código personalizado persistente, código aleatorio y acceso sin código opcional.

- Windows x64: descomprime todo el ZIP y ejecuta ConcatPlusLAN.exe; concat.exe conserva el editor nativo.
- Android arm64: instala el APK y abre Ajustes → General → Concat Plus LAN.
- Ambos dispositivos deben estar en la misma LAN privada. Mantén la PC y el host encendidos.
- Guía, límites y código completo: [lan/README.md](https://github.com/thiagowiegert/Concat/blob/v0.3.0-lan-preview.1/lan/README.md).

Esta es una **pre-release**, no una versión estable validada en una pareja física Windows/Android.
Windows x64 y el APK arm64 compilaron; el APK arrancó en emuladores Android 11/13/14/15.
La integración del host Windows pasó pairing, carga de medios, edición, deshacer/rehacer,
seguimiento del trabajo de exportación y reconexión. El runner Windows de GitHub no tiene un
renderer DXGI utilizable: la prueba confirma que el host registra ese error, pero no demuestra
un render ni una exportación de video exitosa en Windows. Hace falta probarlo en una PC Windows
con GPU funcional y conectarlo a un Android en la misma LAN. Tampoco demuestra que el control
remoto siga funcionando con la pantalla del teléfono bloqueada.
La interfaz remota usa el navegador y todavía no tiene todas las funciones avanzadas del editor nativo.

El APK de esta preview usa una clave de compilación temporal: puede requerir desinstalar una
compilación anterior; guarda tus proyectos y videos antes. El ZIP de Windows no está firmado.
Se incluyen sumas SHA-256. Se conservan la licencia AGPL y la atribución del proyecto original.
