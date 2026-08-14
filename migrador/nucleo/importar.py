"""Aplicación del paquete en el computador nuevo."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from . import windows
from .util import (
    Consola,
    copiar_arbol,
    desde_plantilla,
    ejecutar,
    es_administrador,
    es_variable_de_ruta,
    es_windows,
    formatear_tamano,
    leer_json,
    marca_tiempo,
)

SECCIONES = ("variables", "configuraciones", "claude", "tareas", "inicio", "archivos")


class Aplicador:
    """Encapsula el modo simulación y el respaldo de lo que se sobrescribe."""

    def __init__(self, consola: Consola, simular: bool) -> None:
        self.consola = consola
        self.simular = simular
        self.carpeta_respaldo = Path.home() / f"migracion-respaldo-{marca_tiempo()}"
        self.cambios: list[dict] = []
        self.respaldos = 0
        self.pendientes: list[str] = []

    def registrar(self, tipo: str, detalle: str) -> None:
        self.cambios.append({"tipo": tipo, "detalle": detalle})

    def respaldar(self, ruta: Path) -> None:
        """Guarda una copia de lo que está por sobrescribirse."""
        if not ruta.exists() or self.simular:
            return
        try:
            relativa = ruta.relative_to(Path.home())
        except ValueError:
            relativa = Path(ruta.name)
        destino = self.carpeta_respaldo / relativa
        destino.parent.mkdir(parents=True, exist_ok=True)
        try:
            if ruta.is_dir():
                shutil.copytree(ruta, destino, dirs_exist_ok=True)
            else:
                shutil.copy2(ruta, destino)
            self.respaldos += 1
        except OSError as exc:
            self.consola.aviso(f"no se pudo respaldar {ruta}: {exc}")


# --------------------------------------------------------------------------- #
# Secciones
# --------------------------------------------------------------------------- #

def _importar_variables(app: Aplicador, paquete: Path, con_sistema: bool) -> None:
    app.consola.titulo("Variables de entorno")

    for ambito, archivo in (("usuario", "usuario.json"), ("sistema", "sistema.json")):
        ruta = paquete / "variables" / archivo
        if not ruta.exists():
            continue
        if ambito == "sistema":
            if not con_sistema:
                app.consola.paso("variables de sistema omitidas (usa --con-sistema)")
                continue
            if not es_administrador():
                app.consola.aviso(
                    "las variables de sistema requieren PowerShell como administrador; se omiten"
                )
                continue

        variables = leer_json(ruta)
        actuales = windows.leer_variables(ambito)
        for nombre, datos in variables.items():
            valor_nuevo = datos["valor"]
            tipo = datos.get("tipo", 1)
            valor_actual = actuales.get(nombre, {}).get("valor")

            if es_variable_de_ruta(nombre):
                # Las rutas se fusionan: lo del equipo nuevo manda y se agrega
                # solo lo que falta, para no perder lo que ya instaló Windows.
                existentes = [p for p in (valor_actual or "").split(";") if p]
                agregar = [p for p in valor_nuevo.split(";") if p and p not in existentes]
                if not agregar:
                    continue
                for entrada in agregar:
                    expandida = Path(os.path.expandvars(entrada))
                    if not expandida.exists():
                        app.consola.aviso(f"{nombre}: '{entrada}' no existe en este equipo")
                valor_final = ";".join(existentes + agregar)
                app.consola.paso(f"{nombre}: +{len(agregar)} ruta(s)")
            else:
                if valor_actual == valor_nuevo:
                    continue
                valor_final = valor_nuevo
                if valor_actual is None:
                    app.consola.paso(f"{nombre}: nueva")
                else:
                    app.consola.paso(f"{nombre}: se reemplaza")

            app.registrar("variable", f"{ambito}/{nombre}")
            if not app.simular:
                ok, error = windows.escribir_variable(ambito, nombre, valor_final, tipo)
                if not ok:
                    app.consola.error(f"{nombre}: {error}")

    if not app.simular:
        windows.avisar_cambio_de_entorno()


def _restaurar_ruta(app: Aplicador, paquete: Path, entrada: dict, etiqueta: str) -> None:
    """Copia un elemento del paquete a su ubicación en este equipo."""
    origen = paquete / entrada["ruta_en_paquete"]
    if not origen.exists():
        return
    destino = desde_plantilla(entrada["destino_plantilla"])

    if entrada.get("requiere_carpeta_previa") and not destino.parent.is_dir():
        # Caso típico: tnsnames.ora cuando el cliente Oracle todavía no está
        # instalado. Crear la carpeta dejaría el archivo huérfano y el cliente
        # lo sobrescribiría al instalarse.
        app.consola.aviso(
            f"{etiqueta}: no existe {destino.parent}, se omite {destino.name}"
        )
        app.consola.info("       instala primero el programa y vuelve a correr la importación")
        app.pendientes.append(str(destino))
        return

    app.consola.paso(f"{etiqueta}: {destino}")
    app.registrar(etiqueta, str(destino))
    if app.simular:
        return

    app.respaldar(destino)
    try:
        if origen.is_dir():
            # Sin exclusiones: lo que está en el paquete ya fue filtrado al
            # exportar. Y sin omitir lo que esté solo en la nube: si el paquete
            # viajó por OneDrive, sus archivos pueden ser marcadores sin
            # descargar, y saltárselos restauraría carpetas vacías en silencio.
            copiar_arbol(
                origen, destino,
                excluir_carpetas=set(), excluir_extensiones=set(),
                limite_archivo_bytes=1024**3,
                omitir_en_la_nube=False,
            )
        else:
            destino.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(origen, destino)
    except OSError as exc:
        app.consola.error(f"no se pudo restaurar {destino}: {exc}")


def _importar_configuraciones(app: Aplicador, paquete: Path, manifiesto: dict) -> None:
    app.consola.titulo("Configuraciones")
    for entrada in manifiesto.get("configuraciones", []):
        _restaurar_ruta(app, paquete, entrada, entrada["categoria"])


def _importar_claude(app: Aplicador, paquete: Path, manifiesto: dict) -> None:
    app.consola.titulo("Claude Code")
    archivos = manifiesto.get("claude_archivos") or {}
    for clave in ("carpeta", "config"):
        entrada = archivos.get(clave)
        if entrada:
            _restaurar_ruta(app, paquete, entrada, "claude")
    if not archivos:
        app.consola.paso("el paquete no trae configuración de Claude")


def _importar_tareas(app: Aplicador, paquete: Path, manifiesto: dict) -> None:
    app.consola.titulo("Tareas programadas")
    tareas = manifiesto.get("tareas", [])
    if not tareas:
        app.consola.paso("el paquete no trae tareas")
        return
    if not es_windows():
        app.consola.aviso("no es Windows: no se pueden crear tareas")
        return

    carpeta = paquete / "automatizaciones/tareas-programadas"
    existentes = {t["nombre"].lower() for t in windows.listar_tareas(app.consola)}

    for tarea in tareas:
        archivo = tarea.get("archivo_xml")
        if not archivo:
            app.consola.aviso(f"{tarea['nombre']}: sin XML, hay que recrearla a mano")
            continue
        if tarea["nombre"].lower() in existentes:
            app.consola.paso(f"{tarea['nombre']}: ya existe, se omite")
            continue

        ruta_xml = carpeta / archivo
        if not ruta_xml.exists():
            continue
        app.consola.paso(f"{tarea['nombre']}: crear")
        app.registrar("tarea", tarea["nombre"])
        if app.simular:
            continue
        codigo, _, error = ejecutar(
            ["schtasks", "/create", "/tn", tarea["nombre"], "/xml", str(ruta_xml), "/f"]
        )
        if codigo != 0:
            app.consola.error(f"{tarea['nombre']}: {error.strip() or 'falló schtasks'}")
            app.consola.info("       revisa la ruta del programa y la cuenta de ejecución")


def _importar_inicio(app: Aplicador, paquete: Path, manifiesto: dict) -> None:
    app.consola.titulo("Carpeta de Inicio")
    entrada = manifiesto.get("inicio")
    if not entrada:
        app.consola.paso("el paquete no trae elementos de inicio")
        return
    _restaurar_ruta(app, paquete, entrada, "inicio")


def _importar_archivos(app: Aplicador, paquete: Path, manifiesto: dict) -> None:
    app.consola.titulo("Carpetas de trabajo")
    carpetas = manifiesto.get("archivos", [])
    if not carpetas:
        app.consola.paso("el paquete no trae carpetas de trabajo")
        return
    for carpeta in carpetas:
        destino = desde_plantilla(carpeta["destino_plantilla"])
        estado = "se fusiona con lo existente" if destino.exists() else "nueva"
        app.consola.paso(
            f"{Path(carpeta['origen']).name} -> {destino} "
            f"({carpeta.get('archivos', 0)} archivos, "
            f"{formatear_tamano(carpeta.get('bytes', 0))}, {estado})"
        )
        _restaurar_ruta(app, paquete, carpeta, "archivos")


# --------------------------------------------------------------------------- #
# Entrada principal
# --------------------------------------------------------------------------- #

def aplicar_paquete(
    consola: Consola,
    ruta_paquete: Path,
    secciones: list[str] | None = None,
    simular: bool = False,
    con_sistema: bool = False,
) -> dict:
    """Aplica el paquete a este equipo. Con `simular` no escribe nada."""
    manifiesto = leer_json(ruta_paquete / "manifiesto.json")
    secciones = secciones or list(SECCIONES)
    app = Aplicador(consola, simular)

    if simular:
        consola.info("")
        consola.info("  SIMULACIÓN: no se va a escribir nada.")
    else:
        consola.info("")
        consola.info(f"  Respaldo de lo sobrescrito: {app.carpeta_respaldo}")

    origen = manifiesto["equipo"]
    consola.info(f"  Paquete de {origen['host']} ({origen['usuario']}), {manifiesto['generado']}")

    if "variables" in secciones:
        _importar_variables(app, ruta_paquete, con_sistema)
    if "configuraciones" in secciones:
        _importar_configuraciones(app, ruta_paquete, manifiesto)
    if "claude" in secciones:
        _importar_claude(app, ruta_paquete, manifiesto)
    if "tareas" in secciones:
        _importar_tareas(app, ruta_paquete, manifiesto)
    if "inicio" in secciones:
        _importar_inicio(app, ruta_paquete, manifiesto)
    if "archivos" in secciones:
        _importar_archivos(app, ruta_paquete, manifiesto)

    consola.titulo("Resumen")
    if simular:
        consola.info(f"  Se aplicarían {len(app.cambios)} cambio(s).")
        consola.info("  Vuelve a ejecutar sin --simular para hacerlo de verdad.")
    else:
        consola.info(f"  {len(app.cambios)} cambio(s) aplicados.")
        if app.respaldos:
            consola.info(f"  {app.respaldos} elemento(s) respaldados en {app.carpeta_respaldo}")
    if app.pendientes:
        consola.info("")
        consola.info(f"  {len(app.pendientes)} archivo(s) quedaron pendientes porque falta")
        consola.info("  instalar el programa al que pertenecen:")
        for ruta in app.pendientes:
            consola.info(f"    {ruta}")
        consola.info("  Instálalos y vuelve a ejecutar la importación.")
    if not simular:
        consola.info("  Abre una consola nueva para que las variables tomen efecto.")
        consola.info("  Los programas se instalan aparte:")
        consola.info(f"    powershell -ExecutionPolicy Bypass -File "
                     f"{ruta_paquete / 'programas' / 'instalar-programas.ps1'}")

    return {
        "cambios": app.cambios,
        "simulado": simular,
        "respaldo": str(app.carpeta_respaldo) if app.respaldos else None,
    }
