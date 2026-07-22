import html
import os
import re

import requests
from playwright.sync_api import sync_playwright

# ------------------ CONFIGURACIÓN ------------------

# % de descuento mínimo que quieres detectar
DESCUENTO_MINIMO = 40

# Categorías de Tottus a revisar cada vez que corre el bot (sin el número de página).
# El bot recorre automáticamente todas las páginas de cada una (ver PAGINAS_MAX abajo).
# Cómo conseguir más URLs: entra a https://www.tottus.com.pe, navega a una
# categoría, y copia la URL resultante (sin ningún "?page=" al final).
URLS = [
    "https://www.tottus.com.pe/tottus-pe/lista/CATG48292/Tecnologia",
    "https://www.tottus.com.pe/tottus-pe/lista/CATG48293/Electrohogar",
]

# Máximo de páginas a revisar por categoría (por si la paginación no se detiene sola)
PAGINAS_MAX = 30

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


def limpiar_precio(texto):
    """Extrae un precio tipo 'S/ 3,299.90' de un texto que puede traer basura pegada
    (ej: 'S/ 3,299UN\\n-33%'). Busca específicamente el patrón después de 'S/'
    e ignora todo lo demás (%, 'UN', saltos de línea, etc.)."""
    if not texto:
        return None
    match = re.search(r"S/\.?\s*([\d,]+(?:\.\d+)?)", texto)
    if not match:
        return None
    numero = match.group(1).replace(",", "")
    try:
        valor = float(numero)
        return valor if valor > 0 else None
    except ValueError:
        return None


def ir_a_siguiente_pagina(page):
    """
    Intenta hacer clic en el botón/link de 'siguiente página'. Tottus pagina
    con JavaScript (no cambia la URL), así que hay que clickear el botón.
    Se prueban varios selectores comunes porque no podemos inspeccionar
    el sitio en vivo; si ninguno funciona, revisa el botón real (clic derecho
    -> Inspeccionar sobre el número de página siguiente o la flecha ">") y
    agrega ese selector a la lista de abajo.
    """
    candidatos = [
        "button[aria-label*='iguiente' i]",
        "a[aria-label*='iguiente' i]",
        "[data-testid*='next' i]",
        "button:has-text('Siguiente')",
        "a:has-text('Siguiente')",
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


def extraer_productos(page):
    """
    Extrae productos de la página actual.

    En vez de depender de clases CSS específicas (que Tottus puede cambiar
    en cualquier momento), este método busca todos los enlaces <a> de la
    página, se queda con los que tienen precio (contienen "S/"), y lee:
      - el % de descuento que Tottus YA calcula y muestra (ej: "-22%")
      - el primer precio (precio de oferta/actual)
      - el último precio (precio de referencia/"antes", el más alto)
    Esto es más robusto que adivinar selectores de precio "normal" vs "oferta".
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

            # El % de descuento que Tottus ya muestra (ej: "-22%")
            match_pct = re.search(r"-(\d{1,3})\s*%", texto)
            if not match_pct:
                continue
            descuento = int(match_pct.group(1))
            if not (0 < descuento <= 95):
                continue

            # Todos los precios "S/ ..." que aparecen en el bloque del producto
            precios_texto = re.findall(r"S/\.?\s*([\d,]+(?:\.\d+)?)", texto)
            if len(precios_texto) < 2:
                continue

            precio_oferta = float(precios_texto[0].replace(",", ""))
            precio_normal = float(precios_texto[-1].replace(",", ""))
            if precio_normal <= precio_oferta:
                continue

            # El nombre es el texto antes de que empiece la info de precio/envío
            lineas = [l.strip() for l in texto.split("\n") if l.strip()]
            nombre_partes = []
            for l in lineas:
                if (
                    l.startswith("S/")
                    or l.startswith("-")
                    or l.endswith("%")
                    or "Por TOTTUS" in l
                    or "Envío" in l
                    or l.lower() == "unidad"
                    or l == "Patrocinado"
                ):
                    break
                nombre_partes.append(l)
            nombre = " ".join(nombre_partes)[:150] if nombre_partes else "Producto sin nombre"

            link = href
            if link.startswith("/"):
                link = "https://www.tottus.com.pe" + link

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


def revisar_tottus():
    encontrados = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            )
        )

        for base_url in URLS:
            pagina = 1
            nombres_vistos_en_categoria = set()

            print(f"Revisando: {base_url}")
            try:
                page.goto(base_url, timeout=45000, wait_until="domcontentloaded")
            except Exception as e:
                print(f"Error abriendo {base_url}: {e}")
                continue

            while pagina <= PAGINAS_MAX:
                page.wait_for_timeout(4000)  # deja que cargue el JS/productos

                # Scroll para forzar carga de productos (lazy loading)
                for _ in range(4):
                    page.mouse.wheel(0, 2000)
                    page.wait_for_timeout(1000)

                try:
                    productos = extraer_productos(page)
                except Exception as e:
                    print(f"Error extrayendo productos (página {pagina}): {e}")
                    break

                print(f"  Página {pagina}: {len(productos)} productos con descuento detectados")

                nombres_pagina = {p["nombre"] for p in productos}
                nuevos = nombres_pagina - nombres_vistos_en_categoria
                if not productos or not nuevos:
                    print("  -> Sin productos nuevos, se asume fin de la categoría.")
                    break

                nombres_vistos_en_categoria |= nombres_pagina

                for prod in productos:
                    if prod["descuento"] >= DESCUENTO_MINIMO:
                        encontrados.append(prod)

                if pagina >= PAGINAS_MAX:
                    break

                avanzo = ir_a_siguiente_pagina(page)
                if not avanzo:
                    print("  -> No se encontró botón de siguiente página, fin de la categoría.")
                    break

                pagina += 1

        browser.close()

    return encontrados


def main():
    ofertas = revisar_tottus()

    if not ofertas:
        print("No se encontraron nuevas ofertas >= descuento mínimo.")
        return

    for oferta in ofertas:
        nombre_seguro = html.escape(oferta["nombre"])
        link = oferta["link"] or ""
        mensaje = (
            f"🔥 <b>Oferta Tottus {oferta['descuento']}% OFF</b>\n"
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
