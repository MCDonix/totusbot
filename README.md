# Bot de ofertas Tottus Perú (>80% descuento) → Telegram

Revisa varias categorías/búsquedas de tottus.falabella.com.pe cada 15 minutos
y te avisa por Telegram cuando encuentra un producto con 80% de descuento o más.
Corre gratis en GitHub Actions, sin servidor ni PC encendida.

## 1. Crear tu bot de Telegram (5 min)

1. Abre Telegram y busca **@BotFather**.
2. Envíale `/newbot`, ponle un nombre y un usuario (debe terminar en `bot`).
3. BotFather te dará un **token** parecido a `123456789:AAExxxxxxxxxxxxxxxxxxxxxxx`. Guárdalo.
4. Busca tu bot recién creado por su usuario y envíale cualquier mensaje (ej. "hola") para "activar" el chat.
5. Abre en el navegador (reemplazando TU_TOKEN):
   `https://api.telegram.org/botTU_TOKEN/getUpdates`
6. En el JSON que aparece busca `"chat":{"id":123456789,...}` — ese número es tu **chat_id**.

## 2. Subir estos archivos a GitHub

1. Crea una cuenta en https://github.com si no tienes.
2. Crea un repositorio nuevo (puede ser privado), por ejemplo `tottus-ofertas-bot`.
3. Sube TODOS los archivos de esta carpeta (incluida la carpeta `.github/workflows`)
   manteniendo la misma estructura. La forma más fácil: en la página del repo,
   usa "Add file" → "Upload files" y arrastra todo (asegúrate de que
   `.github/workflows/tottus-ofertas.yml` quede en esa ruta exacta).

## 3. Configurar los secretos

En tu repositorio: **Settings → Secrets and variables → Actions → New repository secret**

- `TELEGRAM_TOKEN` → el token de BotFather
- `TELEGRAM_CHAT_ID` → el chat_id que obtuviste

## 4. Listo

- El workflow ya está programado para correr cada 15 minutos automáticamente.
- Puedes forzar una ejecución de prueba ya mismo: pestaña **Actions** →
  "Tottus Ofertas Bot" → **Run workflow**.
- Revisa los "logs" de cada ejecución en esa misma pestaña si quieres ver
  qué encontró o si algo falló.

## Cosas a tener en cuenta

- **GitHub apaga los cron jobs si el repo está 60 días sin actividad.**
  Si eso pasa, entra a Actions y presiona "Enable workflow" (o haz cualquier commit).
- **Los selectores del sitio pueden cambiar.** Tottus actualiza su web de vez en
  cuando; si el bot deja de encontrar productos, hay que revisar los selectores
  CSS en `tottus_bot.py` (están comentados con instrucciones de cómo inspeccionar
  la página).
- **Categorías a revisar:** por defecto reviso solo unas pocas URLs de ejemplo
  (Despensa, Bebidas, Electro). Edita la lista `URLS` en `tottus_bot.py` para
  agregar las categorías que más te interesen — cuantas más URLs agregues,
  más tarda cada corrida (ten en cuenta que GitHub Actions gratis da 2,000
  minutos/mes en repos privados; en repos públicos es ilimitado).
- **Evita saturar el sitio:** el script ya espera unos segundos entre pasos.
  No lo configures para correr más seguido de cada 5 minutos, y evita agregar
  decenas de URLs, para no generar tráfico excesivo hacia Tottus.
- El archivo `seen.json` guarda qué ofertas ya te notificó, para no repetirte
  el mismo producto en cada corrida.
