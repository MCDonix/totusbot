import html
import json
import os
import re
from pathlib import Path

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

SEEN_FILE = Path("seen.json")

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")


def cargar_vistos():
    if SEEN_FILE.exists():
        try:
            return set(json.loads(SEEN_FILE.read_text()))
        except Exception:
            return set()
    return set()


def guardar_vistos(vistos):
    # Limitamos el tamaño del archivo para que no crezca infinito
    lista = sorted(vistos)[-5000:]
    SEEN_FILE.write_text(json.dumps(lista))


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
    """Convierte 'S/ 199.90' -> 199.90. Devuelve None si el texto no es un precio
    (por ejemplo si trae '%', que indica que es una etiqueta de descuento, no un precio)."""
    if not texto:
        return None
    if "%" in texto:
        return None
    numero = re.sub(r"[^\d.]", "", texto.replace(",", ""))
    if not numero:
        return None
    try:
        valor = float(numero)
        return valor if valor > 0 else None
    except ValueError:
        return None


def extraer_productos(page):
    """
    Extrae productos de la página actual.

    IMPORTANTE: los selectores CSS de Tottus pueden cambiar con el tiempo.
    Si el script deja de encontrar productos, inspecciona (botón derecho ->
    Inspeccionar) una tarjeta de producto en tottus.falabella.com.pe y
    actualiza los selectores de abajo.
    """
    productos = []
    tarjetas = page.query_selector_all("[data-testid='pod']")
    if not tarjetas:
        tarjetas = page.query_selector_all(".pod")

    for tarjeta in tarjetas:
        try:
            nombre_el = tarjeta.query_selector(
                "[data-testid='pod-displaySubTitle'], .pod-subTitle, b.title-product"
            )
            link_el = tarjeta.query_selector("a")
            precio_normal_el = tarjeta.query_selector(
                "[data-normal-price], .normal-price, .prices-0, .lowest-price"
            )
            precio_oferta_el = tarjeta.query_selector(
                "[data-internet-price], .internet-price, .prices-1, .cmr-price, .best-price"
            )

            nombre = nombre_el.inner_text().strip() if nombre_el else "Producto sin nombre"
            link = link_el.get_attribute("href") if link_el else ""
            if link and link.startswith("/"):
                link = "https://www.tottus.com.pe" + link

            precio_normal = limpiar_precio(precio_normal_el.inner_text()) if precio_normal_el else None
            precio_oferta = limpiar_precio(precio_oferta_el.inner_text()) if precio_oferta_el else None

            if precio_normal and precio_oferta and precio_normal > precio_oferta:
                descuento = round((1 - precio_oferta / precio_normal) * 100, 1)
                # Descarta resultados imposibles (indican que se leyó mal algún precio)
                if 0 < descuento <= 95:
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
            print(f"Error procesando una tarjeta: {e}")
            continue

    return productos


def revisar_tottus():
    vistos = cargar_vistos()
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
            separador = "&" if "?" in base_url else "?"
            pagina = 1
            nombres_vistos_en_categoria = set()

            while pagina <= PAGINAS_MAX:
                # Página 1 es la URL tal cual; desde la 2 se agrega ?page=N
                url = base_url if pagina == 1 else f"{base_url}{separador}page={pagina}"
                print(f"Revisando: {url}")

                try:
                    page.goto(url, timeout=45000, wait_until="domcontentloaded")
                    page.wait_for_timeout(4000)  # deja que cargue el JS/productos

                    # Scroll para forzar carga de productos (lazy loading)
                    for _ in range(4):
                        page.mouse.wheel(0, 2000)
                        page.wait_for_timeout(1000)

                    productos = extraer_productos(page)
                    print(f"  -> {len(productos)} productos con descuento detectados")

                    # Si la página no trajo productos nuevos, asumimos que ya
                    # no hay más páginas (se repitió el contenido o llegó al final)
                    nombres_pagina = {p["nombre"] for p in productos}
                    if not productos or nombres_pagina.issubset(nombres_vistos_en_categoria):
                        print("  -> Sin productos nuevos, se asume fin de la paginación.")
                        break

                    nombres_vistos_en_categoria |= nombres_pagina

                    for prod in productos:
                        if prod["descuento"] >= DESCUENTO_MINIMO:
                            clave = f"{prod['nombre']}|{prod['precio_oferta']}"
                            if clave not in vistos:
                                encontrados.append(prod)
                                vistos.add(clave)

                    pagina += 1

                except Exception as e:
                    print(f"Error revisando {url}: {e}")
                    break

        browser.close()

    guardar_vistos(vistos)
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
