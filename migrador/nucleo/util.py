"""Utilidades comunes del migrador."""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

VERSION = "1.0"
FORMATO_PAQUETE = 1

# Nombres de variables que casi siempre contienen credenciales.
PATRON_SECRETO = re.compile(
    r"(KEY|TOKEN|SECRET|PASS|PWD|CRED|AUTH|PRIVATE|SESSION|COOKIE|SALT|SIGNATURE)",
    re.IGNORECASE,
)

# Variables que son listas de rutas: al importar se fusionan, nunca se reemplazan.
VARIABLES_TIPO_RUTA = {"PATH", "PYTHONPATH", "PSMODULEPATH", "CLASSPATH", "INCLUDE", "LIB"}

# Variables que Windows administra solo: copiarlas al equipo nuevo rompe cosas.
VARIABLES_DEL_SISTEMA = {
    "ALLUSERSPROFILE", "APPDATA", "COMMONPROGRAMFILES", "COMMONPROGRAMFILES(X86)",
    "COMMONPROGRAMW6432", "COMPUTERNAME", "COMSPEC", "DRIVERDATA", "HOMEDRIVE",
    "HOMEPATH", "LOCALAPPDATA", "LOGONSERVER", "NUMBER_OF_PROCESSORS", "OS",
    "PATHEXT", "PROCESSOR_ARCHITECTURE", "PROCESSOR_ARCHITEW6432",
    "PROCESSOR_IDENTIFIER", "PROCESSOR_LEVEL", "PROCESSOR_REVISION",
    "PROGRAMDATA", "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMW6432",
    "PUBLIC", "SESSIONNAME", "SYSTEMDRIVE", "SYSTEMROOT", "TEMP", "TMP",
    "USERDOMAIN", "USERDOMAIN_ROAMINGPROFILE", "USERNAME", "USERPROFILE",
    "WINDIR", "ONEDRIVE", "ONEDRIVECOMMERCIAL", "ONEDRIVECONSUMER",
}

# Carpetas que nunca vale la pena copiar.
CARPETAS_EXCLUIDAS = {
    "node_modules", "__pycache__", ".venv", "venv", "env", ".env.d", "dist",
    "build", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox", ".next",
    ".nuxt", "target", "bin", "obj", ".gradle", ".idea", ".vs", "vendor",
    "site-packages", ".ipynb_checkpoints", "coverage", ".terraform",
}

EXTENSIONES_EXCLUIDAS = {
    ".pyc", ".pyo", ".pyd", ".dll", ".exe", ".msi", ".iso", ".zip", ".7z",
    ".rar", ".tar", ".gz", ".log", ".tmp", ".swp", ".lock",
}


class Consola:
    """Salida por consola tolerante a la codificación de la terminal de Windows."""

    def __init__(self, silencioso: bool = False) -> None:
        self.silencioso = silencioso
        for flujo in (sys.stdout, sys.stderr):
            try:
                flujo.reconfigure(encoding="utf-8", errors="replace")
            except (AttributeError, ValueError):
                pass

    def _escribir(self, texto: str, flujo=None) -> None:
        destino = flujo or sys.stdout
        try:
            print(texto, file=destino)
        except UnicodeEncodeError:
            print(texto.encode("ascii", "replace").decode("ascii"), file=destino)

    def info(self, texto: str = "") -> None:
        if not self.silencioso:
            self._escribir(texto)

    def paso(self, texto: str) -> None:
        if not self.silencioso:
            self._escribir(f"  -> {texto}")

    def titulo(self, texto: str) -> None:
        if not self.silencioso:
            self._escribir("")
            self._escribir(texto)
            self._escribir("-" * len(texto))

    def aviso(self, texto: str) -> None:
        self._escribir(f"  [aviso] {texto}", sys.stderr)

    def error(self, texto: str) -> None:
        self._escribir(f"  [error] {texto}", sys.stderr)


def es_windows() -> bool:
    return platform.system() == "Windows"


def ahora_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def marca_tiempo() -> str:
    return datetime.now().strftime("%Y-%m-%d_%H%M")


def parece_secreto(nombre: str) -> bool:
    return bool(PATRON_SECRETO.search(nombre))


def es_variable_de_ruta(nombre: str) -> bool:
    return nombre.upper() in VARIABLES_TIPO_RUTA


def ejecutar(comando: list[str], timeout: int = 120) -> tuple[int, str, str]:
    """Ejecuta un comando y devuelve (codigo, stdout, stderr). Nunca lanza excepción."""
    ejecutable = shutil.which(comando[0])
    if ejecutable is None:
        return (127, "", f"no se encontró el ejecutable '{comando[0]}'")
    try:
        proceso = subprocess.run(
            [ejecutable, *comando[1:]],
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return (124, "", f"'{comando[0]}' superó el tiempo límite de {timeout}s")
    except OSError as exc:
        return (1, "", str(exc))
    return (
        proceso.returncode,
        proceso.stdout.decode("utf-8", "replace"),
        proceso.stderr.decode("utf-8", "replace"),
    )


def escribir_json(ruta: Path, datos) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(
        json.dumps(datos, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )


def leer_json(ruta: Path):
    return json.loads(ruta.read_text(encoding="utf-8-sig"))


def escribir_texto(ruta: Path, texto: str) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(texto, encoding="utf-8")


def formatear_tamano(bytes_: float) -> str:
    for unidad in ("B", "KB", "MB", "GB"):
        if bytes_ < 1024 or unidad == "GB":
            return f"{bytes_:.0f} {unidad}" if unidad == "B" else f"{bytes_:.1f} {unidad}"
        bytes_ /= 1024
    return f"{bytes_:.1f} GB"


def ruta_segura(nombre: str) -> str:
    """Convierte un nombre arbitrario en algo usable como nombre de archivo."""
    limpio = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", nombre).strip(" ._")
    return limpio[:120] or "sin_nombre"


def _es_subruta(hijo: Path, padre: Path) -> bool:
    try:
        hijo.relative_to(padre)
    except ValueError:
        return False
    return True


def copiar_arbol(
    origen: Path,
    destino: Path,
    excluir_carpetas: set[str] | None = None,
    excluir_extensiones: set[str] | None = None,
    limite_bytes: int | None = None,
    limite_archivo_bytes: int = 50 * 1024 * 1024,
) -> dict:
    """Copia un árbol de archivos aplicando exclusiones y límites de tamaño.

    Devuelve un resumen con lo copiado y lo omitido; no interrumpe la ejecución
    ante un archivo bloqueado o sin permisos.
    """
    excluir_carpetas = excluir_carpetas if excluir_carpetas is not None else CARPETAS_EXCLUIDAS
    excluir_extensiones = (
        excluir_extensiones if excluir_extensiones is not None else EXTENSIONES_EXCLUIDAS
    )
    resumen = {"archivos": 0, "bytes": 0, "omitidos": [], "truncado": False}
    origen = origen.resolve()

    for carpeta_actual, subcarpetas, archivos in os.walk(origen, onerror=lambda e: None):
        carpeta = Path(carpeta_actual)
        # Evita seguir enlaces simbólicos que apunten fuera del árbol.
        subcarpetas[:] = [
            d for d in subcarpetas
            if d.lower() not in excluir_carpetas and not (carpeta / d).is_symlink()
        ]
        relativa = carpeta.relative_to(origen)
        for archivo in archivos:
            ruta_archivo = carpeta / archivo
            if ruta_archivo.suffix.lower() in excluir_extensiones:
                continue
            try:
                tamano = ruta_archivo.stat().st_size
            except OSError:
                resumen["omitidos"].append({"ruta": str(ruta_archivo), "motivo": "ilegible"})
                continue
            if tamano > limite_archivo_bytes:
                resumen["omitidos"].append(
                    {"ruta": str(ruta_archivo), "motivo": f"pesa {formatear_tamano(tamano)}"}
                )
                continue
            if limite_bytes is not None and resumen["bytes"] + tamano > limite_bytes:
                resumen["truncado"] = True
                resumen["omitidos"].append(
                    {"ruta": str(ruta_archivo), "motivo": "se alcanzó el límite de tamaño"}
                )
                continue
            destino_archivo = destino / relativa / archivo
            try:
                destino_archivo.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(ruta_archivo, destino_archivo)
            except OSError as exc:
                resumen["omitidos"].append({"ruta": str(ruta_archivo), "motivo": str(exc)})
                continue
            resumen["archivos"] += 1
            resumen["bytes"] += tamano
    return resumen


def medir_arbol(origen: Path, limite_archivos: int = 200_000) -> dict:
    """Cuenta archivos y bytes de un árbol sin copiarlo."""
    total = {"archivos": 0, "bytes": 0}
    for carpeta_actual, subcarpetas, archivos in os.walk(origen, onerror=lambda e: None):
        subcarpetas[:] = [d for d in subcarpetas if d.lower() not in CARPETAS_EXCLUIDAS]
        for archivo in archivos:
            try:
                total["bytes"] += (Path(carpeta_actual) / archivo).stat().st_size
            except OSError:
                continue
            total["archivos"] += 1
            if total["archivos"] >= limite_archivos:
                return total
    return total


def _anclas() -> list[tuple[str, Path]]:
    """Carpetas base del perfil, de la más específica a la más general.

    Sirven para guardar rutas como '{APPDATA}/Code/User/settings.json' en vez de
    'C:/Users/antiguo/AppData/...': el equipo nuevo tiene otro nombre de usuario
    y puede tener Documentos redirigido a OneDrive.
    """
    inicio = Path.home()
    anclas: list[tuple[str, Path]] = []
    for token, valor in (
        ("APPDATA", os.environ.get("APPDATA")),
        ("LOCALAPPDATA", os.environ.get("LOCALAPPDATA")),
        ("ONEDRIVE", os.environ.get("OneDrive") or os.environ.get("OneDriveCommercial")),
        ("PROGRAMDATA", os.environ.get("ProgramData")),
    ):
        if valor:
            anclas.append((token, Path(valor)))

    for nombre in ("Documents", "Documentos"):
        candidato = inicio / nombre
        if candidato.exists():
            anclas.append(("DOCUMENTS", candidato))
            break

    anclas.append(("HOME", inicio))
    # La ruta más larga gana: HOME es prefijo de casi todas las demás.
    return sorted(anclas, key=lambda par: len(str(par[1])), reverse=True)


def a_plantilla(ruta: Path) -> str:
    """Convierte una ruta absoluta en una plantilla portable entre equipos."""
    ruta = Path(ruta)
    for token, base in _anclas():
        if _es_subruta(ruta, base):
            relativa = ruta.relative_to(base).as_posix()
            return f"{{{token}}}/{relativa}" if relativa else f"{{{token}}}"
    return ruta.as_posix()


def desde_plantilla(plantilla: str) -> Path:
    """Resuelve una plantilla contra las carpetas de ESTE equipo."""
    if not plantilla.startswith("{"):
        return Path(plantilla)
    cierre = plantilla.index("}")
    token = plantilla[1:cierre]
    resto = plantilla[cierre + 1:].lstrip("/")

    bases = {nombre: base for nombre, base in _anclas()}
    base = bases.get(token)
    if base is None:
        # El equipo nuevo puede no tener OneDrive o Documentos redirigido:
        # se cae a una ubicación equivalente dentro del perfil.
        respaldo = {
            "APPDATA": Path.home() / "AppData/Roaming",
            "LOCALAPPDATA": Path.home() / "AppData/Local",
            "DOCUMENTS": Path.home() / "Documents",
            "ONEDRIVE": Path.home() / "OneDrive",
            "PROGRAMDATA": Path("C:/ProgramData"),
        }
        base = respaldo.get(token, Path.home())
    return base / resto if resto else base


def datos_del_equipo() -> dict:
    return {
        "host": platform.node(),
        "usuario": os.environ.get("USERNAME") or os.environ.get("USER") or "desconocido",
        "so": platform.system(),
        "version_so": platform.version(),
        "release_so": platform.release(),
        "arquitectura": platform.machine(),
        "python": platform.python_version(),
        "ruta_python": sys.executable,
        "perfil": str(Path.home()),
    }


def es_administrador() -> bool:
    if not es_windows():
        return os.geteuid() == 0 if hasattr(os, "geteuid") else False
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False
