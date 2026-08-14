# Migrador de computador de trabajo

Herramienta para pasar todo lo que tienes en el computador antiguo al nuevo:
variables de entorno, configuraciones, automatizaciones hechas con Claude Code,
tareas programadas y la lista de programas para reinstalarlos.

Pensada para **Windows → Windows**, moviendo el paquete por OneDrive, pendrive
o disco externo.

## La idea en tres pasos

```
COMPUTADOR ANTIGUO                OneDrive                COMPUTADOR NUEVO
──────────────────                ────────                ────────────────
python migrar.py exportar   ──►   paquete   ──►   INSTALAR.ps1
                                                  (instala Python si falta,
                                                   muestra estado, simula,
                                                   pregunta y aplica)
```

Nada se aplica sin que confirmes: siempre hay una simulación antes, y lo que se
sobrescribe queda respaldado en `%USERPROFILE%\migracion-respaldo-<fecha>`.

## Paso a paso

### 1. En el computador antiguo

Primero mira qué hay, sin tocar nada:

```powershell
python migrar.py escanear
```

Te muestra cuántas variables tienes, tus comandos y skills de Claude, tus
tareas programadas, tus programas y qué carpetas de trabajo detectó. Revisa
esa lista: es lo que se va a llevar.

Después arma el paquete:

```powershell
python migrar.py exportar --destino "$env:OneDrive\Migracion"
```

Queda una carpeta `migracion-<EQUIPO>-<fecha>` con todo adentro, incluida la
propia herramienta y un `LEEME.md` con el resumen de lo que trae.

### 2. Llevarlo al computador nuevo

Copia esa carpeta completa. Si es por OneDrive, **espera a que termine de
sincronizar**: los archivos deben verse con el visto verde, no con el ícono de
nube, o llegarán vacíos.

### 3. En el computador nuevo

```powershell
powershell -ExecutionPolicy Bypass -File .\INSTALAR.ps1
```

Ese script instala Python si no está, te muestra qué le falta al equipo nuevo,
hace una simulación y recién ahí pregunta si aplicar.

Los programas van aparte, porque toma bastante rato:

```powershell
powershell -ExecutionPolicy Bypass -File .\programas\instalar-programas.ps1
```

## Comandos

| Comando | Para qué sirve |
|---|---|
| `escanear` | Inventaría el equipo actual. No copia ni modifica nada. |
| `exportar` | Crea el paquete de migración. |
| `estado` | En el equipo nuevo: compara con el paquete y dice qué falta. |
| `importar` | Aplica el paquete. Con `--simular` no escribe nada. |
| `comparar` | Compara dos informes de `escanear` (antiguo vs nuevo). |

Opciones útiles:

```powershell
# Ver qué haría sin tocar nada
python migrar.py importar --paquete "..." --simular

# Aplicar solo una parte
python migrar.py importar --paquete "..." --solo claude configuraciones

# Incluir variables de sistema (necesita consola como administrador)
python migrar.py importar --paquete "..." --con-sistema

# Exportar sin credenciales
python migrar.py exportar --sin-secretos

# Agregar una carpeta que el descubrimiento automático no encontró
python migrar.py exportar --carpeta "D:\Trabajo\scripts"

# Solo el inventario, sin copiar archivos pesados
python migrar.py exportar --sin-archivos
```

## Qué se lleva

**Variables de entorno** — Se leen del registro, de usuario y de sistema,
conservando el tipo (`REG_EXPAND_SZ`, para que `%USERPROFILE%` siga funcionando).

Al aplicarlas, `PATH` y las demás variables de rutas **se fusionan**: se
conserva lo que el equipo nuevo ya trae y se agrega solo lo que falta. Nunca se
reemplaza el `PATH` completo, porque eso dejaría el equipo sin sus propias
rutas. Si una ruta del equipo antiguo no existe en el nuevo, te avisa.

Las variables que Windows administra solo (`USERNAME`, `TEMP`, `PROGRAMFILES`…)
quedan fuera a propósito.

**Configuraciones** — `.gitconfig`, `.ssh/config`, perfiles de PowerShell
(incluyendo Documentos redirigido a OneDrive), `settings.json` y atajos de
VS Code, Windows Terminal, `.npmrc`, `.condarc`, `pip.ini`, configs de AWS,
Azure y Docker.

**Claude Code** — La carpeta `~/.claude` completa: comandos, agentes, skills,
hooks, plugins y tu `CLAUDE.md`. Más `~/.claude.json`, del que se saca el
historial de conversaciones (puede pesar decenas de MB y no sirve en el equipo
nuevo) conservando los servidores MCP y los permisos.

**Automatizaciones** — Tareas programadas exportadas a XML, que es lo que
permite recrearlas idénticas, y la carpeta de Inicio de Windows.

**Programas** — La *lista*, no los binarios: `winget export` (re-importable
directo), Chocolatey, `pip freeze`, paquetes globales de npm, extensiones de
VS Code, y el inventario completo del registro para lo que no esté en ningún
gestor.

**Carpetas de trabajo** — Busca en tu perfil y en OneDrive carpetas que
parezcan trabajo propio: las que tienen `.claude`, `.git`, `CLAUDE.md`,
`requirements.txt`, `package.json` o scripts sueltos. Cuando encuentra una, se
la lleva entera y no sigue bajando.

Quedan fuera `node_modules`, `.venv`, `__pycache__`, `dist`, `build` y
similares, los archivos de más de 50 MB y las carpetas ocultas del perfil (esas
las cubre la sección de configuraciones). El tope total son 5 GB, ajustable con
`--limite-gb`.

**Lo que ya está en OneDrive no se copia al paquete.** No tiene sentido
duplicarlo: al iniciar sesión en el computador nuevo, OneDrive lo sincroniza
solo. El escaneo te las lista igual, para que sepas que están consideradas. Si
prefieres llevarlas también dentro del paquete, usa `--incluir-onedrive`.

Los archivos que OneDrive dejó **solo en la nube** (el ícono de nube, sin el
visto verde) tampoco se copian, porque copiarlos obligaría a descargarlos y
podría traerse gigabytes sin avisar. Se cuentan aparte en el informe.

## Qué NO se lleva

- Los programas en sí: viaja la lista para reinstalarlos.
- Licencias, sesiones abiertas y credenciales guardadas dentro de cada programa.
- El historial de conversaciones de Claude.
- Carpetas sin ninguna señal de ser trabajo propio (una carpeta con puros PDF no
  se detecta). Agrégalas con `--carpeta`.
- Nada de fuera de tu perfil de usuario, salvo lo que indiques con `--carpeta`.

## Sobre las credenciales

**Por omisión el paquete incluye las credenciales en texto plano**, porque así
se pidió: las variables tipo `API_KEY`, `TOKEN`, `PASSWORD` viajan con su valor
en `variables/usuario.json`.

Eso significa que cualquiera con acceso a esa carpeta en OneDrive puede leerlas.
Al exportar, la herramienta te dice cuáles son. Recomendación práctica:

- Borra el paquete de OneDrive apenas termines la migración.
- No lo compartas por chat ni por correo.
- Si prefieres reponerlas a mano, usa `--sin-secretos` y quedan afuera.

## Requisitos

Python 3.9 o superior, sin librerías externas. En el computador nuevo ni
siquiera hace falta tenerlo: `INSTALAR.ps1` lo instala con winget.

## Pruebas

```powershell
python pruebas.py
```

22 pruebas que cubren la fusión del `PATH`, la traducción de rutas entre
equipos con distinto nombre de usuario, la ronda completa exportar → importar
y la sintaxis de los scripts PowerShell generados.

## Estructura

```
migrador/
  migrar.py            punto de entrada
  pruebas.py           pruebas
  nucleo/
    util.py            utilidades, rutas portables, copia con límites
    windows.py         registro, tareas programadas, programas
    recolectores.py    configuraciones, Claude, descubrimiento de carpetas
    exportar.py        arma el paquete
    importar.py        lo aplica
    estado.py          escanear, estado y comparar
    scripts.py         genera los .ps1
```

## Limitaciones conocidas

- Las tareas programadas se recrean con el XML del equipo antiguo. Si un
  programa quedó instalado en otra ruta, la tarea se crea pero falla al
  ejecutarse: revísalas en el Programador de tareas después de migrar.
- Las variables de sistema requieren consola como administrador. Sin eso, se
  omiten y te avisa.
- `winget import` no encuentra todos los programas: los que no estén en ningún
  gestor quedan listados en `programas/programas-instalados.json` para
  instalarlos a mano.
- Los scripts PowerShell se revisaron por lectura y con pruebas automáticas de
  sintaxis, pero **no se pudieron ejecutar en un Windows real** durante el
  desarrollo. Usa siempre `--simular` la primera vez.
