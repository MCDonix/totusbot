import json
import os
import re
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

# ------------------ CONFIGURACIÓN ------------------

# % de descuento mínimo que quieres detectar
DESCUENTO_MINIMO = 80

# URLs de Tottus a revisar cada vez que corre el bot.
# Puedes agregar/quitar categorías o búsquedas según lo que te interese.
# Cómo conseguir más URLs: entra a https://tottus.falabella.com.pe,
# navega a una categoría o usa el buscador, y copia la URL resultante.
URLS = [
    "https://tottus.falabella.com.pe/tottus-pe/search?Ntt=ofertas",
    "https://tottus.falabella.com.pe/tottus-pe/category/cat40057/Despensa",
    "https://tottus.falabella.com.pe/tottus-pe/category/cat70103/Bebidas",
    "https://tottus.falabella.com.pe/tottus-pe/category/cat3170117/Electro",
]

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
    """Convierte 'S/ 199.90' -> 199.90"""
    if not texto:
        return None
    numero = re.sub(r"[^\d.]", "", texto.replace(",", ""))
    try:
        return float(numero)
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
                link = "https://tottus.falabella.com.pe" + link

            precio_normal = limpiar_precio(precio_normal_el.inner_text()) if precio_normal_el else None
            precio_oferta = limpiar_precio(precio_oferta_el.inner_text()) if precio_oferta_el else None

            if precio_normal and precio_oferta and precio_normal > precio_oferta:
                descuento = round((1 - precio_oferta / precio_normal) * 100, 1)
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

        for url in URLS:
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

                for prod in productos:
                    if prod["descuento"] >= DESCUENTO_MINIMO:
                        clave = f"{prod['nombre']}|{prod['precio_oferta']}"
                        if clave not in vistos:
                            encontrados.append(prod)
                            vistos.add(clave)

            except Exception as e:
                print(f"Error revisando {url}: {e}")
                continue

        browser.close()

    guardar_vistos(vistos)
    return encontrados


def main():
    ofertas = revisar_tottus()

    if not ofertas:
        print("No se encontraron nuevas ofertas >= descuento mínimo.")
        return

    for oferta in ofertas:
        mensaje = (
            f"🔥 <b>Oferta Tottus {oferta['descuento']}% OFF</b>\n"
            f"{oferta['nombre']}\n"
            f"Antes: S/ {oferta['precio_normal']:.2f}\n"
            f"Ahora: S/ {oferta['precio_oferta']:.2f}\n"
            f"{oferta['link']}"
        )
        enviar_telegram(mensaje)
        print(mensaje)


if __name__ == "__main__":
    main()
