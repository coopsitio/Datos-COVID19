"""Lectura y escritura de lo que es propio de Windows: registro, tareas y programas."""

from __future__ import annotations

import csv
import io
import os
from pathlib import Path

from .util import (
    Consola,
    ejecutar,
    es_windows,
    parece_secreto,
    ruta_segura,
)

if es_windows():  # pragma: no cover - solo existe en Windows
    import winreg
else:  # Permite escanear/inspeccionar paquetes desde otro sistema operativo.
    winreg = None  # type: ignore[assignment]

CLAVE_ENV_USUARIO = "Environment"
CLAVE_ENV_SISTEMA = r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"

CLAVES_DESINSTALAR = [
    ("HKLM", r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    ("HKLM", r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
    ("HKCU", r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
]


# --------------------------------------------------------------------------- #
# Variables de entorno
# --------------------------------------------------------------------------- #

def _raiz(nombre: str):
    return winreg.HKEY_LOCAL_MACHINE if nombre == "HKLM" else winreg.HKEY_CURRENT_USER


def leer_variables(ambito: str) -> dict:
    """Lee las variables de entorno persistentes del registro.

    `ambito` es "usuario" o "sistema". Devuelve {NOMBRE: {"valor":..., "tipo":...}}.
    El tipo importa: REG_EXPAND_SZ (2) conserva referencias como %USERPROFILE%.
    """
    if winreg is None:
        return {}
    if ambito == "usuario":
        raiz, subclave = winreg.HKEY_CURRENT_USER, CLAVE_ENV_USUARIO
    else:
        raiz, subclave = winreg.HKEY_LOCAL_MACHINE, CLAVE_ENV_SISTEMA

    variables: dict[str, dict] = {}
    try:
        with winreg.OpenKey(raiz, subclave, 0, winreg.KEY_READ) as clave:
            _, cantidad_valores, _ = winreg.QueryInfoKey(clave)
            for indice in range(cantidad_valores):
                try:
                    nombre, valor, tipo = winreg.EnumValue(clave, indice)
                except OSError:
                    continue
                if not isinstance(valor, str):
                    continue
                variables[nombre] = {
                    "valor": valor,
                    "tipo": tipo,
                    "sensible": parece_secreto(nombre),
                }
    except OSError:
        return {}
    return variables


def escribir_variable(ambito: str, nombre: str, valor: str, tipo: int = 1) -> tuple[bool, str]:
    """Escribe una variable persistente en el registro."""
    if winreg is None:
        return False, "solo disponible en Windows"
    if ambito == "usuario":
        raiz, subclave = winreg.HKEY_CURRENT_USER, CLAVE_ENV_USUARIO
    else:
        raiz, subclave = winreg.HKEY_LOCAL_MACHINE, CLAVE_ENV_SISTEMA
    try:
        with winreg.OpenKey(raiz, subclave, 0, winreg.KEY_SET_VALUE) as clave:
            winreg.SetValueEx(clave, nombre, 0, tipo, valor)
    except PermissionError:
        return False, "sin permisos (las variables de sistema requieren consola como administrador)"
    except OSError as exc:
        return False, str(exc)
    return True, ""


def avisar_cambio_de_entorno() -> None:
    """Notifica a Windows que el entorno cambió, para no tener que reiniciar sesión."""
    if not es_windows():
        return
    try:
        import ctypes

        HWND_BROADCAST = 0xFFFF
        WM_SETTINGCHANGE = 0x001A
        SMTO_ABORTIFHUNG = 0x0002
        resultado = ctypes.c_ulong()
        ctypes.windll.user32.SendMessageTimeoutW(
            HWND_BROADCAST, WM_SETTINGCHANGE, 0,
            ctypes.c_wchar_p("Environment"), SMTO_ABORTIFHUNG, 5000,
            ctypes.byref(resultado),
        )
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# Tareas programadas
# --------------------------------------------------------------------------- #

def listar_tareas(consola: Consola) -> list[dict]:
    """Lista las tareas del Programador de tareas, excluyendo las de Microsoft."""
    codigo, salida, error = ejecutar(["schtasks", "/query", "/fo", "csv", "/v"], timeout=180)
    if codigo != 0:
        consola.aviso(f"no se pudieron listar las tareas programadas: {error.strip() or codigo}")
        return []

    tareas: list[dict] = []
    vistas: set[str] = set()
    lector = csv.DictReader(io.StringIO(salida))
    for fila in lector:
        nombre = (fila.get("TaskName") or fila.get("Nombre de tarea") or "").strip()
        if not nombre or nombre.startswith('"') or nombre in vistas:
            continue
        # Las tareas propias de Windows no se migran: el equipo nuevo ya las trae.
        if nombre.lower().startswith("\\microsoft\\"):
            continue
        vistas.add(nombre)
        tareas.append(
            {
                "nombre": nombre,
                "estado": (fila.get("Status") or fila.get("Estado") or "").strip(),
                "accion": (fila.get("Task To Run") or fila.get("Tarea que se ejecuta") or "").strip(),
                "usuario": (fila.get("Run As User") or fila.get("Ejecutar como usuario") or "").strip(),
                "programacion": (fila.get("Schedule Type") or fila.get("Tipo de programación") or "").strip(),
                "proxima_ejecucion": (fila.get("Next Run Time") or fila.get("Hora próxima ejecución") or "").strip(),
            }
        )
    return tareas


def exportar_tarea_xml(nombre: str) -> str | None:
    """Devuelve el XML de una tarea, que es lo que permite recrearla idéntica."""
    codigo, salida, _ = ejecutar(["schtasks", "/query", "/tn", nombre, "/xml", "ONE"])
    if codigo != 0 or "<?xml" not in salida:
        return None
    return salida[salida.index("<?xml"):]


def guardar_tareas(carpeta_destino: Path, consola: Consola) -> list[dict]:
    """Exporta cada tarea a un XML dentro de `carpeta_destino`."""
    tareas = listar_tareas(consola)
    if not tareas:
        return []
    carpeta_destino.mkdir(parents=True, exist_ok=True)
    exportadas = []
    for tarea in tareas:
        xml = exportar_tarea_xml(tarea["nombre"])
        if xml is None:
            consola.aviso(f"tarea sin XML exportable: {tarea['nombre']}")
            tarea["archivo_xml"] = None
            exportadas.append(tarea)
            continue
        nombre_archivo = ruta_segura(tarea["nombre"].lstrip("\\")) + ".xml"
        (carpeta_destino / nombre_archivo).write_text(xml, encoding="utf-16")
        tarea["archivo_xml"] = nombre_archivo
        exportadas.append(tarea)
    return exportadas


def carpeta_inicio() -> Path:
    base = os.environ.get("APPDATA", str(Path.home() / "AppData/Roaming"))
    return Path(base) / "Microsoft/Windows/Start Menu/Programs/Startup"


# --------------------------------------------------------------------------- #
# Programas instalados
# --------------------------------------------------------------------------- #

def programas_del_registro() -> list[dict]:
    """Inventario completo de programas instalados según el registro."""
    if winreg is None:
        return []
    programas: list[dict] = []
    vistos: set[tuple] = set()
    for raiz_nombre, subclave in CLAVES_DESINSTALAR:
        try:
            acceso = winreg.KEY_READ | winreg.KEY_WOW64_64KEY
            with winreg.OpenKey(_raiz(raiz_nombre), subclave, 0, acceso) as clave:
                cantidad_subclaves, _, _ = winreg.QueryInfoKey(clave)
                for indice in range(cantidad_subclaves):
                    try:
                        nombre_sub = winreg.EnumKey(clave, indice)
                        with winreg.OpenKey(clave, nombre_sub) as entrada:
                            def valor(campo: str) -> str:
                                try:
                                    return str(winreg.QueryValueEx(entrada, campo)[0])
                                except OSError:
                                    return ""

                            nombre = valor("DisplayName")
                            if not nombre or valor("SystemComponent") == "1":
                                continue
                            firma = (nombre, valor("DisplayVersion"))
                            if firma in vistos:
                                continue
                            vistos.add(firma)
                            programas.append(
                                {
                                    "nombre": nombre,
                                    "version": valor("DisplayVersion"),
                                    "editor": valor("Publisher"),
                                    "origen": f"{raiz_nombre}\\{nombre_sub}",
                                }
                            )
                    except OSError:
                        continue
        except OSError:
            continue
    return sorted(programas, key=lambda p: p["nombre"].lower())


def inventario_gestores(carpeta_destino: Path, consola: Consola) -> dict:
    """Consulta cada gestor de paquetes disponible y guarda sus listas."""
    carpeta_destino.mkdir(parents=True, exist_ok=True)
    resultado: dict = {}

    # winget: su export es directamente re-importable en el equipo nuevo.
    destino_winget = carpeta_destino / "winget.json"
    codigo, _, error = ejecutar(
        ["winget", "export", "-o", str(destino_winget),
         "--accept-source-agreements", "--include-versions"],
        timeout=300,
    )
    if codigo != 0 or not destino_winget.exists():
        # Reintento sin versiones fijas: es más tolerante entre distintas builds.
        codigo, _, error = ejecutar(
            ["winget", "export", "-o", str(destino_winget), "--accept-source-agreements"],
            timeout=300,
        )
    if destino_winget.exists():
        resultado["winget"] = {"archivo": "winget.json"}
        consola.paso("winget exportado")
    else:
        resultado["winget"] = {"archivo": None, "error": error.strip() or "winget no disponible"}
        consola.aviso("winget no disponible o falló la exportación")

    codigo, salida, _ = ejecutar(["choco", "list", "--local-only", "--limit-output"])
    if codigo == 0 and salida.strip():
        (carpeta_destino / "chocolatey.txt").write_text(salida, encoding="utf-8")
        resultado["chocolatey"] = {"archivo": "chocolatey.txt", "paquetes": len(salida.strip().splitlines())}
        consola.paso("chocolatey exportado")

    for comando, archivo, etiqueta in (
        (["pip", "freeze"], "pip-requirements.txt", "pip"),
        (["npm", "ls", "-g", "--depth=0", "--parseable"], "npm-globales.txt", "npm"),
        (["code", "--list-extensions", "--show-versions"], "vscode-extensiones.txt", "vscode"),
    ):
        codigo, salida, _ = ejecutar(comando, timeout=180)
        if codigo == 0 and salida.strip():
            (carpeta_destino / archivo).write_text(salida, encoding="utf-8")
            resultado[etiqueta] = {
                "archivo": archivo,
                "paquetes": len(salida.strip().splitlines()),
            }
            consola.paso(f"{etiqueta} exportado")

    programas = programas_del_registro()
    if programas:
        import json

        (carpeta_destino / "programas-instalados.json").write_text(
            json.dumps(programas, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        resultado["registro"] = {
            "archivo": "programas-instalados.json",
            "paquetes": len(programas),
        }
        consola.paso(f"{len(programas)} programas encontrados en el registro")

    return resultado
