"""Generación de los scripts PowerShell que acompañan al paquete de migración.

Existen porque el computador nuevo llega vacío: puede no tener Python todavía,
y PowerShell siempre está. Con estos scripts se puede restaurar lo esencial sin
depender de nada más.

Nota para quien edite esto: en PowerShell `param(...)` debe ser la primera
instrucción del script, antes de cualquier asignación o comentario ejecutable.
"""

from __future__ import annotations

# En los bucles se llaman comandos nativos (schtasks, winget, choco). Con
# 'Stop', el primer mensaje a stderr aborta el script completo y se pierde todo
# lo que venía después; con 'Continue' se informa el fallo y se sigue.
PREAMBULO = """$ErrorActionPreference = 'Continue'
$raiz = Split-Path -Parent $MyInvocation.MyCommand.Path
"""


def script_variables(variables_usuario: dict, variables_sistema: dict) -> str:
    """Restaura variables de entorno leyendo los JSON del paquete."""
    return f"""param(
    [switch]$IncluirSistema,
    [switch]$SoloMostrar
)

# Generado por el migrador. Restaura {len(variables_usuario)} variable(s) de
# usuario y {len(variables_sistema)} de sistema.
{PREAMBULO}""" + r"""
function Restaurar-Variables {
    param($Archivo, $Ambito)

    if (-not (Test-Path $Archivo)) {
        Write-Host "  (no existe $Archivo, se omite)" -ForegroundColor DarkGray
        return
    }
    $datos = Get-Content $Archivo -Raw -Encoding UTF8 | ConvertFrom-Json
    $rutas = @('PATH','PYTHONPATH','PSMODULEPATH','CLASSPATH','INCLUDE','LIB')

    foreach ($propiedad in $datos.PSObject.Properties) {
        $nombre = $propiedad.Name
        $valor  = $propiedad.Value.valor

        if ($rutas -contains $nombre.ToUpper()) {
            # Las variables de rutas se fusionan: se conserva lo del equipo
            # nuevo y se agrega solo lo que falta del equipo antiguo.
            $actual = [Environment]::GetEnvironmentVariable($nombre, $Ambito)
            $existentes = @()
            if ($actual) { $existentes = @($actual -split ';' | Where-Object { $_ -ne '' }) }

            $agregar = @()
            foreach ($entrada in ($valor -split ';')) {
                if ($entrada -eq '') { continue }
                if ($existentes -notcontains $entrada) { $agregar += $entrada }
            }
            if ($agregar.Count -eq 0) {
                Write-Host "  = $nombre sin cambios" -ForegroundColor DarkGray
                continue
            }
            Write-Host "  + $nombre : se agregan $($agregar.Count) ruta(s)" -ForegroundColor Green
            foreach ($a in $agregar) {
                $expandida = [Environment]::ExpandEnvironmentVariables($a)
                if (-not (Test-Path $expandida)) {
                    Write-Host "      ojo: '$a' no existe en este equipo" -ForegroundColor Yellow
                }
            }
            $nuevo = (($existentes + $agregar) -join ';')
            if (-not $SoloMostrar) {
                [Environment]::SetEnvironmentVariable($nombre, $nuevo, $Ambito)
            }
        }
        else {
            $actual = [Environment]::GetEnvironmentVariable($nombre, $Ambito)
            if ($actual -eq $valor) {
                Write-Host "  = $nombre ya tiene el mismo valor" -ForegroundColor DarkGray
                continue
            }
            if ($actual) {
                Write-Host "  ~ $nombre se reemplaza (antes: $actual)" -ForegroundColor Yellow
            } else {
                Write-Host "  + $nombre" -ForegroundColor Green
            }
            if (-not $SoloMostrar) {
                [Environment]::SetEnvironmentVariable($nombre, $valor, $Ambito)
            }
        }
    }
}

Write-Host ""
Write-Host "Variables de usuario" -ForegroundColor Cyan
Restaurar-Variables (Join-Path $raiz 'usuario.json') 'User'

if ($IncluirSistema) {
    $identidad = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identidad)
    $esAdmin = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

    if (-not $esAdmin) {
        Write-Host ""
        Write-Host "Las variables de sistema requieren PowerShell como administrador." -ForegroundColor Red
    } else {
        Write-Host ""
        Write-Host "Variables de sistema" -ForegroundColor Cyan
        Restaurar-Variables (Join-Path $raiz 'sistema.json') 'Machine'
    }
}

Write-Host ""
if ($SoloMostrar) {
    Write-Host "Modo simulacion: no se escribio nada." -ForegroundColor Cyan
} else {
    Write-Host "Listo. Abre una consola nueva para que los cambios tomen efecto." -ForegroundColor Cyan
}
"""


def script_tareas(tareas: list[dict]) -> str:
    """Recrea las tareas programadas desde los XML exportados."""
    return f"""param([switch]$SoloMostrar)

# Generado por el migrador. Tareas exportadas: {len(tareas)}.
{PREAMBULO}""" + r"""
$xmls = @(Get-ChildItem -Path $raiz -Filter '*.xml' -ErrorAction SilentlyContinue)
if ($xmls.Count -eq 0) {
    Write-Host "No hay tareas que restaurar." -ForegroundColor DarkGray
    return
}

Write-Host ""
Write-Host "Se restauraran $($xmls.Count) tarea(s) programada(s)." -ForegroundColor Cyan
Write-Host "Se crearan a nombre de $env:USERDOMAIN\$env:USERNAME." -ForegroundColor DarkGray
Write-Host ""

$fallidas = 0
foreach ($xml in $xmls) {
    $nombre = $xml.BaseName
    Write-Host "  $nombre"
    if ($SoloMostrar) { continue }

    # El XML trae las rutas y la cuenta del equipo antiguo. Si algo no calza,
    # schtasks lo informa y seguimos con la siguiente tarea.
    $salida = & schtasks /create /tn $nombre /xml $xml.FullName /f 2>&1
    if ($LASTEXITCODE -ne 0) {
        $fallidas++
        Write-Host "    fallo: $salida" -ForegroundColor Yellow
        Write-Host "    revisa la ruta del programa y la cuenta de ejecucion" -ForegroundColor DarkGray
    } else {
        Write-Host "    creada" -ForegroundColor Green
    }
}

Write-Host ""
if ($fallidas -gt 0) {
    Write-Host "$fallidas tarea(s) no se pudieron crear." -ForegroundColor Yellow
}
Write-Host "Revisa las tareas en el Programador de tareas antes de confiar en ellas." -ForegroundColor Cyan
"""


def script_programas(inventario: dict) -> str:
    """Reinstala programas con winget y los demás gestores."""
    gestores = ", ".join(sorted(inventario)) or "ninguno"
    return f"""param([switch]$SoloMostrar)

# Generado por el migrador. Gestores encontrados en el equipo antiguo: {gestores}.
{PREAMBULO}""" + r"""
$winget = Join-Path $raiz 'winget.json'
if (Test-Path $winget) {
    Write-Host ""
    Write-Host "Reinstalando programas con winget..." -ForegroundColor Cyan
    if ($SoloMostrar) {
        $lista = Get-Content $winget -Raw -Encoding UTF8 | ConvertFrom-Json
        foreach ($fuente in $lista.Sources) {
            foreach ($paquete in $fuente.Packages) {
                Write-Host "  $($paquete.PackageIdentifier)"
            }
        }
    } elseif (Get-Command winget -ErrorAction SilentlyContinue) {
        # --ignore-unavailable evita que un paquete retirado detenga el resto.
        & winget import -i $winget --accept-package-agreements --accept-source-agreements --ignore-unavailable
    } else {
        Write-Host "  winget no esta disponible en este equipo." -ForegroundColor Yellow
    }
} else {
    Write-Host "No hay winget.json en el paquete." -ForegroundColor DarkGray
}

$choco = Join-Path $raiz 'chocolatey.txt'
if ((Test-Path $choco) -and (Get-Command choco -ErrorAction SilentlyContinue)) {
    Write-Host ""
    Write-Host "Paquetes de Chocolatey..." -ForegroundColor Cyan
    foreach ($linea in Get-Content $choco) {
        $nombre = ($linea -split '\|')[0]
        if (-not $nombre) { continue }
        Write-Host "  $nombre"
        if (-not $SoloMostrar) { & choco install $nombre -y --limit-output }
    }
}

$pip = Join-Path $raiz 'pip-requirements.txt'
if (Test-Path $pip) {
    Write-Host ""
    Write-Host "Paquetes de Python (pip)..." -ForegroundColor Cyan
    if (Get-Command python -ErrorAction SilentlyContinue) {
        if ($SoloMostrar) {
            Write-Host "  $(@(Get-Content $pip).Count) paquete(s)"
        } else {
            & python -m pip install -r $pip
        }
    } else {
        Write-Host "  Python no esta instalado todavia; corre esto despues." -ForegroundColor Yellow
    }
}

$npm = Join-Path $raiz 'npm-globales.txt'
if ((Test-Path $npm) -and (Get-Command npm -ErrorAction SilentlyContinue)) {
    Write-Host ""
    Write-Host "Paquetes globales de npm..." -ForegroundColor Cyan
    foreach ($linea in Get-Content $npm) {
        # El formato --parseable entrega rutas: el nombre del paquete va al final.
        if (-not $linea) { continue }
        $nombre = Split-Path $linea -Leaf
        if (-not $nombre -or $nombre -eq 'node_modules') { continue }
        Write-Host "  $nombre"
        if (-not $SoloMostrar) { & npm install -g $nombre }
    }
}

$ext = Join-Path $raiz 'vscode-extensiones.txt'
if ((Test-Path $ext) -and (Get-Command code -ErrorAction SilentlyContinue)) {
    Write-Host ""
    Write-Host "Extensiones de VS Code..." -ForegroundColor Cyan
    foreach ($linea in Get-Content $ext) {
        $nombre = ($linea -split '@')[0]
        if (-not $nombre) { continue }
        Write-Host "  $nombre"
        if (-not $SoloMostrar) { & code --install-extension $nombre --force }
    }
}

Write-Host ""
Write-Host "Listo. Los programas que no estaban en ningun gestor hay que instalarlos a mano:" -ForegroundColor Cyan
Write-Host "  revisa programas-instalados.json para la lista completa del equipo antiguo." -ForegroundColor DarkGray
"""


def script_instalador() -> str:
    """Punto de entrada del paquete en el computador nuevo."""
    return r"""# INSTALAR.ps1 - punto de entrada en el computador nuevo.
# Uso:  powershell -ExecutionPolicy Bypass -File .\INSTALAR.ps1
$ErrorActionPreference = 'Continue'
$raiz = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host ""
Write-Host "===============================================" -ForegroundColor Cyan
Write-Host " Migracion al computador nuevo" -ForegroundColor Cyan
Write-Host "===============================================" -ForegroundColor Cyan

$manifiesto = Join-Path $raiz 'manifiesto.json'
if (Test-Path $manifiesto) {
    $datos = Get-Content $manifiesto -Raw -Encoding UTF8 | ConvertFrom-Json
    Write-Host ""
    Write-Host "Paquete creado el $($datos.generado)"
    Write-Host "Equipo de origen: $($datos.equipo.host) (usuario $($datos.equipo.usuario))"
} else {
    Write-Host ""
    Write-Host "No se encontro manifiesto.json junto a este script." -ForegroundColor Red
    Write-Host "Ejecuta INSTALAR.ps1 desde adentro de la carpeta del paquete." -ForegroundColor Red
    Read-Host "Enter para salir"
    exit 1
}

# Paso 1: asegurar Python, que es lo que mueve el resto de la migracion.
$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    Write-Host ""
    Write-Host "Python no esta instalado. Instalandolo con winget..." -ForegroundColor Yellow
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Write-Host "winget tampoco esta disponible." -ForegroundColor Red
        Write-Host "Instala Python desde https://www.python.org/downloads/ y vuelve a ejecutar." -ForegroundColor Red
        Read-Host "Enter para salir"
        exit 1
    }
    & winget install --id Python.Python.3.12 -e --accept-package-agreements --accept-source-agreements

    # winget deja el PATH nuevo en el registro, pero esta sesion no lo ve aun.
    $env:Path = [Environment]::GetEnvironmentVariable('Path','Machine') + ';' +
                [Environment]::GetEnvironmentVariable('Path','User')
    $python = Get-Command python -ErrorAction SilentlyContinue
    if (-not $python) {
        Write-Host ""
        Write-Host "Python quedo instalado pero no visible en esta consola." -ForegroundColor Yellow
        Write-Host "Cierra esta ventana, abre otra y vuelve a ejecutar INSTALAR.ps1" -ForegroundColor Yellow
        Read-Host "Enter para salir"
        exit 1
    }
}
Write-Host ""
Write-Host "Python detectado: $(& python --version)" -ForegroundColor Green

$migrador = Join-Path $raiz 'herramienta\migrar.py'
if (-not (Test-Path $migrador)) {
    Write-Host "No se encontro la herramienta en $migrador" -ForegroundColor Red
    Write-Host "Puedes restaurar por partes con los scripts .ps1 de cada subcarpeta." -ForegroundColor Yellow
    Read-Host "Enter para salir"
    exit 1
}

# Paso 2: mostrar el estado del equipo antes de tocar nada.
Write-Host ""
Write-Host "--- Estado actual del equipo frente al paquete ---" -ForegroundColor Cyan
& python $migrador estado --paquete $raiz

# Paso 3: simulacion.
Write-Host ""
Write-Host "Ahora se hara una SIMULACION (no escribe nada)." -ForegroundColor Cyan
Read-Host "Enter para continuar"
& python $migrador importar --paquete $raiz --simular

# Paso 4: aplicar de verdad, solo si se confirma.
Write-Host ""
Write-Host "Si lo de arriba se ve bien, se aplicaran los cambios." -ForegroundColor Yellow
$respuesta = Read-Host "Escribe SI para aplicar (cualquier otra cosa cancela)"
if ($respuesta -eq 'SI') {
    & python $migrador importar --paquete $raiz
    Write-Host ""
    Write-Host "Migracion aplicada." -ForegroundColor Green
    Write-Host "Abre una consola nueva para que las variables de entorno tomen efecto." -ForegroundColor Cyan
    Write-Host ""
    Write-Host "Falta instalar los programas (toma bastante rato):" -ForegroundColor Cyan
    Write-Host "  powershell -ExecutionPolicy Bypass -File `"$raiz\programas\instalar-programas.ps1`"" -ForegroundColor White
    Write-Host ""
    Write-Host "Y si tenias tareas programadas, revisalas en el Programador de tareas." -ForegroundColor Cyan
} else {
    Write-Host "Cancelado. No se cambio nada." -ForegroundColor DarkGray
}

Read-Host "Enter para salir"
"""
