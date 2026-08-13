"""Generación de los scripts PowerShell que acompañan al paquete de migración.

Existen porque el computador nuevo llega vacío: puede no tener Python todavía,
y PowerShell siempre está. Con estos scripts se puede restaurar lo esencial sin
depender de nada más.
"""

from __future__ import annotations

CABECERA = """# Generado por el migrador. Ejecutar desde PowerShell.
$ErrorActionPreference = 'Stop'
$raiz = Split-Path -Parent $MyInvocation.MyCommand.Path
"""


def script_variables(variables_usuario: dict, variables_sistema: dict) -> str:
    """Restaura variables de entorno leyendo los JSON del paquete."""
    return CABECERA + r"""
param(
    [switch]$IncluirSistema,
    [switch]$SoloMostrar
)

function Restaurar-Variables {
    param($Archivo, $Ambito)

    if (-not (Test-Path $Archivo)) {
        Write-Host "  (sin archivo $Archivo, se omite)" -ForegroundColor DarkGray
        return
    }
    $datos = Get-Content $Archivo -Raw -Encoding UTF8 | ConvertFrom-Json
    $rutas = @('PATH','PYTHONPATH','PSMODULEPATH','CLASSPATH','INCLUDE','LIB')

    foreach ($propiedad in $datos.PSObject.Properties) {
        $nombre = $propiedad.Name
        $valor  = $propiedad.Value.valor

        if ($rutas -contains $nombre.ToUpper()) {
            # Las variables de rutas se fusionan: se conserva lo del equipo nuevo
            # y se agrega solo lo que falta del equipo antiguo.
            $actual = [Environment]::GetEnvironmentVariable($nombre, $Ambito)
            $existentes = @()
            if ($actual) { $existentes = $actual -split ';' | Where-Object { $_ -ne '' } }
            $agregar = @()
            foreach ($entrada in ($valor -split ';')) {
                if ($entrada -eq '') { continue }
                if ($existentes -notcontains $entrada) { $agregar += $entrada }
            }
            if ($agregar.Count -eq 0) {
                Write-Host "  = $nombre sin cambios" -ForegroundColor DarkGray
                continue
            }
            $nuevo = (($existentes + $agregar) -join ';')
            Write-Host "  + $nombre : se agregan $($agregar.Count) ruta(s)" -ForegroundColor Green
            foreach ($a in $agregar) {
                $existe = Test-Path $([Environment]::ExpandEnvironmentVariables($a))
                if (-not $existe) { Write-Host "      ojo: '$a' no existe en este equipo" -ForegroundColor Yellow }
            }
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

Write-Host "`nVariables de usuario" -ForegroundColor Cyan
Restaurar-Variables (Join-Path $raiz 'usuario.json') 'User'

if ($IncluirSistema) {
    $esAdmin = ([Security.Principal.WindowsPrincipal] `
        [Security.Principal.WindowsIdentity]::GetCurrent()
        ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    if (-not $esAdmin) {
        Write-Host "`nLas variables de sistema requieren PowerShell como administrador." -ForegroundColor Red
    } else {
        Write-Host "`nVariables de sistema" -ForegroundColor Cyan
        Restaurar-Variables (Join-Path $raiz 'sistema.json') 'Machine'
    }
}

if ($SoloMostrar) {
    Write-Host "`nModo simulacion: no se escribio nada." -ForegroundColor Cyan
} else {
    Write-Host "`nListo. Abre una consola nueva para que los cambios tomen efecto." -ForegroundColor Cyan
}
"""


def script_tareas(tareas: list[dict]) -> str:
    """Recrea las tareas programadas desde los XML exportados."""
    return CABECERA + r"""
param([switch]$SoloMostrar)

$xmls = Get-ChildItem -Path $raiz -Filter '*.xml' -ErrorAction SilentlyContinue
if (-not $xmls) {
    Write-Host "No hay tareas que restaurar." -ForegroundColor DarkGray
    return
}

$usuario = "$env:USERDOMAIN\$env:USERNAME"
Write-Host "Se restauraran $($xmls.Count) tarea(s) programada(s) como $usuario`n" -ForegroundColor Cyan

foreach ($xml in $xmls) {
    $nombre = $xml.BaseName
    Write-Host "  $nombre"
    if ($SoloMostrar) { continue }

    # El XML trae rutas y usuario del equipo antiguo: si algo no calza,
    # schtasks lo informa y seguimos con la siguiente tarea.
    $salida = & schtasks /create /tn $nombre /xml $xml.FullName /f 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Host "    fallo: $salida" -ForegroundColor Yellow
        Write-Host "    revisa la ruta del programa y la cuenta de ejecucion" -ForegroundColor DarkGray
    } else {
        Write-Host "    creada" -ForegroundColor Green
    }
}

Write-Host "`nRevisa las tareas en el Programador de tareas antes de confiar en ellas." -ForegroundColor Cyan
"""


def script_programas(inventario: dict) -> str:
    """Reinstala programas con winget y los demás gestores."""
    return CABECERA + r"""
param([switch]$SoloMostrar)

$winget = Join-Path $raiz 'winget.json'
if (Test-Path $winget) {
    Write-Host "`nReinstalando programas con winget..." -ForegroundColor Cyan
    if ($SoloMostrar) {
        $lista = Get-Content $winget -Raw | ConvertFrom-Json
        foreach ($fuente in $lista.Sources) {
            foreach ($paquete in $fuente.Packages) { Write-Host "  $($paquete.PackageIdentifier)" }
        }
    } else {
        # --ignore-unavailable evita que un paquete retirado detenga todo lo demas.
        & winget import -i $winget --accept-package-agreements --accept-source-agreements --ignore-unavailable
    }
} else {
    Write-Host "`nNo hay winget.json en el paquete." -ForegroundColor DarkGray
}

$choco = Join-Path $raiz 'chocolatey.txt'
if ((Test-Path $choco) -and (Get-Command choco -ErrorAction SilentlyContinue)) {
    Write-Host "`nPaquetes de Chocolatey..." -ForegroundColor Cyan
    foreach ($linea in Get-Content $choco) {
        $nombre = ($linea -split '\|')[0]
        if (-not $nombre) { continue }
        Write-Host "  $nombre"
        if (-not $SoloMostrar) { & choco install $nombre -y --limit-output }
    }
}

$pip = Join-Path $raiz 'pip-requirements.txt'
if (Test-Path $pip) {
    Write-Host "`nPaquetes de Python (pip)..." -ForegroundColor Cyan
    if (Get-Command python -ErrorAction SilentlyContinue) {
        if (-not $SoloMostrar) { & python -m pip install -r $pip }
        else { Write-Host "  $(( Get-Content $pip ).Count) paquete(s)" }
    } else {
        Write-Host "  Python no esta instalado todavia; corre esto despues." -ForegroundColor Yellow
    }
}

$npm = Join-Path $raiz 'npm-globales.txt'
if ((Test-Path $npm) -and (Get-Command npm -ErrorAction SilentlyContinue)) {
    Write-Host "`nPaquetes globales de npm..." -ForegroundColor Cyan
    foreach ($linea in Get-Content $npm) {
        # El formato --parseable entrega rutas: el nombre del paquete es lo ultimo.
        $nombre = Split-Path $linea -Leaf
        if (-not $nombre -or $nombre -eq 'node_modules') { continue }
        Write-Host "  $nombre"
        if (-not $SoloMostrar) { & npm install -g $nombre }
    }
}

$ext = Join-Path $raiz 'vscode-extensiones.txt'
if ((Test-Path $ext) -and (Get-Command code -ErrorAction SilentlyContinue)) {
    Write-Host "`nExtensiones de VS Code..." -ForegroundColor Cyan
    foreach ($linea in Get-Content $ext) {
        $nombre = ($linea -split '@')[0]
        if (-not $nombre) { continue }
        Write-Host "  $nombre"
        if (-not $SoloMostrar) { & code --install-extension $nombre --force }
    }
}

Write-Host "`nListo." -ForegroundColor Cyan
"""


def script_instalador() -> str:
    """Punto de entrada del paquete en el computador nuevo."""
    return r"""# INSTALAR.ps1 - punto de entrada en el computador nuevo.
# Uso: clic derecho > "Ejecutar con PowerShell", o desde una consola:
#   powershell -ExecutionPolicy Bypass -File .\INSTALAR.ps1
$ErrorActionPreference = 'Stop'
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
}

# Paso 1: asegurar Python, que es lo que mueve el resto de la migracion.
$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    Write-Host ""
    Write-Host "Python no esta instalado. Instalandolo con winget..." -ForegroundColor Yellow
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Write-Host "winget tampoco esta disponible." -ForegroundColor Red
        Write-Host "Instala Python a mano desde https://www.python.org/downloads/ y vuelve a ejecutar." -ForegroundColor Red
        Read-Host "Enter para salir"
        exit 1
    }
    & winget install --id Python.Python.3.12 -e --accept-package-agreements --accept-source-agreements
    # winget deja el nuevo PATH en el registro, pero esta sesion no lo ve todavia.
    $env:Path = [Environment]::GetEnvironmentVariable('Path','Machine') + ';' +
                [Environment]::GetEnvironmentVariable('Path','User')
    $python = Get-Command python -ErrorAction SilentlyContinue
    if (-not $python) {
        Write-Host "Python quedo instalado pero no visible en esta consola." -ForegroundColor Yellow
        Write-Host "Cierra esta ventana, abre otra y vuelve a ejecutar INSTALAR.ps1" -ForegroundColor Yellow
        Read-Host "Enter para salir"
        exit 1
    }
}
Write-Host ""
Write-Host "Python detectado: $(& python --version)" -ForegroundColor Green

# Paso 2: mostrar el estado del equipo antes de tocar nada.
$migrador = Join-Path $raiz 'herramienta\migrar.py'
if (-not (Test-Path $migrador)) {
    Write-Host "No se encontro la herramienta en $migrador" -ForegroundColor Red
    Write-Host "Puedes restaurar por partes con los scripts .ps1 de cada subcarpeta." -ForegroundColor Yellow
    Read-Host "Enter para salir"
    exit 1
}

Write-Host ""
Write-Host "--- Estado actual del equipo frente al paquete ---" -ForegroundColor Cyan
& python $migrador estado --paquete $raiz

Write-Host ""
Write-Host "Ahora se hara una SIMULACION (no escribe nada)." -ForegroundColor Cyan
Read-Host "Enter para continuar"
& python $migrador importar --paquete $raiz --simular

Write-Host ""
Write-Host "Si lo de arriba se ve bien, se aplicaran los cambios de verdad." -ForegroundColor Yellow
$respuesta = Read-Host "Escribe SI para aplicar (cualquier otra cosa cancela)"
if ($respuesta -eq 'SI') {
    & python $migrador importar --paquete $raiz
    Write-Host ""
    Write-Host "Migracion aplicada. Abre una consola nueva para ver las variables." -ForegroundColor Green
    Write-Host "Los programas se instalan aparte con: programas\instalar-programas.ps1" -ForegroundColor Cyan
} else {
    Write-Host "Cancelado. No se cambio nada." -ForegroundColor DarkGray
}

Read-Host "Enter para salir"
"""
