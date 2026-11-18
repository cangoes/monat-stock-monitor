# Monitor de stock MONAT — Australia y Nueva Zelanda

Cada lunes a las 7:00 a. m. (hora de Bogotá) revisa todos los productos de
`shop.mymonat.com/shop/t/categories/all-products` en **AU** y **NZ**. Envía un mensaje con:

- los productos que **se agotaron** desde el lunes anterior;
- los productos que **volvieron a tener stock**;
- los productos nuevos o que dejaron de aparecer en el listado, si los hay.

## Cómo comprueba que está en el mercado correcto

El sitio usa la misma URL para todos los mercados. Por eso, en cada mercado el script abre una sesión nueva, selecciona el país con `change_site_market` (AU = `market_id 18`, NZ = `market_id 20`) y verifica:

| Señal | AU | NZ |
|---|---|---|
| Cookie `site_market_id` | 18 | 20 |
| Bandera del encabezado | Monat AU | Monat NZ |
| Precio Retail de GLOSS MODE Shine Infusion Conditioner (SKU 6000007063) | $75–110 (hoy $91) | $95–135 (hoy $113) |
| Cantidad de productos | ≥ 50 | ≥ 50 |

Como referencia, en US ese producto cuesta $57. Si una señal falla, ese mercado **no se compara y su historial no se toca**. El mensaje avisa qué señal falló y la ejecución queda en rojo en GitHub.

Si MONAT cambia el precio de referencia, ajusta `ref_price_range` en `monitor.py`.

## Cómo detecta "agotado"

Un producto está agotado cuando su botón **ADD** está deshabilitado (clase `disabled`).
Cuando un producto se agota, MONAT a veces publica una versión con el sufijo **SO** en el SKU (`6000006005` → `6000006005so`). Por eso la comparación se hace por el **SKU sin el sufijo**. Un producto cuenta como disponible si cualquiera de sus versiones lo está.

## Puesta en marcha (unos 10 minutos)

1. Crea un repositorio **privado** en GitHub y sube todo el contenido de esta carpeta, incluida `.github/`.
2. Configura el canal del mensaje en *Settings → Secrets and variables → Actions → New repository secret*. Usa uno o varios:
   - **Slack:** `SLACK_WEBHOOK_URL`, un Incoming Webhook del canal.
   - **Teams:** `TEAMS_WEBHOOK_URL`, un webhook del canal.
   - **Correo:** `SMTP_HOST`, `SMTP_PORT` (587), `SMTP_USER`, `SMTP_PASSWORD`, `MAIL_TO` (puedes poner varios separados por coma) y, opcionalmente, `MAIL_FROM`. Con Gmail, usa una *contraseña de aplicación*.
3. Ve a *Actions → MONAT stock monitor → Run workflow* para hacer la **primera ejecución de prueba**.
   - La primera vez guarda el punto de partida y lista los productos agotados de hoy.
   - El reporte aparece en el resumen de la ejecución y en `reports/AAAA-MM-DD.md`.
4. Si sale en verde con "Mercado verificado ✅" en AU y NZ, ya está listo. Desde ahí corre solo cada lunes.

Si una ejecución falla, GitHub te avisa por correo (a la cuenta dueña del repositorio).

## Archivos

- `monitor.py`: el barrido, la verificación, la comparación y el envío.
- `state/AU.json`, `state/NZ.json`: el último estado conocido. Los genera y actualiza el workflow.
- `reports/`: un reporte por semana, para tener historial.
- `tests/`: pruebas sin conexión (`pip install pytest && pytest`).

Para agregar más mercados, añade una entrada en `MARKETS` dentro de `monitor.py`, con su `market_id`, el texto de la bandera y el rango de precio. Luego inclúyelo en la variable `MARKETS` del workflow.
