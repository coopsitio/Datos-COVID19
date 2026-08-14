# Estado de la migración — traspaso a una sesión nueva

Documento de contexto para retomar esta migración desde otra sesión de Claude.
Última actualización: 2026-08-14.

## Qué se está haciendo

Diego (usuario `dquiroz`) recibió un computador de trabajo nuevo y hay que
llevarle todo lo del antiguo: variables de entorno, configuraciones,
automatizaciones de Claude Code, tareas programadas y programas.

Para eso se construyó una herramienta en `migrador/` de este mismo repositorio
(`coopsitio/Datos-COVID19`, rama `claude/work-computer-migration-lrs5ly`). Lee
`migrador/README.md` para el detalle de uso; acá va solo el estado.

## Los dos equipos

| | Antiguo | Nuevo |
|---|---|---|
| Nombre | `DQUIROZMNB` | (Windows 11) |
| Sistema | Windows 10 | Windows 11 |
| Python | 3.11.5 | por confirmar |
| OneDrive | `D:\OneDrive - chilquinta.cl (1)` | `C:\Users\dquiroz\OneDrive - chilquinta.cl` |
| winget | **no disponible** | sí (es Windows 11) |

Entorno corporativo (Chilquinta), así que hay que contar con políticas de
seguridad restrictivas.

## Dónde está todo

- **Herramienta**: `migrador/` en este repo. En el PC antiguo está clonada en
  `C:\Users\dquiroz\Datos-COVID19`.
- **Paquete de migración**: `migracion-DQUIROZMNB-2026-08-14_0850`, dentro de
  `OneDrive\Migracion`. Ya sincronizado al PC nuevo.
- Hay un paquete anterior de las `0723` que **debe borrarse**: se generó antes
  de un arreglo y contiene entradas basura.

## Qué trae el paquete

- 15 variables de entorno de usuario y 29 de sistema.
- 15 tareas programadas (XML) y 4 elementos de la carpeta de Inicio.
- Configuraciones: `.gitconfig`, `settings.json` y snippets de VS Code,
  Windows Terminal, y **3 archivos de Oracle** (ver más abajo).
- `~/.claude` completo: 385 archivos, 18.9 MB (2 skills, 2 plugins, sin
  comandos ni agentes, sin servidores MCP).
- 3 carpetas de trabajo: `cortes_2020_2025` (76.3 MB),
  `Datos-COVID19` (14 MB, es el clon de esta herramienta, prescindible) y
  `AgenteFacturacionDia` (83.6 KB).
- Inventario de 122 programas en `programas/programas-instalados.json`.

## Dónde está trabado ahora mismo

En el PC nuevo, al ejecutar el instalador:

```powershell
powershell -ExecutionPolicy Bypass -File .\INSTALAR.ps1
```

el proceso **termina con código 1067 (0x42b) sin imprimir una sola línea**, ni
siquiera la cabecera del script. Muere antes de empezar.

### Diagnóstico pendiente

Falta ejecutar esto en el PC nuevo, desde un PowerShell abierto **desde el menú
Inicio** (para que la ventana no se cierre y se vean los errores):

```powershell
$p = "$env:USERPROFILE\OneDrive - chilquinta.cl\Migracion\migracion-DQUIROZMNB-2026-08-14_0850"
$PSVersionTable.PSVersion
"LanguageMode: $($ExecutionContext.SessionState.LanguageMode)"
Get-ExecutionPolicy -List | Format-Table -AutoSize
Test-Path $p
Get-ChildItem $p | Select-Object Name, Length
Get-Command python -ErrorAction SilentlyContinue | Select-Object Source
```

**Hipótesis principal**: `LanguageMode` en `ConstrainedLanguage` por política
corporativa. En ese modo PowerShell bloquea los tipos .NET, y los scripts
generados usan `[Environment]::SetEnvironmentVariable`. Explicaría la muerte
silenciosa.

### Camino alternativo, que evita PowerShell por completo

Toda la migración se puede hacer con la herramienta en Python, sin tocar
ningún `.ps1`:

```powershell
cd "<ruta del paquete>"
python herramienta\migrar.py estado    --paquete .
python herramienta\migrar.py importar  --paquete . --simular
python herramienta\migrar.py importar  --paquete .
```

Si el bloqueo es una política que impide ejecutar `.ps1`, esta ruta la esquiva.
Requiere Python instalado en el PC nuevo (`winget install Python.Python.3.12`).

Ojo: `migrar.py` escribe las variables con el módulo `winreg`, no con
PowerShell, así que no le afecta el `LanguageMode`.

## Lo que falta después de desbloquear

1. **Instalar Oracle Instant Client 23.9** (Basic + Tools, 64 bits) en
   exactamente `C:\oracle\instantclient_23_9`. Esa ruta importa: las variables
   `ORACLE_HOME`, `TNS_ADMIN` y `PATH` que trae el paquete apuntan ahí.
   Resuelve el `Unable to find OCI.DLL` que da SqlDbx.
2. **Correr la importación** (por PowerShell o por Python).
3. **Los 122 programas van a mano.** El PC antiguo no tiene winget ni
   Chocolatey, así que no hay lista re-importable. La referencia es
   `programas/programas-instalados.json`; el PC nuevo sí tiene winget para
   instalarlos uno a uno.
4. **OpenSmartflex**: hay un segundo `tnsnames.ora` en
   `D:\OpenSmartflex\ODAC\`. Pertenece a esa aplicación corporativa. El
   migrador **no** lo restaura si la carpeta no existe (es a propósito). Cuando
   instalen OpenSmartflex, volver a correr la importación para completarlo.
   Verificar además si el PC nuevo tiene disco `D:`.
5. **Revisar las 15 tareas programadas** en el Programador de tareas. Se
   recrean con el XML del equipo antiguo: si un programa quedó en otra ruta, la
   tarea se crea pero falla al ejecutarse.
6. **Borrar el paquete de OneDrive** al terminar: contiene la variable
   `OPENAI_API_KEY` en texto plano (fue una decisión explícita del usuario).

## Cosas que ya se corrigieron (no volver a tropezar)

Todas salieron de ejecutar en el Windows real y tienen prueba de regresión:

- **Codificación**: `schtasks` responde en cp850, no UTF-8. Los nombres de
  tarea con acento llegaban corruptos y por eso no se podían exportar.
- **Salida propia**: forzar UTF-8 en una consola cp850 producía mojibake.
- **Encabezados repetidos** de `schtasks` se colaban como una tarea llamada
  «Nombre de tarea».
- **Pruebas que tocaban la máquina real**: leían el registro y recorrían el
  OneDrive verdadero. Hay una guardia que ahora lo impide.
- **OneDrive con dos raíces**: se detectaba por `%OneDrive%`, que apunta a una
  sola. Ahora se reconoce por el nombre de la carpeta.
- **Descargas**: 70 de los 80 GB detectados eran `Downloads`. Se omite.
- **Dos `tnsnames.ora`** se pisaban dentro del paquete.
- **El paquete se recolectaba a sí mismo** al exportar dentro del perfil.
- **Archivos sin descargar de OneDrive**: al restaurar se saltaban en
  silencio, dejando carpetas vacías con mensaje de éxito.

## Cómo verificar que la herramienta está sana

```powershell
cd <repo>\migrador
python pruebas.py
```

Deben pasar **40 pruebas**. En PowerShell la salida sale en rojo porque
`unittest` escribe por `stderr`; eso es normal, lo que importa es el `OK`
final.

## Advertencias

- La importación siempre tiene modo `--simular`. Úsalo primero.
- Lo que se sobrescribe queda respaldado en
  `%USERPROFILE%\migracion-respaldo-<fecha>`.
- El `PATH` se fusiona, nunca se reemplaza.
- Las variables de sistema requieren consola como administrador; sin eso se
  omiten y avisa.
- Los scripts `.ps1` **nunca se han ejecutado con éxito en un Windows real**.
  Se revisaron por lectura y con pruebas de sintaxis, nada más.
