#!/usr/bin/env python3
"""Puente serial AMERINET Nextion ↔ AllStarLink 3 para Linux.

Compatible con los proyectos multi de NX3224T024, NX4024T032, NX4832K035
y NX8048T050 revisados el 10 de noviembre de 2026. Velocidad serial 9600 8N1.
Usa la biblioteca estándar de Python 3 en Linux y la consola local de Asterisk.
La configuración panel.ini del
puente anterior es compatible: [panel] puerto, interfaz, idioma,
voltaje_mv_archivo y [asl3] nodo_local, nodo_favorito. El favorito por defecto
es 61627. El voltaje se lee de un sensor en milivoltios, del PMIC de Pi 5
o del núcleo (Vcore) como respaldo en Pi 3/4.

Prueba sin pantalla: python3 AMERINET_Nextion_ASL3.py --demo --once
Prueba con pantalla: python3 AMERINET_Nextion_ASL3.py --demo --port /dev/ttyUSB0
Uso real: sudo python3 AMERINET_Nextion_ASL3.py --node TU_NODO --port /dev/ttyUSB0
El puerto puede ser también una ruta estable de /dev/serial/by-id/.
Cierra el puente Go antes de abrir el mismo puerto con este script.
Sustituye TU_NODO por el número local. Para usar tu INI existente:
sudo python3 AMERINET_Nextion_ASL3.py --config /ruta/absoluta/panel.ini
Para obtener un diagnóstico local sin abrir la pantalla:
sudo python3 AMERINET_Nextion_ASL3.py --node TU_NODO --once

Los botones de sistema requieren root y confirmar dentro de diez segundos.
El modo demo simula también las acciones de sistema. El programa reabre el
puerto cuando se desconecta y consulta de nuevo Asterisk después de sus fallos.
Los datos se actualizan cada cinco segundos y el estado de radio cada medio
segundo aproximadamente. El signo -- representa una lectura no disponible.
La fecha y la hora usan la zona horaria configurada en el equipo.
En Pi 3/4, si no hay sensor externo, se muestra Vcore mediante
vcgencmd measure_volts core. Es voltaje del núcleo, no de la fuente de 5 V.
Para medir la alimentación 5 V en Pi 3/4 se requiere un sensor externo.
Su archivo debe contener milivoltios enteros, por ejemplo 5080, y configurarse
en voltaje_mv_archivo. En Pi 5 se usa EXT5V_V y se muestra Volt.
El favorito inicial es AMERINET 61627 y se puede cambiar con --favorite.

Versión 1.3 (10 de noviembre de 2026)
Usa rpt show variables de ASL3 con rpt xnode como respaldo. RPT_ALINKS
identifica enlaces adyacentes y cuáles están enviando audio (sufijo K).
Reconoce SIP/1005TU y relaciona SIP/1005 con el canal PJSIP real sin duplicados.
Acepta los eventos HOLD_N1..HOLD_N8 con o sin texto, igual que nextion.go.
Usa únicamente Python estándar, comandos locales de Linux y asterisk -rx.
No requiere Amerimon, credenciales AMI, Go ni paquetes pip.
Los canales PJSIP Up se muestran como SIP <CallerID> en las ocho casillas y
se desconectan por su canal real, igual que nextion.go. Si falta CallerID,
se lee pjsip.conf o se muestra el nombre del endpoint. También se admiten
archivos #include de pjsip.conf; no se resuelven plantillas heredadas.
Las verificaciones locales usan respuestas simuladas y un puerto pseudo-TTY.
La prueba con una instalación real de Asterisk y pantalla física está pendiente.
La actividad de SIP se lee del sufijo K de su enlace en RPT_ALINKS cuando
Asterisk lo proporciona. No se abre una conexión AMI para eventos PJSIP.

Referencias de los comandos de AllStarLink
https://allstarlink.github.io/basics/commands/
https://allstarlink.github.io/adv-topics/eventmgmt/
https://allstarlink.github.io/config/rpt_conf/#link-commands
https://community.allstarlink.org/t/any-way-to-stop-crossing-to-specific-nodes/8967/6
https://community.allstarlink.org/t/ilink-6-command-syntax-via-management-interface-port-5038/23279
"""

import argparse
import configparser
import fcntl
import ipaddress
import json
import logging
import os
from pathlib import Path
import re
import select
import shutil
import subprocess
import termios
import time


END = b"\xff\xff\xff"
SLOT_COUNT = 8
NODE = re.compile(r"[1-9][0-9]{0,9}")
CALLSIGN = re.compile(r"[A-Z1-9*][A-Z0-9*-]{0,9}")
SIP_CHANNEL = re.compile(r"PJSIP/[A-Za-z0-9_.-]{1,64}-[A-Za-z0-9_.-]{1,64}")
SIP_LINK = re.compile(r"(?:SIP|PJSIP)/[A-Za-z0-9_.-]{1,128}")
SYS_ACTIONS = {
    "SYS_ARM_RESTART": (("restart", "asterisk.service"), "Reiniciar Asterisk",
                        "Restart Asterisk", "Reiniciando Asterisk", "Restarting Asterisk"),
    "SYS_ARM_REBOOT": (("reboot",), "Reiniciar Linux", "Reboot Linux",
                       "Reiniciando Linux", "Rebooting Linux"),
    "SYS_ARM_OFF": (("poweroff",), "Apagar Linux", "Power Off Linux",
                     "Apagando Linux", "Powering off Linux"),
}
RELEASES = tuple(token.encode("ascii") for token in (
    "CONN_FAV", "DISC_ALL", *SYS_ACTIONS, "SYS_CONFIRM", "SYS_CANCEL",
    *(f"DISC_N{i}" for i in range(1, SLOT_COUNT + 1)),
))
STATUS = {
    "idle": ("Sistema Listo", "System ready", 33840),
    "tx": ("Transmitiendo...", "Transmitting...", 63488),
    "rx": ("Recibiendo...", "Receiving...", 1024),
    "unknown_tx": ("TX no disponible", "TX unavailable", 33840),
    "offline": ("ASL3 sin conexión", "ASL3 disconnected", 33840),
}
# Paths de Debian y Raspberry Pi OS para las órdenes que se ejecutan como root.
BIN_PATH = "/usr/sbin:/usr/bin:/sbin:/bin:/usr/local/sbin:/usr/local/bin:/opt/vc/bin"


class CommandError(RuntimeError):
    """El programa local o la consola de Asterisk rechazó la consulta."""


def run_command(program, *args, timeout=0.9):
    executable = shutil.which(program, path=BIN_PATH)
    if not executable:
        raise CommandError(f"No se encontró {program}")
    try:
        result = subprocess.run(
            [executable, *args], capture_output=True, encoding="utf-8",
            errors="replace", timeout=timeout, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CommandError(f"{program}: {exc}") from exc
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()
        raise CommandError(detail or f"{program} terminó con código {result.returncode}")
    return result.stdout


def valid_link(value):
    return bool(NODE.fullmatch(value) or SIP_LINK.fullmatch(value) or
                (CALLSIGN.fullmatch(value) and re.search(r"[A-Z]", value)))


def asterisk(command):
    # Asterisk recibe un solo argumento con la orden completa. No se usa shell.
    output = run_command("asterisk", "-rx", command, timeout=2)
    if re.search(r"no such command|unable to connect|unable to find|"
                 r"not found|invalid node|unknown node|no such node|"
                 r"^\s*(?:usage|error)\s*:", output, re.I | re.M):
        raise CommandError(output.strip())
    return output


def parse_rpt_variables(output):
    """Formato de rpt show variables y sección RPT_* de rpt xnode."""
    return {key: value.strip() for key, value in re.findall(
        r"^[ \t]*(RPT_[A-Z0-9_]+)[ \t]*=[ \t]*([^\r\n]*)", output, re.M)}


def parse_sip_channels(output, caller_ids=None):
    """Mismos índices y filtro Up de core show channels concise que nextion.go."""
    labels = {}
    for line in output.splitlines():
        fields = line.strip().split("!")
        if len(fields) < 5 or fields[4].strip().lower() != "up":
            continue
        channel = fields[0].strip()
        if not SIP_CHANNEL.fullmatch(channel):
            continue
        endpoint = channel[len("PJSIP/"):].rsplit("-", 1)[0]
        caller = fields[7].strip() if len(fields) > 7 else ""
        if caller.lower() == "<unknown>":
            caller = ""
        caller = caller or (caller_ids or {}).get(endpoint) or endpoint
        labels["SIP:" + channel] = "SIP " + display_text(caller)
    return labels


def read_sip_caller_ids(path="/etc/asterisk/pjsip.conf", seen=None):
    """Lee callerid de endpoints locales y #include sin ejecutar #exec."""
    import glob
    path = Path(path)
    seen = set() if seen is None else seen
    if str(path.resolve()) in seen or len(seen) >= 64:
        return {}
    seen.add(str(path.resolve()))
    result, section = {}, None
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return result
    for line in lines:
        line = line.strip()
        include = re.match(r'^#(?:try)?include\s+["<]?([^">]+)[">]?\s*$', line)
        if include:
            pattern = Path(include[1].strip())
            if not pattern.is_absolute():
                pattern = path.parent / pattern
            for child in sorted(glob.glob(str(pattern))):
                result.update(read_sip_caller_ids(child, seen))
            continue
        if line.startswith(";"):
            continue
        header = re.match(r"^\[([^]]+)\]", line)
        if header:
            section = header[1]
        elif section:
            match = re.match(r"callerid\s*=\s*(.*?)\s*(?:;.*)?$", line, re.I)
            if match:
                caller = match[1].strip()
                # CallerID estándar: nombre <número>; reproduce el número de la CLI.
                number = re.search(r"<([^>]+)>", caller)
                result[section] = number[1] if number else caller.strip('"')
    return result


def merge_sip_links(nodes, sources, labels):
    """Relaciona SIP/1005 de RPT_ALINKS con el canal PJSIP real, sin duplicarlo."""
    aliases, remapped = {}, []
    labels = dict(labels)
    channels = list(labels)
    for node in nodes:
        if not SIP_LINK.fullmatch(node):
            remapped.append(node)
            continue
        identity = node.split("/", 1)[1]
        # Prefiere endpoint exacto; caller ID es solo respaldo si es único.
        matches = [channel for channel in channels
                   if channel[len("SIP:PJSIP/"):].rsplit("-", 1)[0] == identity]
        if not matches:
            matches = [channel for channel in channels if labels[channel] == "SIP " + identity]
        if len(matches) == 1:
            aliases[node] = matches[0]
            remapped.append(matches[0])
        elif len(matches) > 1:
            # Cada llamada conserva su propio canal; la actividad sigue siendo del endpoint.
            remapped.extend(matches)
            labels[node] = "SIP " + display_text(identity)
        else:
            remapped.append(node)
            labels[node] = "SIP " + display_text(identity)
    for channel in channels:
        remapped.append(channel)
    remapped = list(dict.fromkeys(remapped))
    sources = list(dict.fromkeys(aliases.get(source, source) for source in sources))
    return remapped, sources, labels


def parse_adjacent_links(value):
    """RPT_ALINKS: cantidad,nodo+modo+[K|U]. No incluye enlaces indirectos."""
    links, receiving = [], []
    parts = value.strip().split(",")
    if not parts or not parts[0].strip().isdigit():
        raise CommandError("RPT_ALINKS no tiene un formato reconocido")
    for item in parts[1:]:
        match = re.fullmatch(r"(.+?)[TRCL]([KU])", item.strip())
        if not match or not valid_link(match[1]):
            raise CommandError(f"Enlace adyacente no reconocido: {item!r}")
        node, keyed = match.groups()
        if node not in links:
            links.append(node)
        if keyed == "K" and node not in receiving:
            receiving.append(node)
    if len(links) != int(parts[0]):
        raise CommandError("RPT_ALINKS tiene una cantidad incoherente")
    return sorted(links, key=lambda node: (len(node), node)), receiving


def parse_rpt_stats(output):
    fields = {
        key.strip(" .…\t").lower(): value.strip()
        for key, value in (line.split(":", 1) for line in output.splitlines() if ":" in line)
    }
    signal = fields.get("signal on input", "").upper()
    links = fields.get("nodes currently connected to us")
    if signal not in ("YES", "NO") or links is None:
        detail = next((line for line in output.splitlines() if line.strip()), "")
        raise CommandError(f"rpt stats no devolvió el estado del nodo. {detail}".strip())
    if links.upper() in ("NONE", "<NONE>", "(NONE)", "N/A"):
        links = ""
    candidates = (item.strip().upper() for item in links.split(","))
    nodes = sorted({item for item in candidates if valid_link(item)},
                   key=lambda item: (len(item), item))
    return signal == "YES", nodes


class ASLRadio:
    def __init__(self, node):
        self.node = node
        self.variable_command = None
        self.activity = ""
        self.labels = {}
        self.last_sip_error = ""
        self.sip_caller_ids = {}
        self.next_sip_config = 0

    def variables(self):
        commands = [f"rpt show variables {self.node}", f"rpt xnode {self.node}"]
        if self.variable_command:
            commands.remove(self.variable_command)
            commands.insert(0, self.variable_command)
        failures = []
        for command in commands:
            try:
                values = parse_rpt_variables(asterisk(command))
                if not any(name in values for name in ("RPT_TXKEYED", "RPT_RXKEYED")):
                    raise CommandError("La consulta no devolvió variables de estado")
                self.variable_command = command
                return values
            except CommandError as exc:
                failures.append(f"{command}: {exc}")
        raise CommandError("; ".join(failures))

    def snapshot(self):
        values = self.variables()
        rx_value, tx_value = values.get("RPT_RXKEYED"), values.get("RPT_TXKEYED")
        if rx_value not in ("0", "1") or tx_value not in ("0", "1"):
            raise CommandError("Asterisk no devolvió RPT_RXKEYED y RPT_TXKEYED válidos")
        rx, tx = rx_value == "1", tx_value == "1"
        adjacent = values.get("RPT_ALINKS")
        if adjacent is None:
            # Puede no existir antes de la primera conexión; stats enumera vecinos.
            _, nodes = parse_rpt_stats(asterisk(f"rpt stats {self.node}"))
            sources = []
        else:
            nodes, sources = parse_adjacent_links(adjacent)
        if rx:
            sources.insert(0, self.node)
        if time.monotonic() >= self.next_sip_config:
            self.sip_caller_ids = read_sip_caller_ids()
            self.next_sip_config = time.monotonic() + 30
        try:
            self.labels = parse_sip_channels(asterisk("core show channels concise"),
                                             self.sip_caller_ids)
            self.last_sip_error = ""
        except CommandError as exc:
            self.labels = {}
            if str(exc) != self.last_sip_error:
                logging.warning("Consulta SIP: %s", exc)
            self.last_sip_error = str(exc)
        nodes, sources, self.labels = merge_sip_links(nodes, sources, self.labels)
        return rx, tx, nodes, sources

    def direct_links(self):
        rx, _, nodes, _ = self.snapshot()
        return rx, nodes

    def status(self):
        rx, tx, nodes, sources = self.snapshot()
        self.activity = ", ".join(self.labels.get(source, source) for source in sources)
        # Como el Go: recepción local o de enlaces tiene prioridad visual.
        if rx or sources:
            return "rx", nodes
        if tx:
            self.activity = self.node
            return "tx", nodes
        return "idle", nodes

    def link(self, method, target=""):
        if method == 1 and target.startswith("SIP:"):
            channel = target[len("SIP:"):]
            if not SIP_CHANNEL.fullmatch(channel):
                raise CommandError("Canal SIP inválido")
            asterisk("channel request hangup " + channel)
            return
        if method not in (1, 3, 6) or (method in (1, 3) and not valid_link(target)):
            raise CommandError("Acción de enlace inválida")
        command = f"rpt cmd {self.node} ilink {method}"
        try:
            output = asterisk(f"{command} {target}".rstrip())
        except CommandError as exc:
            if method != 6 or not re.search(r"^\s*Usage:\s*rpt cmd\b", str(exc), re.I | re.M):
                raise
            output = asterisk(f"{command} *")
        if re.search(r"no such command|unable to connect|unable to find|"
                     r"not found|invalid node|^\s*(?:usage|error)\s*:",
                     output, re.I | re.M):
            raise CommandError(output.strip())


class DemoRadio:
    def __init__(self):
        self.started = time.monotonic()
        self.labels = {"SIP:PJSIP/phone1-00000001": "SIP KQ4HPI",
                       "SIP:PJSIP/phone2-00000002": "SIP N2AST"}
        self.nodes = ["41777", "46105", "61520", *self.labels,
                      "*AMERINET*", "KQ4HPI-L", "61628"]

    def status(self):
        states = ("idle", "rx", "tx", "idle")
        return states[int((time.monotonic() - self.started) / 4) % 4], self.nodes.copy()

    def direct_links(self):
        return self.status()[0] == "rx", self.nodes.copy()

    def link(self, method, target=""):
        if method == 3:
            self.nodes.append(target)
        elif method == 1:
            self.nodes.remove(target)
        elif method == 6:
            self.nodes.clear()


def percent(used, total):
    return f"{100 * used / total:.0f}%" if total > 0 and 0 <= used <= total else "--"


class LinuxTelemetry:
    def __init__(self, interface, voltage_file):
        self.interface = interface
        self.voltage_file = voltage_file
        self.cpu_previous = None
        self.voltage_label = "Volt"

    def read(self):
        return {
            "t_ip": self.ip(), "t_temp": self.temperature(), "t_volt": self.voltage(),
            "t_volt_label": self.voltage_label,
            "t_mem": self.memory(), "t_dsk": self.disk(), "t_cpu": self.cpu(),
        }

    def ip(self):
        device = ("dev", self.interface) if self.interface else ()
        try:
            output = run_command("ip", "-o", "-4", "addr", "show",
                                 *device, "scope", "global", "up", timeout=0.4)
        except CommandError:
            return "--"
        addresses = []
        for line in output.splitlines():
            fields = line.split()
            if len(fields) < 4 or fields[2] != "inet":
                continue
            address = ipaddress.IPv4Interface(fields[3]).ip
            if not (address.is_loopback or address.is_link_local):
                addresses.append((not fields[1].startswith(("e", "wl")), str(address)))
        return min(addresses, key=lambda pair: pair[0])[1] if addresses else "--"

    @staticmethod
    def temperature():
        paths = [Path("/sys/class/thermal/thermal_zone0/temp")]
        # En x86 suele estar expuesto como coretemp o k10temp en hwmon.
        for sensor in Path("/sys/class/hwmon").glob("hwmon*"):
            try:
                if sensor.joinpath("name").read_text().strip() in (
                    "coretemp", "k10temp", "zenpower", "cpu_thermal", "soc_thermal"
                ):
                    paths.insert(0, sensor / "temp1_input")
            except OSError:
                continue
        for path in paths:
            try:
                return f"{int(path.read_text()) / 1000:.1f}°C"
            except (OSError, ValueError):
                continue
        return "--"

    def voltage(self):
        self.voltage_label = "Volt"
        if self.voltage_file:
            try:
                volts = int(Path(self.voltage_file).read_text()) / 1000
                return f"{volts:.2f}V" if volts > 0 else "--"
            except (OSError, ValueError):
                return "--"
        try:
            output = run_command("vcgencmd", "pmic_read_adc", "EXT5V_V", timeout=0.5)
            match = re.search(r"^\s*EXT5V_V\s+volt\(\d+\)\s*=\s*"
                              r"(\d+(?:\.\d+)?)V\s*$", output, re.M)
            volts = float(match[1]) if match else 0
            if volts > 0:
                return f"{volts:.2f}V"
        except CommandError:
            pass
        # Pi 3/4: el núcleo se puede consultar, pero NO mide la alimentación 5V.
        self.voltage_label = "Vcore"
        try:
            output = run_command("vcgencmd", "measure_volts", "core", timeout=0.5)
            match = re.fullmatch(r"\s*volt\s*=\s*(\d+(?:\.\d+)?)V\s*", output)
            volts = float(match[1]) if match else 0
            return f"{volts:.2f}V" if volts > 0 else "--"
        except CommandError:
            return "--"

    @staticmethod
    def memory():
        try:
            values = {}
            for line in Path("/proc/meminfo").read_text().splitlines():
                name, _, value = line.partition(":")
                if name in ("MemTotal", "MemAvailable"):
                    values[name] = int(value.split()[0])
            total = values["MemTotal"]
            return percent(total - values["MemAvailable"], total)
        except (OSError, ValueError, KeyError, IndexError):
            return "--"

    @staticmethod
    def disk():
        try:
            info = os.statvfs("/")
            used = info.f_blocks - info.f_bfree
            return percent(used, used + info.f_bavail)
        except OSError:
            return "--"

    def cpu(self):
        try:
            with Path("/proc/stat").open() as stats:
                fields = stats.readline().split()
            if len(fields) < 9 or fields[0] != "cpu":
                return "--"
            ticks = list(map(int, fields[1:9]))
        except (OSError, ValueError):
            return "--"
        previous = self.cpu_previous
        current = sum(ticks), ticks[3] + ticks[4]
        self.cpu_previous = current
        if previous is None:
            return "--"
        total = current[0] - previous[0]
        idle = current[1] - previous[1]
        return percent(total - idle, total)


def open_serial(path):
    port = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    try:
        fcntl.flock(port, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.ioctl(port, termios.TIOCEXCL)
        attrs = termios.tcgetattr(port)
        attrs[:6] = [
            0, 0, termios.CS8 | termios.CREAD | termios.CLOCAL, 0,
            termios.B9600, termios.B9600,
        ]
        attrs[6][termios.VMIN] = 1
        attrs[6][termios.VTIME] = 0
        termios.tcsetattr(port, termios.TCSAFLUSH, attrs)
    except OSError:
        os.close(port)
        raise
    return port


def display_text(value):
    """Texto Nextion ISO-8859-1 sin comillas, controles o terminadores."""
    return "".join(char if ord(char) <= 255 else "?" for char in str(value).strip()
                   if char != '"' and ord(char) >= 32 and ord(char) not in (127, 255))


def write_serial(port, commands):
    payload = b"".join(command.encode("latin-1") + END for command in commands)
    deadline = time.monotonic() + 2
    while payload:
        remaining = max(0, deadline - time.monotonic())
        if not select.select([], [port], [], remaining)[1]:
            raise TimeoutError("La Nextion no acepta datos por el puerto serial")
        try:
            written = os.write(port, payload)
        except BlockingIOError:
            continue
        payload = payload[written:]


class NextionEvents:
    """Decodifica los mensajes ASCII del HMI y sendme de la página 0."""

    def __init__(self):
        self.pending = b""

    def feed(self, data):
        self.pending += data
        events = []
        while self.pending:
            head = self.pending
            if head[0] == 0x66:  # sendme: código, número de página, tres FF.
                if len(head) < 5:
                    break
                if head[2:5] == END:
                    self.pending = head[5:]
                    if head[1] == 0:
                        events.append(("PANEL_READY", ""))
                    continue
            if head.startswith(b"HOLD_N"):
                end = head.find(b"\n")
                if end < 0 and len(head) <= 128:
                    break
                if 0 < end <= 128:
                    match = re.fullmatch(rb"HOLD_N([1-8])(?:[ \t]+([^\r\n]*))?\r?", head[:end])
                    self.pending = head[end + 1:]
                    if match:
                        events.append(("HOLD_N" + match[1].decode(),
                                       (match[2] or b"").decode("latin-1")))
                    continue
            token = next((item for item in RELEASES if head.startswith(item)), None)
            if token is not None:
                events.append((token.decode("ascii"), ""))
                self.pending = head[len(token):]
                continue
            if b"HOLD_N".startswith(head) or any(item.startswith(head) for item in RELEASES):
                break
            self.pending = head[1:]
        return events


class Panel:
    def __init__(self, radio, settings):
        self.radio = radio
        self.node = settings.node
        self.favorite = settings.favorite
        self.english = settings.lang == "en"
        self.demo = settings.demo
        self.state = "offline"
        self.nodes = []
        self.slots = [""] * SLOT_COUNT
        self.telemetry = {}
        self.held = {}
        self.sent = {}
        self.pending_system = None
        self.message = None
        self.last_error = ""

    def label(self, spanish, english):
        return english if self.english else spanish

    def refresh(self):
        try:
            self.state, self.nodes = self.radio.status()
            self.last_error = ""
        except CommandError as exc:
            self.state, self.nodes = "offline", []
            if str(exc) != self.last_error:
                logging.warning("ASL3: %s", exc)
            self.last_error = str(exc)
        self.slots = [node if node in self.nodes else "" for node in self.slots]
        newcomers = iter(node for node in self.nodes if node not in self.slots)
        self.slots = [node or next(newcomers, "") for node in self.slots]

    def handle(self, command, target):
        now = time.monotonic()
        if command == "PANEL_READY":
            self.sent.clear()
            self.held.clear()
            self.pending_system = None
            return
        if command in SYS_ACTIONS:
            self.pending_system = command, now + 10
            return
        if command == "SYS_CANCEL":
            self.pending_system = None
            return
        if command == "SYS_CONFIRM":
            self.confirm_system(now)
            return
        if command == "CONN_FAV":
            method, target = 3, self.favorite
        elif command == "DISC_ALL":
            method, target = 6, ""
        else:
            if not re.fullmatch(r"(?:HOLD|DISC)_N[1-8]", command):
                return
            slot = int(command[-1]) - 1
            if command.startswith("HOLD_"):
                self.held.pop(slot, None)
                # El Go captura el nodo del slot; el texto recibido no es el ID.
                if self.slots[slot]:
                    self.held[slot] = self.slots[slot], now
                return
            held = self.held.pop(slot, None)
            if not held or now - held[1] > 8:
                return
            method, target = 1, held[0]
        try:
            _, active = self.radio.direct_links()
            if (method == 1 and target not in active) or (method == 3 and target in active):
                return
            self.radio.link(method, target)
            logging.info("ASL3 recibió ilink %s %s", method, target)
        except CommandError as exc:
            logging.warning("%s: %s", command, exc)

    def confirm_system(self, now):
        pending, self.pending_system = self.pending_system, None
        if pending is None or now >= pending[1]:
            return
        args, es, en, running_es, running_en = SYS_ACTIONS[pending[0]]
        if self.demo:
            message = "Demo: " + self.label(es, en)
        else:
            try:
                if os.geteuid() != 0:
                    raise CommandError("Los botones de sistema requieren ejecutar el puente como root")
                run_command("systemctl", "--no-block", *args, timeout=1)
                message = self.label(running_es, running_en)
            except CommandError as exc:
                logging.warning("%s: %s", es, exc)
                message = self.label("Acción del sistema fallida", "System action failed")
        logging.info("%s", message)
        self.message = message, time.monotonic() + 4

    def render(self, port, heartbeat):
        now = time.localtime()
        heading = self.label("ENLACES ACTIVOS", "ACTIVE LINKS")
        extra = len(self.nodes) - SLOT_COUNT
        if extra > 0:
            heading += f"  +{extra}"
        es, en, color = STATUS[self.state]
        status = self.label(es, en)
        if not self.demo and self.state != "offline":
            status = getattr(self.radio, "activity", "")
        if self.message and self.message[1] > time.monotonic():
            status = self.message[0]
        values = {
            "t_actions": self.label("ACCIONES", "ACTIONS"),
            "b_fav": self.label("Favorito", "Favorite"),
            "b_disc_all": self.label("Cortar todos", "Disconnect all"),
            "t_hint": self.label("Toca para cortar", "Tap to disconnect"),
            "t_admin_title": self.label("SISTEMA", "SYSTEM"),
            "b_restart": self.label("Reiniciar Asterisk", "Restart Asterisk"),
            "b_reboot": self.label("Reiniciar Linux", "Reboot Linux"),
            "b_poweroff": self.label("Apagar Linux", "Power Off Linux"),
            "b_admin_no": self.label("Cancelar", "Cancel"),
            "b_admin_ok": self.label("Confirmar", "Confirm"),
            "t_nodo": self.label("Nodo: ", "Node: ") + self.node,
            "t_ip": "IP: " + self.telemetry.get("t_ip", "--"),
            "t_temp": "Temp " + self.telemetry.get("t_temp", "--"),
            "t_volt": self.telemetry.get("t_volt_label", "Volt") + " " + self.telemetry.get("t_volt", "--"),
            "t_mem": "Mem " + self.telemetry.get("t_mem", "--"),
            "t_dsk": "Dsk " + self.telemetry.get("t_dsk", "--"),
            "t_cpu": "CPU " + self.telemetry.get("t_cpu", "--"),
            "t_date": time.strftime("%Y-%m-%d", now),
            "t_time": time.strftime("%H:%M:%S", now),
            "t_links": heading,
            "t0": status,
        }
        for index, node in enumerate(self.slots, 1):
            values[f"t_n{index}"] = getattr(self.radio, "labels", {}).get(node, node) or "--"
        commands = {"va_lang.val": f"={int(self.english)}"}
        commands.update({f"{key}.txt": f'="{display_text(value)}"' for key, value in values.items()})
        commands.update({"b_estado.bco": f"={color}", "t0.bco": f"={color}"})
        for index, node in enumerate(self.slots, 1):
            visibility = f",{int(bool(node))}"
            commands[f"vis b_d{index}"] = visibility
            commands[f"tsw b_d{index}"] = visibility
        changed = {key: value for key, value in commands.items() if self.sent.get(key) != value}
        outgoing = ["va_alive.val=4"] if heartbeat else []
        outgoing.extend(key + value for key, value in changed.items())
        if outgoing:
            write_serial(port, outgoing)
            self.sent.update(changed)


DEMO_SENSORS = {
    "t_ip": "192.168.1.27", "t_temp": "48.6°C", "t_volt": "5.08V",
    "t_mem": "42%", "t_dsk": "31%", "t_cpu": "18%",
}


def serial_loop(port, panel, sensors):
    events = NextionEvents()
    panel.handle("PANEL_READY", "")
    next_radio = next_system = next_beat = 0.0
    while True:
        now = time.monotonic()
        if now >= next_radio:
            panel.refresh()
            interval = 1.5 if panel.state == "offline" else 0.5
            next_radio = time.monotonic() + interval
        if now >= next_system:
            panel.telemetry = DEMO_SENSORS if panel.demo else sensors.read()
            next_system = time.monotonic() + 5
        now = time.monotonic()
        heartbeat = now >= next_beat
        panel.render(port, heartbeat)
        if heartbeat:
            next_beat = now + 1
        if select.select([port], [], [], 0.1)[0]:
            try:
                data = os.read(port, 512)
            except BlockingIOError:
                continue
            if not data:
                raise OSError("Se desconectó el puerto serial")
            for command, target in events.feed(data):
                panel.handle(command, target)


def arguments():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--config", "-config", help="archivo INI (por defecto panel.ini si existe)")
    parser.add_argument("--port", "-port", help="puerto serial (por defecto /dev/ttyUSB0)")
    parser.add_argument("--node", help="número del nodo AllStarLink local")
    parser.add_argument("--favorite", help="nodo favorito (por defecto 61627)")
    parser.add_argument("--lang", choices=("es", "en"), help="idioma del panel")
    parser.add_argument("--demo", "-demo", action="store_true", help="simular sin ejecutar controles reales")
    parser.add_argument("--once", action="store_true", help="mostrar las lecturas en JSON y salir sin usar serial")
    options = parser.parse_args()
    config = configparser.ConfigParser(interpolation=None)
    config_path = Path(options.config or "panel.ini")
    if options.config or config_path.exists():
        try:
            with config_path.open(encoding="utf-8-sig") as file:
                config.read_file(file)
        except (OSError, ValueError, configparser.Error) as exc:
            parser.error(str(exc))
    options.port = options.port or config.get("panel", "puerto", fallback="") or "/dev/ttyUSB0"
    options.lang = options.lang or config.get("panel", "idioma", fallback="").lower() or "es"
    options.node = options.node or config.get("asl3", "nodo_local", fallback="")
    options.favorite = options.favorite or config.get("asl3", "nodo_favorito", fallback="") or "61627"
    options.interface = config.get("panel", "interfaz", fallback="")
    options.voltage_file = config.get("panel", "voltaje_mv_archivo", fallback="")
    if options.demo and not options.node:
        options.node = "616262"
    if not NODE.fullmatch(options.node):
        parser.error("indica tu número de nodo con --node o [asl3] nodo_local")
    if not NODE.fullmatch(options.favorite) or options.favorite == options.node:
        parser.error("el favorito debe ser un número válido y distinto al nodo local")
    if options.lang not in ("es", "en"):
        parser.error("el idioma debe ser es o en")
    return options


def main():
    settings = arguments()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    radio = DemoRadio() if settings.demo else ASLRadio(settings.node)
    panel = Panel(radio, settings)
    sensors = LinuxTelemetry(settings.interface, settings.voltage_file)
    if settings.once:
        panel.refresh()
        if not settings.demo:
            sensors.cpu()
            time.sleep(0.25)
        telemetry = DEMO_SENSORS if settings.demo else sensors.read()
        print(json.dumps({
            "nodo": settings.node, "estado": panel.state, "enlaces": panel.nodes,
            "etiquetas": getattr(radio, "labels", {}),
            "origen_audio": getattr(radio, "activity", ""),
            "comando_estado": getattr(radio, "variable_command", None),
            "error": panel.last_error or None, "telemetria": telemetry,
        }, ensure_ascii=False, indent=2))
        return int(panel.state == "offline")
    while True:
        try:
            port = open_serial(settings.port)
            try:
                logging.info("Nextion en %s a 9600 baudios", settings.port)
                serial_loop(port, panel, sensors)
            finally:
                os.close(port)
        except OSError as exc:
            logging.warning("Serial %s: %s. Reintento en 2 segundos.", settings.port, exc)
            time.sleep(2)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(0)
