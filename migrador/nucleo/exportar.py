"""Creación del paquete de migración en el computador antiguo."""

from __future__ import annotations

import shutil
from pathlib import Path

from . import recolectores, scripts, windows
from .util import (
    CARPETAS_EXCLUIDAS,
    FORMATO_PAQUETE,
    VERSION,
    Consola,
    a_plantilla,
    ahora_iso,
    copiar_arbol,
    datos_del_equipo,
    escribir_json,
    escribir_texto,
    es_windows,
    formatear_tamano,
    marca_tiempo,
    parece_secreto,
    ruta_segura,
)


def _copiar_configuracion(
    entrada: dict, paquete: Path, consola: Consola, usadas: set[str] | None = None
) -> dict | None:
    """Copia un archivo o carpeta de configuración al paquete."""
    origen = Path(entrada["origen"])
    if not origen.exists():
        return None

    plantilla = a_plantilla(origen)
    # `ruta_en_paquete` siempre es relativa a la raíz del paquete: es lo que usa
    # la importación para encontrar el archivo.
    base = Path("configuraciones") / entrada["categoria"]
    relativa = base / ruta_segura(origen.name)

    # Un mismo nombre puede venir de varias rutas: es habitual tener dos
    # tnsnames.ora, uno por cliente Oracle instalado. Sin desambiguar, el
    # segundo pisaría al primero y una instalación recibiría el archivo
    # equivocado. Se antepone la carpeta de origen, que es lo que los distingue.
    if usadas is not None and relativa.as_posix() in usadas:
        relativa = base / ruta_segura(origen.parent.name) / ruta_segura(origen.name)
        contador = 2
        while relativa.as_posix() in usadas:
            relativa = base / f"{ruta_segura(origen.parent.name)}_{contador}" / ruta_segura(origen.name)
            contador += 1
    if usadas is not None:
        usadas.add(relativa.as_posix())

    destino = paquete / relativa

    try:
        if origen.is_dir():
            resumen = copiar_arbol(origen, destino)
            archivos, bytes_ = resumen["archivos"], resumen["bytes"]
        else:
            destino.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(origen, destino)
            archivos, bytes_ = 1, origen.stat().st_size
    except OSError as exc:
        consola.aviso(f"no se pudo copiar {origen}: {exc}")
        return None

    return {
        "categoria": entrada["categoria"],
        "origen": str(origen),
        "destino_plantilla": plantilla,
        "ruta_en_paquete": relativa.as_posix(),
        "tipo": "carpeta" if origen.is_dir() else "archivo",
        "archivos": archivos,
        "bytes": bytes_,
        "sensible": entrada.get("sensible", False),
        "requiere_carpeta_previa": entrada.get("requiere_carpeta_previa", False),
    }


def _exportar_claude(paquete: Path, consola: Consola) -> dict:
    """Copia la configuración de Claude Code sin el historial de conversaciones."""
    inicio = Path.home()
    carpeta = inicio / ".claude"
    archivo_config = inicio / ".claude.json"
    resultado: dict = {"carpeta": None, "config": None}
    base_relativa = Path("configuraciones/claude")

    if carpeta.is_dir():
        excluir = CARPETAS_EXCLUIDAS | recolectores.RUIDO_CLAUDE
        resumen = copiar_arbol(
            carpeta,
            paquete / base_relativa / ".claude",
            excluir_carpetas=excluir,
            # Los .md de skills y comandos no tienen extensión "de código":
            # aquí no se filtra por extensión, solo por carpeta.
            excluir_extensiones={".log", ".tmp", ".lock"},
        )
        # plugins/repos son clones de git que se rehacen solos al reinstalar.
        repos = paquete / base_relativa / ".claude/plugins/repos"
        if repos.exists():
            shutil.rmtree(repos, ignore_errors=True)
        resultado["carpeta"] = {
            "destino_plantilla": "{HOME}/.claude",
            "ruta_en_paquete": (base_relativa / ".claude").as_posix(),
            **resumen,
        }
        consola.paso(
            f".claude copiado ({resumen['archivos']} archivos, "
            f"{formatear_tamano(resumen['bytes'])})"
        )

    if archivo_config.exists():
        limpieza = recolectores.limpiar_config_claude(
            archivo_config, paquete / base_relativa / ".claude.json"
        )
        resultado["config"] = {
            "destino_plantilla": "{HOME}/.claude.json",
            "ruta_en_paquete": (base_relativa / ".claude.json").as_posix(),
            **limpieza,
        }
        if limpieza["limpiado"]:
            consola.paso(
                f".claude.json copiado sin historial "
                f"({formatear_tamano(limpieza['bytes_originales'])} -> "
                f"{formatear_tamano(limpieza['bytes_finales'])})"
            )
    return resultado


def crear_paquete(
    consola: Consola,
    destino: Path,
    incluir_secretos: bool = True,
    incluir_archivos: bool = True,
    incluir_onedrive: bool = False,
    carpetas_extra: list[str] | None = None,
    limite_gb: float = 5.0,
    ruta_herramienta: Path | None = None,
) -> Path:
    """Genera el paquete completo y devuelve su ruta."""
    equipo = datos_del_equipo()
    nombre_paquete = f"migracion-{ruta_segura(equipo['host'])}-{marca_tiempo()}"
    paquete = (destino / nombre_paquete).resolve()
    paquete.mkdir(parents=True, exist_ok=True)

    consola.info(f"Paquete: {paquete}")
    limite_bytes = int(limite_gb * 1024**3)

    manifiesto: dict = {
        "formato": FORMATO_PAQUETE,
        "version_herramienta": VERSION,
        "generado": ahora_iso(),
        "equipo": equipo,
        "opciones": {
            "incluir_secretos": incluir_secretos,
            "incluir_archivos": incluir_archivos,
            "incluir_onedrive": incluir_onedrive,
            "limite_gb": limite_gb,
        },
    }

    # ---- Variables de entorno -------------------------------------------- #
    consola.titulo("Variables de entorno")
    if es_windows():
        usuario = windows.leer_variables("usuario")
        sistema = windows.leer_variables("sistema")
    else:
        consola.aviso("no es Windows: se omiten las variables del registro")
        usuario, sistema = {}, {}

    if not incluir_secretos:
        usuario = {k: v for k, v in usuario.items() if not parece_secreto(k)}
        sistema = {k: v for k, v in sistema.items() if not parece_secreto(k)}

    carpeta_variables = paquete / "variables"
    escribir_json(carpeta_variables / "usuario.json", usuario)
    escribir_json(carpeta_variables / "sistema.json", sistema)
    escribir_texto(
        carpeta_variables / "restaurar-variables.ps1",
        scripts.script_variables(usuario, sistema),
    )
    sensibles = sorted(k for k in {**usuario, **sistema} if parece_secreto(k))
    manifiesto["variables"] = {
        "usuario": sorted(usuario),
        "sistema": sorted(sistema),
        "sensibles": sensibles,
    }
    consola.paso(f"{len(usuario)} de usuario, {len(sistema)} de sistema")
    if sensibles and incluir_secretos:
        consola.aviso(
            f"{len(sensibles)} variable(s) con credenciales van en texto plano: "
            + ", ".join(sensibles[:6])
            + ("..." if len(sensibles) > 6 else "")
        )

    # ---- Automatizaciones -------------------------------------------------- #
    consola.titulo("Automatizaciones")
    carpeta_auto = paquete / "automatizaciones"
    if es_windows():
        tareas = windows.guardar_tareas(carpeta_auto / "tareas-programadas", consola)
        consola.paso(f"{len(tareas)} tarea(s) programada(s)")
    else:
        tareas = []
    escribir_texto(
        carpeta_auto / "tareas-programadas/restaurar-tareas.ps1", scripts.script_tareas(tareas)
    )
    manifiesto["tareas"] = tareas

    inicio_windows = windows.carpeta_inicio()
    if inicio_windows.is_dir():
        resumen_inicio = copiar_arbol(inicio_windows, carpeta_auto / "inicio")
        manifiesto["inicio"] = {
            "destino_plantilla": a_plantilla(inicio_windows),
            "ruta_en_paquete": "automatizaciones/inicio",
            **resumen_inicio,
        }
        consola.paso(f"{resumen_inicio['archivos']} elemento(s) en la carpeta de Inicio")
    else:
        manifiesto["inicio"] = None

    # ---- Configuraciones y Claude ----------------------------------------- #
    consola.titulo("Configuraciones")
    encontradas = recolectores.descubrir_configuraciones(incluir_secretos)
    copiadas = []
    usadas: set[str] = set()
    for entrada in encontradas:
        resultado = _copiar_configuracion(entrada, paquete, consola, usadas)
        if resultado:
            copiadas.append(resultado)
            consola.paso(f"{entrada['categoria']}: {Path(entrada['origen']).name}")
    manifiesto["configuraciones"] = copiadas

    consola.titulo("Claude Code")
    manifiesto["claude_archivos"] = _exportar_claude(paquete, consola)
    manifiesto["claude"] = recolectores.inspeccionar_claude()
    resumen_claude = manifiesto["claude"]
    consola.paso(
        f"{len(resumen_claude['comandos'])} comandos, "
        f"{len(resumen_claude['agentes'])} agentes, "
        f"{len(resumen_claude['skills'])} skills, "
        f"{len(resumen_claude['servidores_mcp'])} servidores MCP"
    )

    # ---- Programas --------------------------------------------------------- #
    consola.titulo("Programas instalados")
    carpeta_programas = paquete / "programas"
    if es_windows():
        inventario = windows.inventario_gestores(carpeta_programas, consola)
    else:
        consola.aviso("no es Windows: se omite el inventario de programas")
        inventario = {}
    escribir_texto(
        carpeta_programas / "instalar-programas.ps1", scripts.script_programas(inventario)
    )
    manifiesto["programas"] = inventario

    # ---- Carpetas de trabajo ---------------------------------------------- #
    consola.titulo("Carpetas de trabajo")
    # Se le pasa la ruta del paquete: si se exporta a una carpeta del perfil,
    # el descubrimiento encuentra el paquete recién creado y lo trataría como
    # trabajo del usuario, copiándolo dentro de sí mismo.
    carpetas = recolectores.descubrir_carpetas_de_trabajo(
        consola, carpetas_extra, excluir=paquete
    )
    manifiesto["carpetas_detectadas"] = carpetas

    copiadas_archivos = []
    ya_sincronizadas = []
    if incluir_archivos and carpetas:
        usado = 0
        for carpeta in carpetas:
            origen = Path(carpeta["origen"])

            if carpeta.get("en_onedrive") and not incluir_onedrive:
                # Lo que ya está en OneDrive llega solo al equipo nuevo al
                # iniciar sesión. Copiarlo al paquete lo duplicaría y obligaría
                # a descargar de la nube lo que esté solo allá.
                ya_sincronizadas.append(carpeta)
                consola.paso(f"{origen.name}: ya está en OneDrive, se sincroniza sola")
                continue

            restante = limite_bytes - usado
            if restante <= 0:
                consola.aviso(f"se alcanzó el límite de {limite_gb} GB; falta {origen}")
                break
            relativa = Path("archivos") / ruta_segura(origen.name)
            resumen = copiar_arbol(origen, paquete / relativa, limite_bytes=restante)
            usado += resumen["bytes"]
            copiadas_archivos.append(
                {
                    "origen": str(origen),
                    "destino_plantilla": a_plantilla(origen),
                    "ruta_en_paquete": relativa.as_posix(),
                    **resumen,
                }
            )
            estado = " (truncada)" if resumen["truncado"] else ""
            consola.paso(
                f"{origen.name}: {resumen['archivos']} archivos, "
                f"{formatear_tamano(resumen['bytes'])}{estado}"
            )
    elif not incluir_archivos:
        consola.paso(f"{len(carpetas)} carpeta(s) detectadas, no copiadas (--sin-archivos)")
    manifiesto["archivos"] = copiadas_archivos
    manifiesto["ya_en_onedrive"] = ya_sincronizadas
    if ya_sincronizadas:
        consola.info("")
        consola.info(f"  {len(ya_sincronizadas)} carpeta(s) quedan fuera del paquete por estar")
        consola.info("  en OneDrive. Llegarán solas al iniciar sesión en el equipo nuevo.")
        consola.info("  Si quieres copiarlas igual, usa --incluir-onedrive.")

    # ---- La herramienta y los textos de apoyo ------------------------------ #
    if ruta_herramienta and ruta_herramienta.exists():
        copiar_arbol(
            ruta_herramienta,
            paquete / "herramienta",
            excluir_extensiones={".pyc", ".log"},
        )
    escribir_texto(paquete / "INSTALAR.ps1", scripts.script_instalador())
    escribir_json(paquete / "manifiesto.json", manifiesto)
    escribir_texto(paquete / "LEEME.md", _texto_leeme(manifiesto, paquete))
    return paquete


def _texto_leeme(manifiesto: dict, paquete: Path) -> str:
    equipo = manifiesto["equipo"]
    variables = manifiesto["variables"]
    sensibles = variables["sensibles"]
    total_archivos = sum(c.get("archivos", 0) for c in manifiesto.get("archivos", []))
    total_bytes = sum(c.get("bytes", 0) for c in manifiesto.get("archivos", []))

    aviso_secretos = ""
    if sensibles and manifiesto["opciones"]["incluir_secretos"]:
        lista = "\n".join(f"- `{nombre}`" for nombre in sensibles)
        aviso_secretos = f"""
## Ojo con esto

Este paquete contiene **credenciales en texto plano** en
`variables/usuario.json` y `variables/sistema.json`:

{lista}

Cualquiera que abra la carpeta puede leerlas. Bórrala de OneDrive apenas
termines la migración, y no la compartas por chat ni por correo.
"""

    return f"""# Paquete de migración

Generado el {manifiesto["generado"]} desde **{equipo["host"]}**
(usuario `{equipo["usuario"]}`, {equipo["so"]} {equipo["release_so"]}).

## Qué trae

| Contenido | Cantidad |
|---|---|
| Variables de entorno de usuario | {len(variables["usuario"])} |
| Variables de entorno de sistema | {len(variables["sistema"])} |
| Tareas programadas | {len(manifiesto.get("tareas", []))} |
| Archivos de configuración | {len(manifiesto.get("configuraciones", []))} |
| Carpetas de trabajo copiadas | {len(manifiesto.get("archivos", []))} ({total_archivos} archivos) |

Tamaño de las carpetas de trabajo: {formatear_tamano(total_bytes)}.
{aviso_secretos}
## Cómo usarlo en el computador nuevo

1. Copia esta carpeta completa (`{paquete.name}`) al computador nuevo,
   por OneDrive, pendrive o disco externo. Espera a que OneDrive termine de
   sincronizar: los archivos deben verse con el check verde, no con la nube.
2. Abre PowerShell en la carpeta y ejecuta:

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\\INSTALAR.ps1
   ```

   El script instala Python si falta, te muestra el estado del equipo nuevo,
   hace una simulación y recién después pregunta si aplicar.

3. Los programas se instalan aparte, porque toma más rato:

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\\programas\\instalar-programas.ps1
   ```

## Si prefieres ir por partes

Cada subcarpeta se puede restaurar sola:

- `variables\\restaurar-variables.ps1` — variables de entorno
  (agrega `-IncluirSistema` en una consola de administrador para las de sistema,
  y `-SoloMostrar` para simular).
- `automatizaciones\\tareas-programadas\\restaurar-tareas.ps1` — tareas programadas.
- `programas\\instalar-programas.ps1` — programas.
- `configuraciones\\` y `archivos\\` — se copian con la herramienta:
  `python herramienta\\migrar.py importar --paquete . --solo configuraciones`

## Qué NO trae

- El historial de conversaciones de Claude (no sirve en el equipo nuevo).
- Archivos de más de 50 MB y carpetas como `node_modules`, `.venv`, `__pycache__`.
- Programas propiamente tales: viaja la *lista* para reinstalarlos, no los binarios.
- Licencias, sesiones abiertas ni credenciales guardadas dentro de cada programa.
"""
