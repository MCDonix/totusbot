import html
import os
import re
from urllib.parse import urlparse

import requests
from playwright.sync_api import sync_playwright

# ------------------ CONFIGURACIÓN ------------------

# % de descuento mínimo que quieres detectar
DESCUENTO_MINIMO = 70

# Máximo de páginas a revisar por categoría (por si la paginación no se detiene sola)
PAGINAS_MAX = 30

# Cada tienda tiene:
#  - "urls": las categorías a revisar
#  - "paginacion": "click" (hay que clickear un botón "siguiente"/"mostrar más")
#                  o "url" (la paginación se hace agregando ?page=N a la URL)
SITIOS = [
    {
        "tienda": "Tottus",
        "paginacion": "click",
        "urls": [
            "https://www.tottus.com.pe/tottus-pe/lista/CATG48292/Tecnologia",
            "https://www.tottus.com.pe/tottus-pe/lista/CATG48293/Electrohogar",
            "https://www.tottus.com.pe/tottus-pe/lista/CATG48294/Dormitorio",
            "https://www.tottus.com.pe/tottus-pe/lista/CATG48296/Muebles",
            "https://www.tottus.com.pe/tottus-pe/lista/CATG48297/Jugueteria",
            "https://www.tottus.com.pe/tottus-pe/lista/CATG48299/Bazar",
        ],
    },
    {
        "tienda": "Falabella",
        "paginacion": "click",
        "urls": [
            "https://www.falabella.com.pe/falabella-pe/category/cat40793/Tecnologia",
            "https://www.falabella.com.pe/falabella-pe/category/cat760702/Telefonia",
            "https://www.falabella.com.pe/falabella-pe/category/cat40584/Electrohogar",
        ],
    },
    {
        "tienda": "Ripley",
        "paginacion": "url",
        "urls": [
            "https://simple.ripley.com.pe/electrohogar",
            "https://simple.ripley.com.pe/tecnologia",
        ],
    },
    {
        "tienda": "Metro",
        "paginacion": "click",
        "urls": [
            "https://www.metro.pe/electrohogar",
            "https://www.metro.pe/tecnologia",
        ],
    },
    {
        "tienda": "Plaza Vea",
        "paginacion": "click",
        "urls": [
            "https://www.plazavea.com.pe/tecnologia",
            "https://www.plazavea.com.pe/electrohogar",
        ],
    },
    {
        "tienda": "Coolbox",
        "paginacion": "click",
        "urls": [
            "https://www.coolbox.pe/celulares-y-accesorios",
            "https://www.coolbox.pe/audio",
            "https://www.coolbox.pe/computo",
            "https://www.coolbox.pe/gamer",
            "https://www.coolbox.pe/hogar/smart-home",
            "https://www.coolbox.pe/tv-y-video",
        ],
    },
    {
        "tienda": "Hiraoka",
        "paginacion": "click",
        "urls": [
            "https://hiraoka.com.pe/audio-y-musica/audio",
            "https://hiraoka.com.pe/computo-y-tablets/computadoras",
            "https://hiraoka.com.pe/celulares-y-telefonia/celulares",
            "https://hiraoka.com.pe/televisores/televisores",
            "https://hiraoka.com.pe/electrodomesticos",
            "https://hiraoka.com.pe/electrohogar/cocina-y-empotrables",
            "https://hiraoka.com.pe/electrohogar/lavado-y-limpieza",
            "https://hiraoka.com.pe/electrohogar/refrigeracion",
        ],
    },
]

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")


def enviar_telegram(mensaje):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️  Falta configurar TELEGRAM_TOKEN o TELEGRAM_CHAT_ID (ver README).")
        print(mensaje)
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        r = requests.post(
            url,
            data={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": mensaje,
                "parse_mode": "HTML",
            },
            timeout=15,
        )
        if r.status_code != 200:
            print(f"Telegram respondió {r.status_code}: {r.text}")
    except Exception as e:
        print(f"Error enviando a Telegram: {e}")


def ir_a_siguiente_pagina(page):
    """
    Intenta hacer clic en el botón/link de 'siguiente página' o 'mostrar más'.
    Varios sitios (Tottus, Falabella, Metro, Plaza Vea, Coolbox, Hiraoka) cargan
    más productos con JavaScript en vez de cambiar la URL. Se prueban varios
    selectores comunes; si el sitio real usa uno distinto, hay que inspeccionar
    el botón (clic derecho -> Inspeccionar) y agregar ese selector a la lista.
    """
    candidatos = [
        "button[aria-label*='iguiente' i]",
        "a[aria-label*='iguiente' i]",
        "[data-testid*='next' i]",
        "button:has-text('Siguiente')",
        "a:has-text('Siguiente')",
        "button:has-text('Mostrar más')",
        "button:has-text('Ver más')",
        "a:has-text('Ver más')",
        "a[rel='next']",
        "li.pagination-arrow-next a",
        "[class*='pagination'] [class*='next']",
        "[class*='paginator'] [class*='next']",
    ]
    for sel in candidatos:
        try:
            el = page.query_selector(sel)
            if el and el.is_visible():
                el.click()
                return True
        except Exception:
            continue
    return False


def extraer_productos(page, dominio_base):
    """
    Extrae productos de la página actual, sin depender de clases CSS específicas
    (cada tienda usa las suyas y cambian con el tiempo). Busca todos los <a> con
    precio ("S/ ...") y de ahí saca:
      - todos los precios "S/ ..." que aparecen en el bloque (el más bajo = precio
        de oferta, el más alto = precio de referencia/"antes")
      - el % de descuento: si la tienda ya lo muestra (ej: "-22%") se usa ese
        (el más alto, si hay varios niveles como en Ripley); si no lo muestra,
        se calcula a partir de los dos precios (ej: Coolbox)
    """
    productos = []
    vistos_href = set()
    enlaces = page.query_selector_all("a")

    for a in enlaces:
        try:
            href = a.get_attribute("href")
            if not href or href in vistos_href:
                continue

            texto = a.inner_text()
            if not texto or "S/" not in texto:
                continue

            precios_texto = re.findall(r"S/\.?\s*([\d,]+(?:\.\d+)?)", texto)
            precios = []
            for p in precios_texto:
                try:
                    valor = float(p.replace(",", ""))
                    if valor > 0:
                        precios.append(valor)
                except ValueError:
                    continue
            if len(precios) < 2:
                continue

            precio_oferta = min(precios)
            precio_normal = max(precios)
            if precio_normal <= precio_oferta:
                continue

            # % que la tienda ya calcula y muestra (puede haber más de uno,
            # ej: Ripley muestra un descuento general y uno extra con tarjeta)
            badges = re.findall(r"-(\d{1,3})\s*%", texto)
            if badges:
                descuento = max(int(b) for b in badges)
            else:
                descuento = round((1 - precio_oferta / precio_normal) * 100)

            if not (0 < descuento <= 95):
                continue

            # El nombre es el texto antes de que empiece la info de precio/envío
            lineas = [l.strip() for l in texto.split("\n") if l.strip()]
            nombre_partes = []
            for l in lineas:
                if (
                    l.startswith("S/")
                    or l.startswith("-")
                    or l.endswith("%")
                    or "Por " in l
                    or "Envío" in l
                    or "Recíbelo" in l
                    or l.lower() == "unidad"
                    or l in ("Patrocinado", "Vendedor destacado", "Cupón: SKYPERU")
                ):
                    break
                nombre_partes.append(l)
            nombre = " ".join(nombre_partes)[:150] if nombre_partes else "Producto sin nombre"

            link = href
            if link.startswith("/"):
                link = dominio_base + link

            vistos_href.add(href)
            productos.append(
                {
                    "nombre": nombre,
                    "link": link,
                    "precio_normal": precio_normal,
                    "precio_oferta": precio_oferta,
                    "descuento": descuento,
                }
            )
        except Exception as e:
            print(f"Error procesando un enlace: {e}")
            continue

    return productos


def revisar_categoria(page, base_url, dominio_base, tipo_paginacion):
    """Revisa todas las páginas de una categoría y devuelve los productos con descuento."""
    encontrados = []
    pagina = 1
    nombres_vistos = set()

    try:
        page.goto(base_url, timeout=45000, wait_until="domcontentloaded")
    except Exception as e:
        print(f"  Error abriendo {base_url}: {e}")
        return encontrados

    while pagina <= PAGINAS_MAX:
        if tipo_paginacion == "url" and pagina > 1:
            separador = "&" if "?" in base_url else "?"
            url_pagina = f"{base_url}{separador}page={pagina}"
            try:
                page.goto(url_pagina, timeout=45000, wait_until="domcontentloaded")
            except Exception as e:
                print(f"  Error abriendo página {pagina}: {e}")
                break

        page.wait_for_timeout(4000)  # deja que cargue el JS/productos
        for _ in range(4):
            page.mouse.wheel(0, 2000)
            page.wait_for_timeout(1000)

        try:
            productos = extraer_productos(page, dominio_base)
        except Exception as e:
            print(f"  Error extrayendo productos (página {pagina}): {e}")
            break

        print(f"  Página {pagina}: {len(productos)} productos con descuento detectados")

        nombres_pagina = {p["nombre"] for p in productos}
        nuevos = nombres_pagina - nombres_vistos
        if not productos or not nuevos:
            print("  -> Sin productos nuevos, se asume fin de la categoría.")
            break

        nombres_vistos |= nombres_pagina
        for prod in productos:
            if prod["descuento"] >= DESCUENTO_MINIMO:
                encontrados.append(prod)

        if pagina >= PAGINAS_MAX:
            break

        if tipo_paginacion == "click":
            avanzo = ir_a_siguiente_pagina(page)
            if not avanzo:
                print("  -> No se encontró botón de siguiente página, fin de la categoría.")
                break

        pagina += 1

    return encontrados


def revisar_todo():
    encontrados = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            )
        )

        for sitio in SITIOS:
            tienda = sitio["tienda"]
            dominio_base = f"{urlparse(sitio['urls'][0]).scheme}://{urlparse(sitio['urls'][0]).netloc}"

            for url in sitio["urls"]:
                print(f"[{tienda}] Revisando: {url}")
                try:
                    resultado = revisar_categoria(page, url, dominio_base, sitio["paginacion"])
                except Exception as e:
                    print(f"  Error inesperado en {url}: {e}")
                    resultado = []

                for prod in resultado:
                    prod["tienda"] = tienda
                encontrados.extend(resultado)

        browser.close()

    return encontrados


def main():
    ofertas = revisar_todo()

    if not ofertas:
        print("No se encontraron nuevas ofertas >= descuento mínimo.")
        return

    for oferta in ofertas:
        nombre_seguro = html.escape(oferta["nombre"])
        link = oferta["link"] or ""
        mensaje = (
            f"🔥 <b>{oferta['tienda']}: {oferta['descuento']}% OFF</b>\n"
            f"{nombre_seguro}\n"
            f"Antes: S/ {oferta['precio_normal']:.2f}\n"
            f"Ahora: S/ {oferta['precio_oferta']:.2f}\n"
        )
        if link:
            mensaje += f'<a href="{html.escape(link)}">🔗 Ver producto</a>'
        enviar_telegram(mensaje)
        print(mensaje)


if __name__ == "__main__":
    main()
