#!/usr/bin/env python3
"""Migrador de computador de trabajo (Windows -> Windows).

Rescata variables de entorno, configuraciones, automatizaciones de Claude Code
y la lista de programas de un equipo, para dejarlos igual en otro.

Uso típico:

    # En el computador ANTIGUO
    python migrar.py escanear
    python migrar.py exportar --destino "%OneDrive%\\Migracion"

    # En el computador NUEVO (o directamente INSTALAR.ps1 del paquete)
    python migrar.py estado    --paquete "...\\migracion-EQUIPO-2026-08-13_2210"
    python migrar.py importar  --paquete "..." --simular
    python migrar.py importar  --paquete "..."
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from nucleo import estado as modulo_estado  # noqa: E402
from nucleo import exportar as modulo_exportar  # noqa: E402
from nucleo import importar as modulo_importar  # noqa: E402
from nucleo.util import (  # noqa: E402
    VERSION,
    Consola,
    escribir_json,
    es_windows,
    marca_tiempo,
)


def _construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="migrar.py",
        description="Migra variables de entorno, configuraciones, automatizaciones y programas.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--version", action="version", version=f"migrador {VERSION}")

    # Se declara en un padre para que --silencioso valga tanto antes como
    # después del subcomando; argparse no lo hace solo.
    comun = argparse.ArgumentParser(add_help=False)
    comun.add_argument("--silencioso", action="store_true", help="solo muestra errores")
    parser.add_argument("--silencioso", action="store_true", help="solo muestra errores")

    subcomandos = parser.add_subparsers(dest="comando", required=True)

    escanear = subcomandos.add_parser(
        "escanear", parents=[comun],
        help="inventaría este equipo sin copiar ni modificar nada",
    )
    escanear.add_argument("--salida", type=Path, help="guarda el informe en un JSON")
    escanear.add_argument(
        "--sin-carpetas", action="store_true",
        help="omite la búsqueda de carpetas de trabajo (es la parte más lenta)",
    )

    exportar = subcomandos.add_parser(
        "exportar", parents=[comun], help="crea el paquete de migración"
    )
    exportar.add_argument(
        "--destino", type=Path, default=Path.cwd(),
        help="carpeta donde crear el paquete (por defecto, la carpeta actual)",
    )
    exportar.add_argument(
        "--sin-secretos", action="store_true",
        help="excluye variables y archivos con credenciales",
    )
    exportar.add_argument(
        "--sin-archivos", action="store_true",
        help="no copia las carpetas de trabajo, solo las inventaría",
    )
    exportar.add_argument(
        "--carpeta", action="append", default=[], metavar="RUTA",
        help="carpeta extra a incluir (se puede repetir)",
    )
    exportar.add_argument(
        "--limite-gb", type=float, default=5.0,
        help="tope de tamaño para las carpetas de trabajo (por defecto 5 GB)",
    )

    estado = subcomandos.add_parser(
        "estado", parents=[comun],
        help="compara este equipo con un paquete y dice qué falta",
    )
    estado.add_argument("--paquete", type=Path, required=True, help="carpeta del paquete")
    estado.add_argument("--salida", type=Path, help="guarda el resultado en un JSON")

    importar = subcomandos.add_parser(
        "importar", parents=[comun], help="aplica el paquete en este equipo"
    )
    importar.add_argument("--paquete", type=Path, required=True, help="carpeta del paquete")
    importar.add_argument(
        "--solo", nargs="+", choices=modulo_importar.SECCIONES, metavar="SECCION",
        help=f"aplica solo estas secciones: {', '.join(modulo_importar.SECCIONES)}",
    )
    importar.add_argument(
        "--simular", action="store_true", help="muestra qué haría, sin escribir nada"
    )
    importar.add_argument(
        "--con-sistema", action="store_true",
        help="también aplica variables de sistema (requiere ser administrador)",
    )

    comparar = subcomandos.add_parser(
        "comparar", parents=[comun], help="compara dos informes de 'escanear'"
    )
    comparar.add_argument("informe_a", type=Path)
    comparar.add_argument("informe_b", type=Path)
    comparar.add_argument("--salida", type=Path, help="guarda el resultado en un JSON")

    return parser


def main(argv: list[str] | None = None) -> int:
    argumentos = _construir_parser().parse_args(argv)
    consola = Consola(silencioso=argumentos.silencioso)

    consola.info("")
    consola.info(f"  Migrador de computador {VERSION}")

    if not es_windows():
        consola.aviso(
            "esto no es Windows: las variables del registro, las tareas programadas "
            "y el inventario de programas no estarán disponibles."
        )

    if argumentos.comando == "escanear":
        informe = modulo_estado.escanear_equipo(
            consola, incluir_carpetas=not argumentos.sin_carpetas
        )
        destino = argumentos.salida or Path(f"informe-equipo-{marca_tiempo()}.json")
        escribir_json(destino, informe)
        consola.info("")
        consola.info(f"  Informe guardado en {destino.resolve()}")
        return 0

    if argumentos.comando == "exportar":
        paquete = modulo_exportar.crear_paquete(
            consola,
            destino=argumentos.destino,
            incluir_secretos=not argumentos.sin_secretos,
            incluir_archivos=not argumentos.sin_archivos,
            carpetas_extra=argumentos.carpeta,
            limite_gb=argumentos.limite_gb,
            ruta_herramienta=Path(__file__).resolve().parent,
        )
        consola.titulo("Listo")
        consola.info(f"  Paquete creado en:\n    {paquete}")
        consola.info("")
        consola.info("  Siguiente paso: copia esa carpeta al computador nuevo")
        consola.info("  (OneDrive, pendrive o disco externo) y ejecuta ahí INSTALAR.ps1.")
        if not argumentos.sin_secretos:
            consola.info("")
            consola.info("  Contiene credenciales en texto plano: bórralo de OneDrive")
            consola.info("  apenas termines la migración.")
        return 0

    if argumentos.comando == "estado":
        if not (argumentos.paquete / "manifiesto.json").exists():
            consola.error(f"no hay manifiesto.json en {argumentos.paquete}")
            consola.info("  ¿Apuntaste a la carpeta del paquete (la que contiene INSTALAR.ps1)?")
            return 1
        resultado = modulo_estado.estado_frente_a_paquete(consola, argumentos.paquete)
        if argumentos.salida:
            escribir_json(argumentos.salida, resultado)
            consola.info(f"  Resultado guardado en {argumentos.salida.resolve()}")
        return 0

    if argumentos.comando == "importar":
        if not (argumentos.paquete / "manifiesto.json").exists():
            consola.error(f"no hay manifiesto.json en {argumentos.paquete}")
            return 1
        modulo_importar.aplicar_paquete(
            consola,
            ruta_paquete=argumentos.paquete,
            secciones=argumentos.solo,
            simular=argumentos.simular,
            con_sistema=argumentos.con_sistema,
        )
        return 0

    if argumentos.comando == "comparar":
        for ruta in (argumentos.informe_a, argumentos.informe_b):
            if not ruta.exists():
                consola.error(f"no existe {ruta}")
                return 1
        resultado = modulo_estado.comparar_informes(
            consola, argumentos.informe_a, argumentos.informe_b
        )
        if argumentos.salida:
            escribir_json(argumentos.salida, resultado)
        return 0

    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n  Cancelado por el usuario.")
        sys.exit(130)
