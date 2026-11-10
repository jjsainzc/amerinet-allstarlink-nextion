AMERINET Nextion para AllStarLink 3

Guía en español de AMERINET_Nextion_ASL3.py, versión 1.3, del 10 de noviembre de 2026.

Este programa conecta una pantalla Nextion con el nodo AllStarLink instalado en el mismo equipo. Consulta Asterisk mediante asterisk -rx, obtiene métricas de Linux y actualiza la pantalla por el puerto serial.

Trabaja de forma autónoma: no necesita Amerimon, el programa en Go, credenciales AMI, un servidor web ni paquetes de pip. Asterisk debe estar instalado y funcionando para el uso real.

Requisitos

• Linux con Python 3 y AllStarLink 3.
• Pantalla Nextion con el proyecto AMERINET compatible ya cargado.
• Puerto serial configurado a 9600 baudios, 8 bits, sin paridad y 1 bit de parada, sin control de flujo.
• Ejecución con sudo para acceder al sistema y utilizar los botones de administración.
• ip para consultar la dirección IP y vcgencmd para obtener voltaje en Raspberry Pi.

El script no genera ni carga el archivo .tft. La pantalla debe tener los componentes y eventos indicados en esta guía. No ejecutes simultáneamente el puente Go y este Python sobre el mismo puerto.

Inicio rápido

Guarda AMERINET_Nextion_ASL3.py en el equipo del nodo. Los ejemplos usan el nodo 616265, el favorito 61627 y el puerto /dev/ttyUSB0; cambia esos valores si corresponde.

Primero comprueba Asterisk y las métricas sin abrir la pantalla:

sudo python3 ./AMERINET_Nextion_ASL3.py --node 616265 --once

El resultado es un diagnóstico JSON con el estado, los enlaces, las etiquetas SIP, el origen del audio, el comando de consulta, las métricas y los errores disponibles.

Después inicia la pantalla:

sudo python3 ./AMERINET_Nextion_ASL3.py --node 616265 --port /dev/ttyUSB0

Para detener el programa, presiona Ctrl+C. Para actualizarlo, detén la versión anterior, reemplaza el archivo y vuelve a ejecutarlo.

Opciones de ejecución

|Opción                    |Función                                                                    |
|--------------------------|---------------------------------------------------------------------------|
|`--node 616265`           |Selecciona el nodo local; obligatorio en uso real si no está en el INI.    |
|`--port /dev/ttyUSB0`     |Selecciona el puerto serial; por defecto `/dev/ttyUSB0`.                   |
|`--favorite 61627`        |Selecciona el favorito; debe ser diferente del nodo local.                 |
|`--lang es`               |Idioma español; también admite `en`.                                       |
|`--config /ruta/panel.ini`|Carga un archivo de configuración.                                         |
|`--once`                  |Imprime el diagnóstico y termina sin abrir el puerto serial.               |
|`--demo`                  |Simula radio y métricas; no ejecuta acciones reales sobre Asterisk o Linux.|
|`--help`                  |Muestra las opciones disponibles.                                          |

La velocidad está fijada en 9600 baudios en esta versión; no existe una opción --baud.

Pruebas de demostración

Sin pantalla ni Asterisk:

python3 ./AMERINET_Nextion_ASL3.py --demo --once

Con pantalla, pero sin controlar Asterisk ni ejecutar acciones de sistema:

sudo python3 ./AMERINET_Nextion_ASL3.py --demo --port /dev/ttyUSB0

La demostración incluye enlaces numéricos y dos ejemplos SIP. Sus métricas son simuladas.

Configuración con panel.ini

Ejemplo de archivo panel.ini:

[panel]
puerto = /dev/ttyUSB0
interfaz =
idioma = es
voltaje_mv_archivo =

[asl3]
nodo_local = 616265
nodo_favorito = 61627

Ejecuta:

sudo python3 ./AMERINET_Nextion_ASL3.py --config ./panel.ini

Si existe un panel.ini en el directorio desde donde inicias el programa, se carga automáticamente. Las opciones de la línea de comandos tienen prioridad sobre las opciones equivalentes del INI.

Deja interfaz vacío para seleccionar una IP automáticamente, o indica una interfaz real del equipo, por ejemplo eth0. Para identificar puertos seriales estables:

ls -l /dev/serial/by-id/

Puedes usar una de esas rutas completas en puerto o en --port.

Datos de la pantalla

|Componente        |Información                                                                      |
|------------------|---------------------------------------------------------------------------------|
|`t_nodo`          |Nodo local.                                                                      |
|`t_ip`            |Dirección IPv4 del equipo.                                                       |
|`t_date`, `t_time`|Fecha y hora del equipo; se usa su zona horaria local.                           |
|`t_temp`          |Temperatura del procesador cuando está disponible.                               |
|`t_volt`          |Voltaje disponible, identificado como `Volt` o `Vcore`.                          |
|`t_cpu`           |Uso de CPU.                                                                      |
|`t_mem`           |Uso de memoria.                                                                  |
|`t_dsk`           |Uso de disco de la partición raíz.                                               |
|`t_n1` a `t_n8`   |Hasta ocho enlaces visibles.                                                     |
|`t0`              |Origen de audio disponible, mensajes de sistema o aviso de desconexión.          |
|`b_estado`        |Indicador de actividad: gris en espera, verde en recepción y rojo en transmisión.|

Los títulos y botones utilizan t_actions, t_links, t_hint, t_admin_title, b_fav, b_disc_all, b_restart, b_reboot, b_poweroff, b_admin_no y b_admin_ok. El diseño debe incluir también va_lang y va_alive.

El estado de radio se consulta aproximadamente cada medio segundo y las métricas cada cinco segundos, además del tiempo que requieren las consultas. Una lectura no disponible se muestra como --.

Voltaje en Raspberry Pi

El programa intenta las fuentes en este orden:

1. Archivo externo de milivoltios, si está configurado en voltaje_mv_archivo.
2. Lectura EXT5V_V mediante vcgencmd pmic_read_adc EXT5V_V, disponible en Pi 5.
3. Lectura del núcleo mediante vcgencmd measure_volts core, como respaldo para Pi 3/4.

En Pi 4, una respuesta como volt=0.9260V se muestra como Vcore 0.93V. Es el voltaje del núcleo, no la alimentación de 5 V. La Pi 3/4 necesita un sensor externo para medir esa alimentación desde este programa.

Comprueba la lectura del núcleo con:

sudo vcgencmd measure_volts core

Un archivo de sensor externo debe contener un número entero en milivoltios: por ejemplo, 5080 para 5.08 V. Otro proceso o controlador debe actualizar ese archivo; el script no realiza la adquisición del sensor externo.

Enlaces SIP y PJSIP

Los canales PJSIP activos en estado Up se consultan con core show channels concise. En pantalla aparecen como SIP <CallerID>. Si falta el CallerID, se intenta leer /etc/asterisk/pjsip.conf y sus archivos #include; como último recurso se muestra el nombre del endpoint. No se resuelven plantillas heredadas del archivo PJSIP.

El programa reconoce entradas como:

RPT_ALINKS=1,SIP/1005TU

En este ejemplo, SIP/1005 es el identificador del enlace, T es el modo y U indica que no está enviando señal de activación hacia el nodo. El sufijo K indica que sí la está enviando.

Cuando puede relacionar ese enlace de manera única con un canal PJSIP real, evita mostrarlo dos veces y conserva el canal completo para desconectarlo correctamente. Si varias llamadas coinciden, mantiene cada canal individual.

La lista de canales PJSIP se obtiene para todo el servidor, como en el Go de referencia. En un servidor con varios nodos puede incluir llamadas de otros nodos. La indicación de actividad SIP depende de que Asterisk publique el estado del enlace en RPT_ALINKS; no se abre una conexión AMI para escuchar eventos.

Botones y eventos del HMI

|Acción                       |Mensaje enviado por la pantalla|
|-----------------------------|-------------------------------|
|Conectar favorito            |`CONN_FAV`                     |
|Desconectar enlaces RPT      |`DISC_ALL`                     |
|Preparar reinicio de Asterisk|`SYS_ARM_RESTART`              |
|Preparar reinicio de Linux   |`SYS_ARM_REBOOT`               |
|Preparar apagado de Linux    |`SYS_ARM_OFF`                  |
|Confirmar acción             |`SYS_CONFIRM`                  |
|Cancelar acción              |`SYS_CANCEL`                   |

Los botones de desconexión individual b_d1 a b_d8 deben enviar HOLD_N1 a HOLD_N8, terminados con un salto de línea, al tocarse; y DISC_N1 a DISC_N8 al soltarse. También se acepta texto después de HOLD_N1, igual que en el Go. El programa guarda el identificador interno de la casilla al comenzar el toque.

Ejemplo para la primera casilla, en el evento Touch Press:

print "HOLD_N1"
printh 0A

En el evento Touch Release:

print "DISC_N1"

El toque debe completarse dentro de ocho segundos. Las acciones de administración requieren confirmar dentro de diez segundos. La respuesta a sendme de la página 0 permite reenviar los valores al reiniciar la pantalla.

Para enlaces RPT se utilizan ilink 3 para conectar, ilink 1 para desconectar y ilink 6 para desconectar los enlaces. Las llamadas PJSIP identificadas por su canal real se cuelgan individualmente con channel request hangup. DISC_ALL ejecuta ilink 6; no envía una orden de colgado a cada canal PJSIP del servidor.

Solución de problemas

La pantalla no cambia

Comprueba el puerto, los 9600 baudios y que no haya otro proceso usando el mismo puerto. Revisa que el proyecto cargado en la pantalla tenga los nombres de componentes y eventos indicados arriba. El mensaje de apertura del puerto confirma que se abrió el dispositivo, no que el HMI haya aceptado todos los comandos.

Asterisk aparece desconectado

Ejecuta:

sudo asterisk -rx "rpt show variables 616265"
sudo python3 ./AMERINET_Nextion_ASL3.py --node 616265 --once

El script utiliza rpt show variables y prueba rpt xnode como respaldo. No utiliza el comando antiguo rpt showvars. El diagnóstico incluye el error recibido y la consulta seleccionada.

Aparece «Enlace adyacente no reconocido: SIP/1005TU»

Esa validación se corrigió en la versión 1.2. Reemplaza el archivo anterior y reinicia el proceso. La versión 1.3 incluye esa corrección y la lectura Vcore para Pi 3/4.

El voltaje muestra –

Prueba sudo vcgencmd measure_volts core. Si el comando no existe o no devuelve una lectura válida, el programa no puede obtener Vcore. Si configuraste un archivo externo, comprueba que exista, sea legible y contenga milivoltios enteros; una ruta externa configurada tiene prioridad.

Verificación y alcance

La versión 1.3 pasó 28 pruebas locales con respuestas simuladas de Asterisk y comprobaciones mediante un puerto pseudo-TTY. Incluyen el caso SIP/1005TU, etiquetas SIP, desconexión por canal, recepción y transmisión, eventos fragmentados, confirmaciones y fuentes de voltaje.

Estas pruebas no sustituyen la validación con la pantalla física y la instalación real del nodo. La respuesta volt=0.9260V fue confirmada en el equipo del usuario; falta confirmar su presentación en la pantalla.

Referencias

• Comandos de AllStarLink 3
• Variables y eventos de app_rpt
• Documentación de vcgencmd de Raspberry Pi
