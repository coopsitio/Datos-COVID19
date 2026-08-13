"""Descubrimiento de configuraciones, automatizaciones de Claude y carpetas de trabajo."""

from __future__ import annotations

import json
import os
from pathlib import Path

from .util import (
    CARPETAS_EXCLUIDAS,
    Consola,
    medir_arbol,
)

# Subcarpetas de ~/.claude que son historial o caché: ocupan mucho y no aportan
# nada al equipo nuevo. Lo que sí importa (settings, comandos, agentes, skills,
# hooks) queda fuera de esta lista.
RUIDO_CLAUDE = {
    "projects", "todos", "shell-snapshots", "statsig", "logs", "file-history",
    "downloads", "ide", "tmp", "caches", "telemetry", "history.jsonl",
}

# Marcadores que delatan una carpeta de trabajo propia.
MARCADORES_PROYECTO = {
    ".claude", "claude.md", ".git", ".mcp.json", "requirements.txt",
    "package.json", "pyproject.toml", ".env", "makefile",
}
EXTENSIONES_PROYECTO = {".py", ".ps1", ".bat", ".cmd", ".sh", ".ipynb"}

# Carpetas del perfil de usuario que jamás contienen automatizaciones propias.
CARPETAS_PERFIL_IGNORADAS = {
    "appdata", "application data", "local settings", "nethood", "printhood",
    "recent", "sendto", "templates", "cookies", "searches", "links",
    "3d objects", "saved games", "contacts", "favorites", "ansel",
    "microsoft", "intelgraphicsprofiles", "onedrivetemp", ".cache",
}


def _candidatos_perfil() -> dict[str, list[Path]]:
    """Rutas donde suelen vivir las configuraciones, incluida la redirección a OneDrive."""
    inicio = Path.home()
    appdata = Path(os.environ.get("APPDATA", inicio / "AppData/Roaming"))
    localappdata = Path(os.environ.get("LOCALAPPDATA", inicio / "AppData/Local"))
    onedrive = os.environ.get("OneDrive") or os.environ.get("OneDriveCommercial")

    documentos = [inicio / "Documents", inicio / "Documentos"]
    if onedrive:
        documentos += [Path(onedrive) / "Documents", Path(onedrive) / "Documentos"]

    perfiles_powershell: list[Path] = []
    for doc in documentos:
        perfiles_powershell += [
            doc / "WindowsPowerShell/Microsoft.PowerShell_profile.ps1",
            doc / "PowerShell/Microsoft.PowerShell_profile.ps1",
            doc / "WindowsPowerShell/profile.ps1",
        ]

    return {
        "git": [inicio / ".gitconfig", inicio / ".gitignore_global", inicio / ".git-credentials"],
        "powershell": perfiles_powershell,
        "ssh": [inicio / ".ssh/config", inicio / ".ssh/known_hosts"],
        "python": [inicio / ".condarc", appdata / "pip/pip.ini", inicio / ".pip/pip.conf"],
        "node": [inicio / ".npmrc", inicio / ".yarnrc"],
        "vscode": [
            appdata / "Code/User/settings.json",
            appdata / "Code/User/keybindings.json",
            appdata / "Code/User/snippets",
        ],
        "terminal": [
            localappdata / "Packages/Microsoft.WindowsTerminal_8wekyb3d8bbwe/LocalState/settings.json"
        ],
        "nube": [inicio / ".aws/config", inicio / ".azure/config", inicio / ".docker/config.json"],
        "otros": [inicio / ".wslconfig", inicio / ".editorconfig"],
    }


def _candidatos_secretos() -> list[Path]:
    """Archivos de configuración que contienen credenciales."""
    inicio = Path.home()
    return [
        inicio / ".aws/credentials",
        inicio / ".ssh/id_rsa",
        inicio / ".ssh/id_ed25519",
        inicio / ".ssh/id_ecdsa",
        inicio / ".git-credentials",
    ]


def descubrir_configuraciones(incluir_secretos: bool) -> list[dict]:
    """Arma la lista de archivos de configuración presentes en este equipo."""
    encontrados: list[dict] = []
    for categoria, rutas in _candidatos_perfil().items():
        for ruta in rutas:
            if not ruta.exists():
                continue
            if ruta in _candidatos_secretos() and not incluir_secretos:
                continue
            if ruta.is_dir():
                medida = medir_arbol(ruta)
                encontrados.append(
                    {
                        "categoria": categoria,
                        "origen": str(ruta),
                        "tipo": "carpeta",
                        "archivos": medida["archivos"],
                        "bytes": medida["bytes"],
                    }
                )
            else:
                try:
                    tamano = ruta.stat().st_size
                except OSError:
                    continue
                encontrados.append(
                    {
                        "categoria": categoria,
                        "origen": str(ruta),
                        "tipo": "archivo",
                        "archivos": 1,
                        "bytes": tamano,
                        "sensible": ruta in _candidatos_secretos(),
                    }
                )

    if incluir_secretos:
        for ruta in _candidatos_secretos():
            if ruta.exists() and not any(c["origen"] == str(ruta) for c in encontrados):
                encontrados.append(
                    {
                        "categoria": "credenciales",
                        "origen": str(ruta),
                        "tipo": "archivo",
                        "archivos": 1,
                        "bytes": ruta.stat().st_size,
                        "sensible": True,
                    }
                )
    return encontrados


# --------------------------------------------------------------------------- #
# Configuración de Claude
# --------------------------------------------------------------------------- #

def inspeccionar_claude() -> dict:
    """Resume qué hay en la instalación de Claude Code de este equipo."""
    inicio = Path.home()
    carpeta = inicio / ".claude"
    archivo_config = inicio / ".claude.json"

    detalle: dict = {
        "carpeta": str(carpeta) if carpeta.exists() else None,
        "archivo_config": str(archivo_config) if archivo_config.exists() else None,
        "comandos": [],
        "agentes": [],
        "skills": [],
        "plugins": [],
        "hooks": [],
        "memoria": None,
        "servidores_mcp": [],
    }
    if not carpeta.exists():
        return detalle

    for clave, subcarpeta, patron in (
        ("comandos", "commands", "*.md"),
        ("agentes", "agents", "*.md"),
        ("skills", "skills", "*"),
    ):
        ruta = carpeta / subcarpeta
        if not ruta.is_dir():
            continue
        if clave == "skills":
            detalle[clave] = sorted(p.name for p in ruta.iterdir() if p.is_dir())
        else:
            detalle[clave] = sorted(p.stem for p in ruta.glob(patron))

    carpeta_plugins = carpeta / "plugins"
    if carpeta_plugins.is_dir():
        detalle["plugins"] = sorted(
            p.name for p in carpeta_plugins.iterdir() if p.is_dir() and p.name != "repos"
        )

    memoria = carpeta / "CLAUDE.md"
    if memoria.exists():
        detalle["memoria"] = str(memoria)

    ajustes = carpeta / "settings.json"
    if ajustes.exists():
        try:
            datos = json.loads(ajustes.read_text(encoding="utf-8-sig"))
            hooks = datos.get("hooks", {})
            detalle["hooks"] = sorted(hooks.keys()) if isinstance(hooks, dict) else []
        except (json.JSONDecodeError, OSError):
            detalle["hooks"] = ["(settings.json ilegible)"]

    if archivo_config.exists():
        try:
            datos = json.loads(archivo_config.read_text(encoding="utf-8-sig"))
            servidores = datos.get("mcpServers", {})
            if isinstance(servidores, dict):
                detalle["servidores_mcp"] = sorted(servidores.keys())
            proyectos = datos.get("projects", {})
            if isinstance(proyectos, dict):
                detalle["proyectos_conocidos"] = sorted(proyectos.keys())
        except (json.JSONDecodeError, OSError):
            pass
    return detalle


def limpiar_config_claude(origen: Path, destino: Path) -> dict:
    """Copia ~/.claude.json quitando el historial de conversaciones.

    El archivo original puede pesar decenas de MB por el historial, que no sirve
    en el equipo nuevo; los servidores MCP y las preferencias sí.
    """
    resumen = {"limpiado": False, "bytes_originales": 0, "bytes_finales": 0}
    try:
        crudo = origen.read_text(encoding="utf-8-sig")
        resumen["bytes_originales"] = len(crudo.encode("utf-8"))
        datos = json.loads(crudo)
    except (OSError, json.JSONDecodeError):
        try:
            destino.parent.mkdir(parents=True, exist_ok=True)
            destino.write_bytes(origen.read_bytes())
        except OSError:
            pass
        return resumen

    proyectos = datos.get("projects")
    if isinstance(proyectos, dict):
        for ruta_proyecto, contenido in proyectos.items():
            if isinstance(contenido, dict):
                contenido.pop("history", None)
                contenido.pop("pastedContents", None)
        resumen["limpiado"] = True

    destino.parent.mkdir(parents=True, exist_ok=True)
    texto = json.dumps(datos, indent=2, ensure_ascii=False)
    destino.write_text(texto, encoding="utf-8")
    resumen["bytes_finales"] = len(texto.encode("utf-8"))
    return resumen


# --------------------------------------------------------------------------- #
# Descubrimiento de carpetas de trabajo
# --------------------------------------------------------------------------- #

def _es_carpeta_de_trabajo(carpeta: Path) -> tuple[bool, list[str]]:
    razones: list[str] = []
    try:
        entradas = list(carpeta.iterdir())
    except OSError:
        return False, razones

    nombres = {e.name.lower() for e in entradas}
    for marcador in MARCADORES_PROYECTO:
        if marcador in nombres:
            razones.append(marcador)
    scripts = [e for e in entradas if e.is_file() and e.suffix.lower() in EXTENSIONES_PROYECTO]
    if scripts:
        razones.append(f"{len(scripts)} script(s)")
    return bool(razones), razones


def descubrir_carpetas_de_trabajo(
    consola: Consola,
    raices_extra: list[str] | None = None,
    profundidad_maxima: int = 3,
) -> list[dict]:
    """Busca en el perfil del usuario carpetas que parezcan trabajo propio.

    Se detiene al encontrar una carpeta de trabajo: no baja dentro de ella, para
    que el proyecto viaje completo y no fragmentado.
    """
    inicio = Path.home()
    raices: list[Path] = [inicio]
    onedrive = os.environ.get("OneDrive") or os.environ.get("OneDriveCommercial")
    if onedrive and Path(onedrive).exists():
        raices.append(Path(onedrive))
    for extra in raices_extra or []:
        ruta = Path(os.path.expandvars(extra)).expanduser()
        if ruta.exists():
            raices.append(ruta)

    encontradas: list[dict] = []
    ya_vistas: set[str] = set()

    def explorar(carpeta: Path, nivel: int) -> None:
        if nivel > profundidad_maxima:
            return
        try:
            subcarpetas = [e for e in carpeta.iterdir() if e.is_dir() and not e.is_symlink()]
        except OSError:
            return
        for sub in subcarpetas:
            nombre = sub.name.lower()
            if nombre in CARPETAS_PERFIL_IGNORADAS or nombre in CARPETAS_EXCLUIDAS:
                continue
            if nombre.startswith("$") or str(sub) in ya_vistas:
                continue
            # Las carpetas ocultas del perfil (.claude, .ssh, .git...) ya las
            # maneja la sección de configuraciones; incluirlas aquí las
            # duplicaría y arrastraría el historial que se excluye a propósito.
            # El .claude de un proyecto sí viaja: va dentro de su proyecto.
            if nombre.startswith("."):
                continue
            es_trabajo, razones = _es_carpeta_de_trabajo(sub)
            if es_trabajo:
                ya_vistas.add(str(sub))
                medida = medir_arbol(sub)
                encontradas.append(
                    {
                        "origen": str(sub),
                        "nombre": sub.name,
                        "motivos": razones,
                        "archivos": medida["archivos"],
                        "bytes": medida["bytes"],
                        "en_onedrive": bool(onedrive) and str(sub).startswith(str(onedrive)),
                    }
                )
                continue  # el proyecto viaja entero; no hace falta seguir bajando
            explorar(sub, nivel + 1)

    for raiz in raices:
        consola.paso(f"buscando carpetas de trabajo en {raiz}")
        explorar(raiz, 1)

    return sorted(encontradas, key=lambda c: c["bytes"], reverse=True)
