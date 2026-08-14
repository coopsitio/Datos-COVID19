#!/usr/bin/env python3
"""Pruebas del migrador. Se ejecutan en cualquier sistema operativo.

    python pruebas.py

Las partes que dependen del registro de Windows se prueban con un registro
simulado, porque son justamente las que pueden dejar el equipo nuevo inservible
si se equivocan (sobre todo la fusión del PATH).
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from nucleo import estado, exportar, importar, recolectores, scripts, util, windows  # noqa: E402
from nucleo.util import Consola  # noqa: E402

CALLADA = Consola(silencioso=True)


@contextlib.contextmanager
def equipo_simulado(inicio: Path, variables: dict | None = None):
    """Aísla por completo las pruebas del equipo real.

    Hay que tapar tres vías, no una: el registro, el entorno y el perfil.

    - El registro y los comandos de Windows: sin esto, en Windows la ronda
      completa leía las variables de verdad, exportaba las tareas programadas
      reales y metía las credenciales del usuario en el paquete de prueba.
    - Las variables de entorno: %OneDrive%, %APPDATA% y %LOCALAPPDATA% se leen
      del entorno, no de Path.home(). Sin taparlas, el descubrimiento se ponía
      a recorrer el OneDrive real —descargándolo desde la nube— y copiaba las
      configuraciones verdaderas de VS Code y Windows Terminal al paquete.
    - Path.home(), para que todo apunte al perfil falso.

    OneDrive queda apuntando a una ruta inexistente: el descubrimiento
    comprueba que exista antes de recorrerla, así que simplemente la ignora.
    """
    variables = variables or {}
    entorno = {
        "APPDATA": str(inicio / "AppData/Roaming"),
        "LOCALAPPDATA": str(inicio / "AppData/Local"),
        "ProgramData": str(inicio / "ProgramData"),
        "OneDrive": str(inicio / "OneDriveInexistente"),
        "OneDriveCommercial": str(inicio / "OneDriveInexistente"),
        "USERNAME": "usuario-de-prueba",
    }
    with mock.patch.dict(os.environ, entorno), \
         mock.patch.object(Path, "home", staticmethod(lambda: inicio)), \
         mock.patch.object(windows, "leer_variables", lambda a: dict(variables.get(a, {}))), \
         mock.patch.object(windows, "escribir_variable", lambda *a, **k: (True, "")), \
         mock.patch.object(windows, "avisar_cambio_de_entorno", lambda: None), \
         mock.patch.object(windows, "guardar_tareas", lambda *a, **k: []), \
         mock.patch.object(windows, "listar_tareas", lambda *a, **k: []), \
         mock.patch.object(windows, "inventario_gestores", lambda *a, **k: {}), \
         mock.patch.object(windows, "programas_del_registro", lambda: []), \
         mock.patch.object(windows, "carpeta_inicio", lambda: inicio / "InicioInexistente"):
        yield


def _perfil_de_ejemplo(raiz: Path) -> Path:
    """Arma un perfil de usuario parecido al de un equipo real."""
    inicio = raiz / "antiguo"
    for sub in (".claude/commands", ".claude/agents", ".claude/skills/ingreso-medicos",
                ".claude/projects/basura", ".ssh", "Documentos/automatizaciones/.claude"):
        (inicio / sub).mkdir(parents=True, exist_ok=True)

    (inicio / ".claude/CLAUDE.md").write_text("# memoria", encoding="utf-8")
    (inicio / ".claude/settings.json").write_text('{"model":"opus"}', encoding="utf-8")
    (inicio / ".claude/commands/reporte.md").write_text("/reporte", encoding="utf-8")
    (inicio / ".claude/agents/revisor.md").write_text("agente", encoding="utf-8")
    (inicio / ".claude/skills/ingreso-medicos/SKILL.md").write_text("skill", encoding="utf-8")
    # Historial: es lo que NO debe viajar.
    (inicio / ".claude/projects/basura/historial.jsonl").write_text("x" * 200_000, encoding="utf-8")

    (inicio / ".claude.json").write_text(json.dumps({
        "mcpServers": {"ClickUp": {"command": "npx"}, "Gmail": {"command": "npx"}},
        "projects": {"C:/repo": {"history": [{"display": "y" * 5000}], "allowedTools": ["Bash"]}},
    }), encoding="utf-8")

    (inicio / ".gitconfig").write_text("[user]\n\tname = Diego\n", encoding="utf-8")
    (inicio / ".ssh/config").write_text("Host github\n", encoding="utf-8")
    (inicio / "Documentos/automatizaciones/bot.py").write_text('print("bot")', encoding="utf-8")
    return inicio


class PruebaFusionDePath(unittest.TestCase):
    """La fusión del PATH es lo más delicado: no debe perder ni duplicar rutas."""

    def setUp(self) -> None:
        self.registro = {
            "usuario": {
                "PATH": {"valor": r"C:\Windows;C:\SoloDelNuevo", "tipo": 2},
                "EDITOR": {"valor": "notepad", "tipo": 1},
            }
        }
        self.escrituras: list[tuple] = []
        self.paquete = Path(tempfile.mkdtemp())
        (self.paquete / "variables").mkdir()
        (self.paquete / "variables/usuario.json").write_text(json.dumps({
            "PATH": {"valor": r"C:\Windows;C:\Antiguo\bin;C:\Python311", "tipo": 2},
            "EDITOR": {"valor": "code", "tipo": 1},
            "API_TOKEN": {"valor": "secreto-123", "tipo": 1},
        }), encoding="utf-8")

    def tearDown(self) -> None:
        shutil.rmtree(self.paquete, ignore_errors=True)

    def _escribir(self, ambito, nombre, valor, tipo=1):
        self.escrituras.append((ambito, nombre, valor))
        self.registro.setdefault(ambito, {})[nombre] = {"valor": valor, "tipo": tipo}
        return True, ""

    def _importar(self, simular: bool = False):
        with mock.patch.object(windows, "leer_variables", lambda a: dict(self.registro.get(a, {}))), \
             mock.patch.object(windows, "escribir_variable", self._escribir), \
             mock.patch.object(windows, "avisar_cambio_de_entorno", lambda: None):
            app = importar.Aplicador(CALLADA, simular=simular)
            importar._importar_variables(app, self.paquete, con_sistema=False)
            return app

    def test_conserva_las_rutas_del_equipo_nuevo(self):
        self._importar()
        self.assertEqual(
            self.registro["usuario"]["PATH"]["valor"],
            r"C:\Windows;C:\SoloDelNuevo;C:\Antiguo\bin;C:\Python311",
        )

    def test_no_duplica_rutas_ya_presentes(self):
        self._importar()
        self.assertEqual(self.registro["usuario"]["PATH"]["valor"].count(r"C:\Windows;"), 1)

    def test_reemplaza_variables_normales_y_trae_credenciales(self):
        self._importar()
        self.assertEqual(self.registro["usuario"]["EDITOR"]["valor"], "code")
        self.assertEqual(self.registro["usuario"]["API_TOKEN"]["valor"], "secreto-123")

    def test_reejecutar_no_cambia_nada(self):
        self._importar()
        self.escrituras.clear()
        self._importar()
        self.assertEqual(self.escrituras, [])

    def test_simular_no_escribe(self):
        self._importar(simular=True)
        self.assertEqual(self.escrituras, [])
        self.assertEqual(self.registro["usuario"]["EDITOR"]["valor"], "notepad")

    def test_conserva_el_tipo_de_registro(self):
        # REG_EXPAND_SZ (2) mantiene vivas las referencias tipo %USERPROFILE%.
        self._importar()
        self.assertEqual(self.registro["usuario"]["PATH"]["tipo"], 2)


class PruebaPlantillasDeRuta(unittest.TestCase):
    """Las rutas deben viajar sin el nombre de usuario del equipo antiguo."""

    def test_ida_y_vuelta_por_el_perfil(self):
        origen = Path.home() / "Documentos" / "cosas"
        plantilla = util.a_plantilla(origen)
        self.assertTrue(plantilla.startswith("{"), plantilla)
        self.assertNotIn(Path.home().name, plantilla.split("}")[0])
        self.assertEqual(util.desde_plantilla(plantilla), origen)

    def test_token_desconocido_cae_en_el_perfil(self):
        destino = util.desde_plantilla("{ONEDRIVE}/Proyectos")
        self.assertTrue(str(destino).endswith("Proyectos"))


class PruebaRondaCompleta(unittest.TestCase):
    """Exportar en un equipo e importar en otro deja todo en su lugar."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.raiz = Path(tempfile.mkdtemp())
        cls.antiguo = _perfil_de_ejemplo(cls.raiz)
        cls.nuevo = cls.raiz / "nuevo"
        cls.nuevo.mkdir()

        with equipo_simulado(cls.antiguo):
            cls.paquete = exportar.crear_paquete(
                CALLADA, destino=cls.raiz / "traspaso",
                ruta_herramienta=Path(__file__).resolve().parent,
            )
        with equipo_simulado(cls.nuevo):
            importar.aplicar_paquete(CALLADA, cls.paquete)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.raiz, ignore_errors=True)

    def test_llegaron_las_automatizaciones_de_claude(self):
        for relativa in (".claude/commands/reporte.md", ".claude/agents/revisor.md",
                         ".claude/skills/ingreso-medicos/SKILL.md", ".claude/CLAUDE.md"):
            self.assertTrue((self.nuevo / relativa).exists(), relativa)

    def test_el_historial_no_viaja(self):
        self.assertFalse((self.nuevo / ".claude/projects").exists())
        config = json.loads((self.nuevo / ".claude.json").read_text(encoding="utf-8"))
        self.assertNotIn("history", config["projects"]["C:/repo"])
        self.assertIn("allowedTools", config["projects"]["C:/repo"])

    def test_los_servidores_mcp_se_conservan(self):
        config = json.loads((self.nuevo / ".claude.json").read_text(encoding="utf-8"))
        self.assertEqual(sorted(config["mcpServers"]), ["ClickUp", "Gmail"])

    def test_llegaron_las_configuraciones(self):
        self.assertTrue((self.nuevo / ".gitconfig").exists())
        self.assertTrue((self.nuevo / ".ssh/config").exists())

    def test_llego_la_carpeta_de_trabajo(self):
        candidatos = list(self.nuevo.glob("Document*/automatizaciones/bot.py"))
        self.assertTrue(candidatos, "no se copió la carpeta de trabajo")

    def test_la_basura_no_viaja(self):
        self.assertEqual(list(self.nuevo.rglob("__pycache__")), [])
        self.assertEqual(list(self.nuevo.rglob("*.pyc")), [])

    def test_las_rutas_del_manifiesto_existen_en_el_paquete(self):
        manifiesto = json.loads((self.paquete / "manifiesto.json").read_text(encoding="utf-8"))
        entradas = list(manifiesto["configuraciones"]) + list(manifiesto["archivos"])
        for clave in ("carpeta", "config"):
            if manifiesto["claude_archivos"].get(clave):
                entradas.append(manifiesto["claude_archivos"][clave])
        self.assertTrue(entradas)
        for entrada in entradas:
            ruta = self.paquete / entrada["ruta_en_paquete"]
            self.assertTrue(ruta.exists(), f"{entrada['ruta_en_paquete']} no está en el paquete")

    def test_el_paquete_trae_la_herramienta_y_los_scripts(self):
        for relativa in ("INSTALAR.ps1", "LEEME.md", "manifiesto.json",
                         "herramienta/migrar.py", "variables/restaurar-variables.ps1",
                         "programas/instalar-programas.ps1"):
            self.assertTrue((self.paquete / relativa).exists(), relativa)

    def test_estado_queda_sin_pendientes(self):
        with equipo_simulado(self.nuevo):
            resultado = estado.estado_frente_a_paquete(CALLADA, self.paquete)
        self.assertEqual(resultado["pendientes"], 0, resultado)

    def test_el_paquete_no_contiene_datos_del_equipo_real(self):
        # Si las pruebas leen el registro de verdad, las credenciales del
        # usuario terminan dentro del paquete de prueba.
        variables = json.loads((self.paquete / "variables/usuario.json").read_text(encoding="utf-8"))
        self.assertEqual(variables, {})

    def test_todo_lo_recolectado_viene_del_perfil_falso(self):
        """Guardia contra fugas: nada del paquete puede venir del equipo real.

        Es la prueba que faltaba. El aislamiento se escapó una vez por
        %OneDrive% y otra por %APPDATA%, y en ambos casos las pruebas
        recolectaron archivos verdaderos sin que nada fallara.
        """
        manifiesto = json.loads((self.paquete / "manifiesto.json").read_text(encoding="utf-8"))
        origenes = [c["origen"] for c in manifiesto["configuraciones"]]
        origenes += [c["origen"] for c in manifiesto["archivos"]]
        origenes += [c["origen"] for c in manifiesto["carpetas_detectadas"]]

        self.assertTrue(origenes, "el manifiesto quedó vacío: la prueba no verifica nada")
        for origen in origenes:
            self.assertTrue(
                Path(origen).is_relative_to(self.antiguo),
                f"se recolectó algo de fuera del perfil de prueba: {origen}",
            )


class PruebaDescubrimiento(unittest.TestCase):
    def test_no_confunde_carpetas_ocultas_con_proyectos(self):
        raiz = Path(tempfile.mkdtemp())
        try:
            inicio = _perfil_de_ejemplo(raiz)
            with equipo_simulado(inicio):
                carpetas = recolectores.descubrir_carpetas_de_trabajo(CALLADA)
            nombres = [c["nombre"] for c in carpetas]
            self.assertIn("automatizaciones", nombres)
            # ~/.claude lo maneja la sección de configuraciones, no esta.
            self.assertNotIn(".claude", nombres)
        finally:
            shutil.rmtree(raiz, ignore_errors=True)


class PruebaSalidaDeConsola(unittest.TestCase):
    """Los comandos de Windows no responden en UTF-8, y eso rompía los acentos."""

    def test_decodifica_cp850(self):
        # Como responde schtasks en un Windows en español.
        crudo = "Ejecución FTP Bancos".encode("cp850")
        self.assertEqual(util._decodificar(crudo), "Ejecución FTP Bancos")

    def test_decodifica_utf8(self):
        crudo = "Verificación de Folios".encode("utf-8")
        self.assertEqual(util._decodificar(crudo), "Verificación de Folios")

    def test_no_deja_caracteres_de_reemplazo(self):
        # cp850 y utf-8 son los dos casos reales: la consola de un Windows en
        # español y los comandos que ya responden en UTF-8.
        for codec in ("cp850", "utf-8"):
            texto = util._decodificar("Automática".encode(codec))
            self.assertNotIn("\ufffd", texto, codec)


class PruebaListadoDeTareas(unittest.TestCase):
    """El CSV de schtasks trae encabezados repetidos y nombres con acento."""

    CSV = (
        '"Nombre de tarea","Estado","Tarea que se ejecuta"\r\n'
        '"\\Ejecución FTP Bancos","Listo","C:\\scripts\\ftp.bat"\r\n'
        '"Nombre de tarea","Estado","Tarea que se ejecuta"\r\n'
        '"\\Verificación de Folios Automática","Listo","C:\\scripts\\folios.py"\r\n'
        '"\\Microsoft\\Windows\\Defrag\\ScheduledDefrag","Listo","defrag.exe"\r\n'
    )

    def _listar(self):
        salida = self.CSV
        with mock.patch.object(windows, "ejecutar", lambda *a, **k: (0, salida, "")):
            return windows.listar_tareas(CALLADA)

    def test_ignora_los_encabezados_repetidos(self):
        nombres = [t["nombre"] for t in self._listar()]
        self.assertNotIn("Nombre de tarea", nombres)

    def test_conserva_los_acentos(self):
        nombres = [t["nombre"] for t in self._listar()]
        self.assertIn("\\Ejecución FTP Bancos", nombres)
        self.assertIn("\\Verificación de Folios Automática", nombres)

    def test_descarta_las_tareas_de_windows(self):
        nombres = [t["nombre"] for t in self._listar()]
        self.assertEqual(len(nombres), 2, nombres)


class PruebaOracle(unittest.TestCase):
    """tnsnames.ora vive fuera del perfil y sin él no hay conexiones."""

    def setUp(self) -> None:
        self.raiz = Path(tempfile.mkdtemp())
        self.admin = self.raiz / "instantclient/network/admin"
        self.admin.mkdir(parents=True)
        (self.admin / "tnsnames.ora").write_text("BASE1 = (DESCRIPTION=...)", encoding="utf-8")
        (self.admin / "sqlnet.ora").write_text("NAMES.DIRECTORY_PATH=(TNSNAMES)", encoding="utf-8")

    def tearDown(self) -> None:
        shutil.rmtree(self.raiz, ignore_errors=True)

    def test_encuentra_la_configuracion_por_tns_admin(self):
        with mock.patch.dict(os.environ, {"TNS_ADMIN": str(self.admin)}):
            nombres = {Path(c["origen"]).name for c in recolectores.descubrir_oracle()}
        self.assertIn("tnsnames.ora", nombres)
        self.assertIn("sqlnet.ora", nombres)

    def test_encuentra_la_configuracion_por_oracle_home(self):
        with mock.patch.dict(os.environ, {"ORACLE_HOME": str(self.raiz / "instantclient")}):
            nombres = {Path(c["origen"]).name for c in recolectores.descubrir_oracle()}
        self.assertIn("tnsnames.ora", nombres)

    def test_quedan_marcados_para_no_restaurarse_sin_el_cliente(self):
        with mock.patch.dict(os.environ, {"TNS_ADMIN": str(self.admin)}):
            entradas = recolectores.descubrir_oracle()
        self.assertTrue(all(e["requiere_carpeta_previa"] for e in entradas))

    def test_no_se_restaura_si_falta_la_carpeta_del_cliente(self):
        paquete = Path(tempfile.mkdtemp())
        try:
            (paquete / "configuraciones/oracle").mkdir(parents=True)
            (paquete / "configuraciones/oracle/tnsnames.ora").write_text("x", encoding="utf-8")
            entrada = {
                "ruta_en_paquete": "configuraciones/oracle/tnsnames.ora",
                "destino_plantilla": str(self.raiz / "no-instalado/network/admin/tnsnames.ora"),
                "requiere_carpeta_previa": True,
            }
            app = importar.Aplicador(CALLADA, simular=False)
            importar._restaurar_ruta(app, paquete, entrada, "oracle")

            self.assertEqual(app.cambios, [])
            self.assertEqual(len(app.pendientes), 1)
            self.assertFalse((self.raiz / "no-instalado").exists(),
                             "no debe crear la carpeta del cliente Oracle")
        finally:
            shutil.rmtree(paquete, ignore_errors=True)

    def test_si_se_restaura_cuando_el_cliente_esta_instalado(self):
        paquete = Path(tempfile.mkdtemp())
        try:
            (paquete / "configuraciones/oracle").mkdir(parents=True)
            (paquete / "configuraciones/oracle/tnsnames.ora").write_text("BASE1", encoding="utf-8")
            destino = self.admin / "tnsnames.ora"
            entrada = {
                "ruta_en_paquete": "configuraciones/oracle/tnsnames.ora",
                "destino_plantilla": str(destino),
                "requiere_carpeta_previa": True,
            }
            app = importar.Aplicador(CALLADA, simular=False)
            importar._restaurar_ruta(app, paquete, entrada, "oracle")
            self.assertEqual(destino.read_text(encoding="utf-8"), "BASE1")
        finally:
            shutil.rmtree(paquete, ignore_errors=True)


class PruebaScriptsPowerShell(unittest.TestCase):
    """No hay PowerShell aquí, así que se revisa lo que sí se puede revisar."""

    def _generados(self) -> dict[str, str]:
        return {
            "restaurar-variables.ps1": scripts.script_variables({"A": 1}, {"B": 2}),
            "restaurar-tareas.ps1": scripts.script_tareas([{"nombre": "x"}]),
            "instalar-programas.ps1": scripts.script_programas({"winget": {}}),
            "INSTALAR.ps1": scripts.script_instalador(),
        }

    def test_param_es_la_primera_instruccion(self):
        # PowerShell falla si param() no va primero.
        for nombre, texto in self._generados().items():
            if "param(" not in texto:
                continue
            primera = next(l for l in texto.splitlines() if l.strip())
            self.assertTrue(primera.strip().startswith("param"), f"{nombre}: {primera!r}")

    def test_llaves_y_parentesis_balanceados(self):
        for nombre, texto in self._generados().items():
            for abre, cierra in (("{", "}"), ("(", ")")):
                self.assertEqual(texto.count(abre), texto.count(cierra),
                                 f"{nombre}: desbalance de {abre}{cierra}")


class PruebaDeteccionDeSecretos(unittest.TestCase):
    def test_reconoce_nombres_tipicos(self):
        for nombre in ("OPENAI_API_KEY", "gh_token", "DB_PASSWORD", "AWS_SECRET_ACCESS_KEY"):
            self.assertTrue(util.parece_secreto(nombre), nombre)

    def test_no_marca_variables_inocentes(self):
        for nombre in ("PATH", "EDITOR", "JAVA_HOME"):
            self.assertFalse(util.parece_secreto(nombre), nombre)


if __name__ == "__main__":
    unittest.main(verbosity=2)
