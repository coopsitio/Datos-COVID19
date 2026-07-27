# -*- coding: utf-8 -*-
"""
PARCHE para firmas.py — check list NUEVO (otro sistema, sin capa de texto).

PROBLEMA: el check list nuevo (CHECK_LIST_IMPRESION_*, carta apaisada 792x612)
viene 100% rasterizado: no tiene texto extraible. Por eso el ancla al texto
"Jefe de facturaci" (config.FIRMA_LABEL) no encuentra nada y la firma desde el
celular termina en "no encontro la celda para la firma".

COMO INSTALAR (2 pasos, solo en firmas.py):
  1. Renombra tu funcion actual  rect_checklist  a  rect_checklist_texto
     (no le cambies nada mas).
  2. Pega TODO el codigo de este archivo al final de firmas.py.

Nada mas cambia: firmar_ciclo.py y servidor.py siguen usando
firmas.rect_checklist y ahora resuelve asi:
  1) TEXTO   : tu logica antigua (rect_checklist_texto). Sigue cubriendo el
               formato antiguo, y tambien el nuevo si algun dia trae texto.
  2) LINEA   : rasteriza el tercio inferior de la pagina y detecta las lineas
               de firma dibujadas; toma la MAS A LA DERECHA (Jefe de
               Facturacion; la izquierda es del explotador) y centra la firma
               sobre ella. Todo en-proceso con fitz: sin OCR ni procesos
               externos, a prueba de antivirus igual que el resto de la firma.
  3) FRACCION: si tampoco hay lineas, usa la posicion fija medida del layout
               nuevo (fraccion de pagina, sirve aunque cambie el tamano).

Verificado contra CHECK_LIST_IMPRESION_20260727_C03_CASABLANCA.pdf:
lineas detectadas en (127-309, y=526) y (483-665, y=526) pt; rect resultante
(524, 470, 624, 522) = centrado sobre la linea del Jefe de Facturacion.
"""
import fitz
import config

# Fallback final: celda "Jefe de Facturacion" del layout nuevo, como fraccion
# de pagina (medido sobre el check list CASABLANCA C03 de 2026-07, 792x612).
FIRMA_CHECKLIST_NUEVO = dict(x0=0.661, y0=0.771, x1=0.787, y1=0.856)

# Deteccion de lineas de firma en el formato rasterizado
_LINEA_ZOOM     = 2.0    # resolucion del render (px por pt)
_LINEA_GRIS_MAX = 120    # umbral: pixel mas oscuro que esto = tinta
_LINEA_MIN_PT   = 100.0  # largo minimo de una linea de firma (pt)
_LINEA_MAX_FR   = 0.45   # largo maximo como fraccion del ancho (descarta
                         # bordes de tabla, que van casi de lado a lado)
_LINEA_ZONA_FR  = 0.65   # solo se busca bajo este alto de pagina
_LINEA_GAP      = 4.0    # pt entre el borde inferior de la firma y la linea


def _lineas_firma(page):
    """Lineas horizontales 'de firma' en la zona inferior: [(x0, x1, y) en pt]."""
    pix = page.get_pixmap(matrix=fitz.Matrix(_LINEA_ZOOM, _LINEA_ZOOM),
                          colorspace=fitz.csGRAY)
    w, h, s = pix.width, pix.height, pix.samples
    ancho_pt = page.rect.width
    lineas = []
    for y in range(int(h * _LINEA_ZONA_FR), h):      # fila a fila (la linea puede medir 1 px)
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
    # una linea real puede aparecer en varias filas consecutivas -> fusionar
    unicas = []
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
        x0, x1, y = max(lineas, key=lambda ln: (ln[0] + ln[1]) / 2.0)
        cx = (x0 + x1) / 2.0
        y1 = y - _LINEA_GAP
        return fitz.Rect(cx - config.FIRMA_W / 2.0, y1 - config.FIRMA_H,
                         cx + config.FIRMA_W / 2.0, y1)
    f, r = FIRMA_CHECKLIST_NUEVO, page.rect
    return fitz.Rect(r.width * f["x0"], r.height * f["y0"],
                     r.width * f["x1"], r.height * f["y1"])


def rect_checklist(page):
    """Celda de firma del check list: primero el ancla al texto (formato
    antiguo, tu funcion original renombrada); si falla, el formato nuevo."""
    try:
        r = rect_checklist_texto(page)
        if r:
            return r
    except Exception:
        pass
    return rect_checklist_nuevo(page)
