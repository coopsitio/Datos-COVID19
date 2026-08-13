"""Diagnóstico: qué hay en este equipo y qué le falta respecto del paquete."""

from __future__ import annotations

from pathlib import Path

from . import recolectores, windows
from .util import (
    Consola,
    ahora_iso,
    datos_del_equipo,
    desde_plantilla,
    es_windows,
    formatear_tamano,
    leer_json,
    parece_secreto,
)


def escanear_equipo(consola: Consola, incluir_carpetas: bool = True) -> dict:
    """Inventario del equipo actual, sin copiar ni modificar nada."""
    consola.titulo("Equipo")
    equipo = datos_del_equipo()
    consola.paso(f"{equipo['host']} — {equipo['so']} {equipo['release_so']} — {equipo['usuario']}")
    consola.paso(f"Python {equipo['python']}")

    informe: dict = {"generado": ahora_iso(), "equipo": equipo}

    consola.titulo("Variables de entorno")
    usuario = windows.leer_variables("usuario")
    sistema = windows.leer_variables("sistema")
    informe["variables"] = {
        "usuario": sorted(usuario),
        "sistema": sorted(sistema),
        "sensibles": sorted(k for k in {**usuario, **sistema} if parece_secreto(k)),
    }
    consola.paso(f"{len(usuario)} de usuario, {len(sistema)} de sistema")
    if informe["variables"]["sensibles"]:
        consola.paso(f"{len(informe['variables']['sensibles'])} parecen credenciales")

    consola.titulo("Automatizaciones")
    tareas = windows.listar_tareas(consola) if es_windows() else []
    informe["tareas"] = tareas
    consola.paso(f"{len(tareas)} tarea(s) programada(s) propias")
    carpeta_inicio = windows.carpeta_inicio()
    elementos_inicio = (
        [p.name for p in carpeta_inicio.iterdir()] if carpeta_inicio.is_dir() else []
    )
    informe["inicio"] = elementos_inicio
    consola.paso(f"{len(elementos_inicio)} elemento(s) en la carpeta de Inicio")

    consola.titulo("Claude Code")
    claude = recolectores.inspeccionar_claude()
    informe["claude"] = claude
    if claude["carpeta"]:
        consola.paso(
            f"{len(claude['comandos'])} comandos, {len(claude['agentes'])} agentes, "
            f"{len(claude['skills'])} skills, {len(claude['plugins'])} plugins"
        )
        consola.paso(f"servidores MCP: {', '.join(claude['servidores_mcp']) or 'ninguno'}")
    else:
        consola.paso("no hay carpeta ~/.claude en este equipo")

    consola.titulo("Programas")
    programas = windows.programas_del_registro()
    informe["programas"] = [p["nombre"] for p in programas]
    consola.paso(f"{len(programas)} programa(s) instalados")

    consola.titulo("Configuraciones")
    configuraciones = recolectores.descubrir_configuraciones(incluir_secretos=False)
    informe["configuraciones"] = [c["origen"] for c in configuraciones]
    for entrada in configuraciones:
        consola.paso(f"{entrada['categoria']}: {Path(entrada['origen']).name}")

    if incluir_carpetas:
        consola.titulo("Carpetas de trabajo")
        carpetas = recolectores.descubrir_carpetas_de_trabajo(consola)
        informe["carpetas"] = carpetas
        for carpeta in carpetas[:20]:
            consola.paso(
                f"{carpeta['origen']} — {carpeta['archivos']} archivos, "
                f"{formatear_tamano(carpeta['bytes'])} ({', '.join(carpeta['motivos'][:3])})"
            )
        if len(carpetas) > 20:
            consola.paso(f"... y {len(carpetas) - 20} más")
        total = sum(c["bytes"] for c in carpetas)
        consola.info("")
        consola.info(f"  Total a migrar: {formatear_tamano(total)} en {len(carpetas)} carpeta(s)")

    return informe


def _faltantes(esperados, presentes) -> list:
    conjunto = {str(p).lower() for p in presentes}
    return [e for e in esperados if str(e).lower() not in conjunto]


def estado_frente_a_paquete(consola: Consola, ruta_paquete: Path) -> dict:
    """Compara el paquete con este equipo y muestra qué falta por migrar."""
    manifiesto = leer_json(ruta_paquete / "manifiesto.json")
    origen = manifiesto["equipo"]
    actual = datos_del_equipo()

    consola.titulo("Comparación")
    consola.paso(f"Paquete de: {origen['host']} ({origen['usuario']}), {manifiesto['generado']}")
    consola.paso(f"Este equipo: {actual['host']} ({actual['usuario']})")

    diferencias: dict = {"equipo_origen": origen, "equipo_actual": actual}

    # ---- Variables --------------------------------------------------------- #
    consola.titulo("Variables de entorno")
    variables_paquete = leer_json(ruta_paquete / "variables/usuario.json")
    variables_actuales = windows.leer_variables("usuario")

    faltan, difieren, iguales = [], [], []
    for nombre, datos in variables_paquete.items():
        if nombre not in variables_actuales:
            faltan.append(nombre)
        elif variables_actuales[nombre]["valor"] != datos["valor"]:
            difieren.append(nombre)
        else:
            iguales.append(nombre)

    diferencias["variables"] = {"faltan": faltan, "difieren": difieren, "iguales": iguales}
    consola.paso(f"{len(faltan)} faltan, {len(difieren)} con distinto valor, {len(iguales)} iguales")
    for nombre in faltan[:15]:
        marca = " (credencial)" if parece_secreto(nombre) else ""
        consola.info(f"     falta: {nombre}{marca}")
    if len(faltan) > 15:
        consola.info(f"     ... y {len(faltan) - 15} más")
    for nombre in difieren[:10]:
        consola.info(f"     distinta: {nombre}")

    # ---- Tareas ------------------------------------------------------------ #
    consola.titulo("Tareas programadas")
    tareas_paquete = [t["nombre"] for t in manifiesto.get("tareas", [])]
    tareas_actuales = [t["nombre"] for t in (windows.listar_tareas(consola) if es_windows() else [])]
    tareas_faltantes = _faltantes(tareas_paquete, tareas_actuales)
    diferencias["tareas_faltantes"] = tareas_faltantes
    consola.paso(f"{len(tareas_faltantes)} de {len(tareas_paquete)} por crear")
    for nombre in tareas_faltantes[:15]:
        consola.info(f"     falta: {nombre}")

    # ---- Claude ------------------------------------------------------------ #
    consola.titulo("Claude Code")
    claude_paquete = manifiesto.get("claude", {})
    claude_actual = recolectores.inspeccionar_claude()
    detalle_claude = {}
    for clave, etiqueta in (
        ("comandos", "comandos"),
        ("agentes", "agentes"),
        ("skills", "skills"),
        ("servidores_mcp", "servidores MCP"),
        ("plugins", "plugins"),
    ):
        faltantes = _faltantes(claude_paquete.get(clave, []), claude_actual.get(clave, []))
        detalle_claude[clave] = faltantes
        total = len(claude_paquete.get(clave, []))
        if total:
            consola.paso(f"{etiqueta}: faltan {len(faltantes)} de {total}")
            for nombre in faltantes[:10]:
                consola.info(f"     falta: {nombre}")
    diferencias["claude_faltante"] = detalle_claude

    # ---- Programas --------------------------------------------------------- #
    consola.titulo("Programas")
    archivo_programas = ruta_paquete / "programas/programas-instalados.json"
    if archivo_programas.exists():
        del_paquete = [p["nombre"] for p in leer_json(archivo_programas)]
        instalados = [p["nombre"] for p in windows.programas_del_registro()]
        faltantes = _faltantes(del_paquete, instalados)
        diferencias["programas_faltantes"] = faltantes
        consola.paso(f"{len(faltantes)} de {len(del_paquete)} sin instalar")
        for nombre in faltantes[:25]:
            consola.info(f"     falta: {nombre}")
        if len(faltantes) > 25:
            consola.info(f"     ... y {len(faltantes) - 25} más")
    else:
        diferencias["programas_faltantes"] = []
        consola.paso("el paquete no trae inventario de programas")

    # ---- Archivos ---------------------------------------------------------- #
    consola.titulo("Carpetas de trabajo")
    pendientes = []
    for carpeta in manifiesto.get("archivos", []):
        destino = desde_plantilla(carpeta["destino_plantilla"])
        existe = destino.exists()
        pendientes.append(
            {
                "nombre": Path(carpeta["origen"]).name,
                "destino": str(destino),
                "existe": existe,
                "archivos": carpeta.get("archivos", 0),
                "bytes": carpeta.get("bytes", 0),
            }
        )
        marca = "ya existe" if existe else "por copiar"
        consola.paso(
            f"{Path(carpeta['origen']).name} -> {destino} "
            f"({carpeta.get('archivos', 0)} archivos, {marca})"
        )
    diferencias["carpetas"] = pendientes

    # ---- Resumen ----------------------------------------------------------- #
    consola.titulo("Resumen")
    pendiente_total = (
        len(faltan) + len(difieren) + len(tareas_faltantes)
        + sum(len(v) for v in detalle_claude.values())
        + len([c for c in pendientes if not c["existe"]])
    )
    if pendiente_total == 0:
        consola.info("  Este equipo ya tiene todo lo del paquete.")
    else:
        consola.info(f"  Quedan {pendiente_total} elemento(s) por migrar.")
        consola.info("  Para aplicarlos: migrar.py importar --paquete <ruta>")
    diferencias["pendientes"] = pendiente_total
    return diferencias


def comparar_informes(consola: Consola, informe_a: Path, informe_b: Path) -> dict:
    """Compara dos informes de `escanear` (por ejemplo antiguo vs nuevo)."""
    a, b = leer_json(informe_a), leer_json(informe_b)
    consola.titulo("Comparación de informes")
    consola.paso(f"A: {a['equipo']['host']} ({a['generado']})")
    consola.paso(f"B: {b['equipo']['host']} ({b['generado']})")

    resultado: dict = {}
    for clave, etiqueta, extractor in (
        ("variables", "Variables de usuario", lambda d: d["variables"]["usuario"]),
        ("programas", "Programas", lambda d: d["programas"]),
        ("tareas", "Tareas programadas", lambda d: [t["nombre"] for t in d["tareas"]]),
    ):
        try:
            en_a, en_b = extractor(a), extractor(b)
        except (KeyError, TypeError):
            continue
        solo_a = _faltantes(en_a, en_b)
        solo_b = _faltantes(en_b, en_a)
        resultado[clave] = {"solo_en_a": solo_a, "solo_en_b": solo_b}
        consola.titulo(etiqueta)
        consola.paso(f"solo en A: {len(solo_a)} | solo en B: {len(solo_b)}")
        for nombre in solo_a[:20]:
            consola.info(f"     A: {nombre}")
    return resultado
