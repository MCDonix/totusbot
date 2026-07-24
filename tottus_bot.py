import html
import json
import os
import re
import time
from pathlib import Path
from urllib.parse import urlparse

import requests
from playwright.sync_api import sync_playwright

# ------------------ CONFIGURACIÓN ------------------

# % de descuento mínimo que quieres detectar
DESCUENTO_MINIMO = 60

# Máximo de páginas a revisar por categoría
PAGINAS_MAX = 10

# Tiempo máximo total (en segundos) para toda la corrida. Si se llega a este
# límite, el bot corta ahí mismo y envía lo que ya encontró, en vez de seguir
# sin parar. 600 = 10 minutos, dejando margen antes de la próxima corrida
# (que es cada 15 minutos).
TIEMPO_MAXIMO_SEGUNDOS = 600

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
        "espera_extra_ms": 6000,  # hiraoka puede tener una verificación anti-bot inicial
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

SEEN_FILE = Path("seen.json")


def cargar_vistos():
    if SEEN_FILE.exists():
        try:
            return set(json.loads(SEEN_FILE.read_text()))
        except Exception:
            return set()
    return set()


def guardar_vistos(vistos):
    # Limitamos el tamaño del archivo para que no crezca infinito
    lista = sorted(vistos)[-8000:]
    SEEN_FILE.write_text(json.dumps(lista))


def normalizar_link(link):
    """Quita parámetros de query (?...) y ancla (#...) del link. Algunas tiendas
    (ej: Falabella) agregan parámetros de tracking/posición que cambian en cada
    carga aunque sea el mismo producto, lo que rompía la detección de duplicados."""
    if not link:
        return link
    return link.split("?")[0].split("#")[0]


def clave_oferta(oferta):
    """Clave única por producto + tienda + % de descuento + precio de oferta.
    Si cualquiera de los dos cambia (el % o el precio), la clave cambia y el
    bot vuelve a avisar. Si todo sigue igual, no se repite."""
    link_normalizado = normalizar_link(oferta["link"])
    return f"{oferta['tienda']}|{link_normalizado}|{oferta['descuento']}|{oferta['precio_oferta']}"


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


def obtener_bloques_producto(page):
    """
    Devuelve una lista de {href, texto} para cada posible producto de la página.

    En algunos sitios (Tottus, Falabella) el precio está dentro del mismo <a>
    que envuelve el producto. En otros, el precio puede estar en un elemento
    "hermano" fuera del <a> (ej: el link solo envuelve la imagen/nombre).
    Para cubrir ambos casos: para cada <a href>, si su propio texto no trae
    "S/", se sube hasta 3 niveles en el HTML buscando un contenedor que sí
    tenga el precio (probablemente la tarjeta completa del producto).
    """
    return page.evaluate(
        """
        () => {
            const resultados = [];
            const vistos = new Set();
            const anchors = Array.from(document.querySelectorAll('a[href]'));
            for (const a of anchors) {
                if (!a.href || vistos.has(a.href)) continue;
                let contenedor = a;
                let texto = a.innerText || '';
                let nivel = 0;
                while (nivel < 3 && !texto.includes('S/')) {
                    if (!contenedor.parentElement) break;
                    contenedor = contenedor.parentElement;
                    texto = contenedor.innerText || '';
                    nivel++;
                }
                if (!texto.includes('S/')) continue;
                vistos.add(a.href);
                resultados.push({href: a.getAttribute('href'), texto: texto});
            }
            return resultados;
        }
        """
    )


def es_link_de_producto(link, dominio_base):
    """
    Valida que el link sea realmente de un producto y no un enlace vacío,
    de categoría, o de un filtro capturado por error.
    """
    if not link or not link.startswith("http"):
        return False
    if link in ("#", "javascript:void(0)"):
        return False

    ruta = link[len(dominio_base):] if link.startswith(dominio_base) else urlparse(link).path
    if len(ruta.strip("/")) < 5:
        return False

    # Falabella, Tottus y Sodimac comparten la misma plataforma: sus URLs de
    # producto reales siempre incluyen "/product/" en la ruta. Si no lo tiene,
    # es casi seguro que se capturó un link de categoría/filtro por error.
    if any(marca in dominio_base for marca in ("falabella", "tottus", "sodimac")):
        return "/product/" in link

    return True


def extraer_productos(page, dominio_base):
    """
    Extrae productos de la página actual, sin depender de clases CSS específicas
    (cada tienda usa las suyas y cambian con el tiempo). Para cada bloque
    (ver obtener_bloques_producto) saca:
      - todos los precios "S/ ..." que aparecen (ignorando cuotas/mensualidades,
        que no son el precio real del producto)
      - el % de descuento: si la tienda ya lo muestra (ej: "-22%") se usa ese
        (el más alto, si hay varios niveles como en Ripley); si no lo muestra,
        se calcula a partir de los dos precios (ej: Coolbox)
    """
    productos = []
    vistos_nombre_precio = set()
    bloques = obtener_bloques_producto(page)

    for bloque in bloques:
        try:
            href = bloque.get("href")
            texto_original = bloque.get("texto") or ""
            if not href or not texto_original or "S/" not in texto_original:
                continue

            # Quita líneas de cuotas/mensualidades: no son el precio real
            # (ej: "Desde S/ 232.83 al mes o en 3 cuotas sin intereses")
            lineas_texto = texto_original.split("\n")
            lineas_texto = [
                l for l in lineas_texto
                if not re.search(r"cuota|al mes|/mes|mensual", l, re.IGNORECASE)
            ]
            texto = "\n".join(lineas_texto)
            if "S/" not in texto:
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
            lineas = [l.strip() for l in lineas_texto if l.strip()]
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

            # Evita duplicados cuando dos enlaces distintos (ej: imagen y título)
            # suben al mismo contenedor y generan el mismo producto dos veces
            clave = f"{nombre}|{precio_oferta}|{precio_normal}"
            if clave in vistos_nombre_precio:
                continue
            vistos_nombre_precio.add(clave)

            link = href
            if link.startswith("/"):
                link = dominio_base + link

            if not es_link_de_producto(link, dominio_base):
                continue

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
            print(f"Error procesando un bloque: {e}")
            continue

    return productos


def revisar_categoria(page, base_url, dominio_base, tipo_paginacion, tiempo_limite, espera_extra_ms=0):
    """Revisa todas las páginas de una categoría y devuelve los productos con descuento.
    Corta antes si se llega a `tiempo_limite` (timestamp de time.time())."""
    encontrados = []
    pagina = 1
    nombres_vistos = set()

    try:
        page.goto(base_url, timeout=45000, wait_until="domcontentloaded")
        if espera_extra_ms:
            page.wait_for_timeout(espera_extra_ms)
    except Exception as e:
        print(f"  Error abriendo {base_url}: {e}")
        return encontrados

    while pagina <= PAGINAS_MAX:
        if time.time() > tiempo_limite:
            print("  -> Tiempo máximo alcanzado, se corta esta categoría.")
            break

        if tipo_paginacion == "url" and pagina > 1:
            separador = "&" if "?" in base_url else "?"
            url_pagina = f"{base_url}{separador}page={pagina}"
            try:
                page.goto(url_pagina, timeout=45000, wait_until="domcontentloaded")
            except Exception as e:
                print(f"  Error abriendo página {pagina}: {e}")
                break

        page.wait_for_timeout(2500)  # deja que cargue el JS/productos
        for _ in range(2):
            page.mouse.wheel(0, 2000)
            page.wait_for_timeout(700)

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
    tiempo_inicio = time.time()
    tiempo_limite = tiempo_inicio + TIEMPO_MAXIMO_SEGUNDOS

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
            ],
        )
        page = browser.new_page(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            ),
            viewport={"width": 1366, "height": 900},
            locale="es-PE",
            extra_http_headers={"Accept-Language": "es-PE,es;q=0.9"},
        )
        # Oculta la señal más común que delata a un navegador automatizado
        page.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )

        cortar_todo = False
        for sitio in SITIOS:
            if cortar_todo:
                break

            tienda = sitio["tienda"]
            dominio_base = f"{urlparse(sitio['urls'][0]).scheme}://{urlparse(sitio['urls'][0]).netloc}"

            for url in sitio["urls"]:
                if time.time() > tiempo_limite:
                    print(f"Tiempo máximo alcanzado ({TIEMPO_MAXIMO_SEGUNDOS}s). "
                          f"Se detiene la corrida; quedaron categorías sin revisar.")
                    cortar_todo = True
                    break

                print(f"[{tienda}] Revisando: {url}")
                try:
                    resultado = revisar_categoria(
                        page,
                        url,
                        dominio_base,
                        sitio["paginacion"],
                        tiempo_limite,
                        espera_extra_ms=sitio.get("espera_extra_ms", 0),
                    )
                except Exception as e:
                    print(f"  Error inesperado en {url}: {e}")
                    resultado = []

                for prod in resultado:
                    prod["tienda"] = tienda
                encontrados.extend(resultado)

        browser.close()

    minutos = round((time.time() - tiempo_inicio) / 60, 1)
    print(f"Corrida terminada en {minutos} minutos.")
    return encontrados


def main():
    ofertas = revisar_todo()

    if not ofertas:
        print("No se encontraron ofertas >= descuento mínimo.")
        return

    vistos = cargar_vistos()
    ofertas_nuevas = []
    for oferta in ofertas:
        link = (oferta.get("link") or "").strip()
        if not link or link in ("#", "javascript:void(0)") or not link.startswith("http"):
            continue
        clave = clave_oferta(oferta)
        if clave not in vistos:
            ofertas_nuevas.append(oferta)
            vistos.add(clave)

    if not ofertas_nuevas:
        print("Todas las ofertas encontradas ya habían sido notificadas antes (mismo % de descuento).")
        return

    for oferta in ofertas_nuevas:
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

    guardar_vistos(vistos)


if __name__ == "__main__":
    main()
