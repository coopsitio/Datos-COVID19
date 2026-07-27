# -*- coding: utf-8 -*-
"""
INSTALADOR AUTOMATICO del parche "check list nuevo" para firmas.py.

Copia este archivo (y Aplicar_Parche.bat) a la carpeta donde esta firmas.py
(la misma de Firmar_Ciclo.bat) y haz doble clic en Aplicar_Parche.bat.

Que hace, solo en firmas.py:
  1. Crea un respaldo (firmas_backup_AAAAMMDD_HHMMSS.py).
  2. Renombra tu funcion rect_checklist -> rect_checklist_texto.
  3. Agrega al final el codigo nuevo (deteccion de linea de firma + fraccion).
  4. Verifica que el archivo compila y que rect_checklist existe.
     Si algo falla, RESTAURA el respaldo automaticamente.

Es idempotente: si ya esta aplicado, avisa y no hace nada.
Despues de aplicarlo, cierra y vuelve a abrir "Servidor_Celular.bat".
"""
import io
import os
import re
import sys
import shutil
import datetime as dt

MARCADOR = "PARCHE CHECK LIST NUEVO (sin capa de texto)"

PATCH_CODE = r'''

# ============================================================================
# PARCHE CHECK LIST NUEVO (sin capa de texto)
# El check list del sistema nuevo viene 100% rasterizado: page.search_for()
# no encuentra config.FIRMA_LABEL y la firma fallaba con "no encontro la
# celda". rect_checklist ahora intenta: 1) el ancla al texto de siempre
# (rect_checklist_texto, la funcion original), 2) deteccion grafica de la
# linea de firma (in-process con fitz, sin OCR externo -> a prueba de
# antivirus), 3) posicion fija por fraccion de pagina del layout nuevo.
# ============================================================================
import fitz as _fitz_parche
import config as _config_parche

# Celda "Jefe de Facturacion" del layout nuevo como fraccion de pagina
# (medido sobre CHECK_LIST_IMPRESION_20260727_C03_CASABLANCA, 792x612).
FIRMA_CHECKLIST_NUEVO = dict(x0=0.661, y0=0.771, x1=0.787, y1=0.856)

_LINEA_ZOOM     = 2.0    # resolucion del render (px por pt)
_LINEA_GRIS_MAX = 120    # umbral: pixel mas oscuro que esto = tinta
_LINEA_MIN_PT   = 100.0  # largo minimo de una linea de firma (pt)
_LINEA_MAX_FR   = 0.45   # largo maximo (fraccion del ancho): descarta bordes
                         # de tabla, que van casi de lado a lado
_LINEA_ZONA_FR  = 0.65   # solo se busca bajo este alto de pagina
_LINEA_GAP      = 4.0    # pt entre el borde inferior de la firma y la linea


def _lineas_firma(page):
    """Lineas horizontales 'de firma' en la zona inferior: [(x0, x1, y) en pt]."""
    pix = page.get_pixmap(matrix=_fitz_parche.Matrix(_LINEA_ZOOM, _LINEA_ZOOM),
                          colorspace=_fitz_parche.csGRAY)
    w, h, s = pix.width, pix.height, pix.samples
    ancho_pt = page.rect.width
    lineas = []
    for y in range(int(h * _LINEA_ZONA_FR), h):   # fila a fila (puede medir 1 px)
        fila = s[y * w:(y + 1) * w]
        x = 0
        while x < w:
            if fila[x] < _LINEA_GRIS_MAX:
                x0 = x
                while x < w and fila[x] < _LINEA_GRIS_MAX:
                    x += 1
                largo = (x - x0) / _LINEA_ZOOM
                if _LINEA_MIN_PT <= largo <= _LINEA_MAX_FR * ancho_pt:
                    lineas.append((x0 / _LINEA_ZOOM, x / _LINEA_ZOOM, y / _LINEA_ZOOM))
            else:
                x += 1
    unicas = []   # una linea real abarca varias filas -> fusionar
    for x0, x1, y in lineas:
        for i, (u0, u1, uy) in enumerate(unicas):
            if abs(y - uy) <= 3 and abs(x0 - u0) <= 6 and abs(x1 - u1) <= 6:
                unicas[i] = (min(x0, u0), max(x1, u1), max(y, uy))
                break
        else:
            unicas.append((x0, x1, y))
    return unicas


def rect_checklist_nuevo(page):
    """Celda de firma en el check list rasterizado (linea; si no, fraccion)."""
    lineas = _lineas_firma(page)
    if lineas:
        # la linea MAS A LA DERECHA es la del Jefe de Facturacion
        # (la izquierda es la del explotador)
        x0, x1, y = max(lineas, key=lambda ln: (ln[0] + ln[1]) / 2.0)
        cx = (x0 + x1) / 2.0
        y1 = y - _LINEA_GAP
        return _fitz_parche.Rect(cx - _config_parche.FIRMA_W / 2.0,
                                 y1 - _config_parche.FIRMA_H,
                                 cx + _config_parche.FIRMA_W / 2.0, y1)
    f, r = FIRMA_CHECKLIST_NUEVO, page.rect
    return _fitz_parche.Rect(r.width * f["x0"], r.height * f["y0"],
                             r.width * f["x1"], r.height * f["y1"])


def rect_checklist(page):
    """Celda de firma del check list: texto (formato antiguo); si falla, nuevo."""
    try:
        r = rect_checklist_texto(page)
        if r:
            return r
    except Exception:
        pass
    return rect_checklist_nuevo(page)
'''


def _leer(path):
    for enc in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            with io.open(path, "r", encoding=enc) as f:
                return f.read(), enc
        except UnicodeDecodeError:
            continue
    raise RuntimeError("no pude leer %s con utf-8 ni latin-1" % path)


def main():
    aqui = os.path.dirname(os.path.abspath(__file__))
    os.chdir(aqui)
    destino = os.path.join(aqui, "firmas.py")

    print("=" * 60)
    print("  PARCHE: firma del CHECK LIST nuevo (sin capa de texto)")
    print("=" * 60)

    if not os.path.exists(destino):
        print("\n[ERROR] No encuentro firmas.py en esta carpeta:\n  %s" % aqui)
        print("Copia aplicar_parche.py y Aplicar_Parche.bat a la carpeta")
        print("donde esta firmas.py (la misma de Firmar_Ciclo.bat).")
        return 1

    src, enc = _leer(destino)

    if MARCADOR in src or "rect_checklist_texto" in src:
        print("\nEl parche YA esta aplicado. No hay nada que hacer.")
        print("(Si el servidor del celular sigue abierto desde antes,")
        print(" cierralo y vuelve a abrir Servidor_Celular.bat.)")
        return 0

    if not re.search(r"def\s+rect_checklist\s*\(", src):
        print("\n[ERROR] No encontre 'def rect_checklist(' en firmas.py.")
        print("No se modifico nada. Revisa que este sea el firmas.py correcto.")
        return 1

    # 1) respaldo
    backup = os.path.join(aqui, "firmas_backup_%s.py"
                          % dt.datetime.now().strftime("%Y%m%d_%H%M%S"))
    shutil.copy2(destino, backup)
    print("\n1. Respaldo creado: %s" % os.path.basename(backup))

    # 2) renombrar la funcion original (solo la definicion)
    nuevo = re.sub(r"def\s+rect_checklist\s*\(", "def rect_checklist_texto(",
                   src, count=1)
    print("2. rect_checklist renombrada a rect_checklist_texto")

    # 3) agregar el codigo nuevo al final
    nuevo = nuevo.rstrip() + "\n" + PATCH_CODE
    print("3. Codigo nuevo agregado al final de firmas.py")

    # 4) escribir y verificar (si falla, restaurar)
    with io.open(destino, "w", encoding=enc) as f:
        f.write(nuevo)
    try:
        compile(nuevo, destino, "exec")           # sintaxis
        sys.path.insert(0, aqui)
        import importlib
        import firmas                             # importa de verdad
        importlib.reload(firmas)
        for fn in ("rect_checklist", "rect_checklist_texto", "rect_checklist_nuevo"):
            if not hasattr(firmas, fn):
                raise RuntimeError("falta %s tras el parche" % fn)
    except Exception as e:
        shutil.copy2(backup, destino)
        print("\n[ERROR] La verificacion fallo (%s)." % e)
        print("Se RESTAURO tu firmas.py original desde el respaldo.")
        return 1
    print("4. Verificado: firmas.py compila y rect_checklist responde")

    print("\n" + "=" * 60)
    print("  LISTO. El parche quedo aplicado.")
    print("  -> Cierra y vuelve a abrir Servidor_Celular.bat")
    print("     (y/o usa Firmar_Ciclo.bat normalmente).")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
