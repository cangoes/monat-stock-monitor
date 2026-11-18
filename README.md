# Monitor de stock MONAT: todos los mercados, por región

Cada lunes a las 7:00 a. m. (hora de Bogotá) revisa todos los productos de
`shop.mymonat.com/shop/t/categories/all-products` en cada mercado y genera **un reporte por región** con:

- los productos que **se agotaron** desde el lunes anterior;
- los productos que **volvieron a tener stock**;
- los productos nuevos o que dejaron de aparecer en el listado, si los hay.

| Región | Mercados |
|---|---|
| **NA**: Norteamérica | US Estados Unidos, CA Canadá |
| **LATAM**: Latinoamérica | MX México, DO República Dominicana, PA Panamá |
| **EU**: Europa | GB Reino Unido, IE Irlanda, ES España, FR Francia, BE Bélgica, DE Alemania, AT Austria, IT Italia, CH Suiza, PL Polonia, LT Lituania, MT Malta (tienda "Rest of Europe") |
| **APAC**: Asia-Pacífico | AU Australia, NZ Nueva Zelanda |

Por ahora no se incluyen **Costa Rica**, porque su tienda no tenía productos el 18-nov-2026, ni **Colombia**, que aún no está lanzada. Cuando tengan productos, se agregan en `MARKETS` (CR = `market_id 30`, CO = `market_id 22`).

Cada producto trae su link en el formato con protocolo de mercado, listo para pegar en el array del mercado:

```
- Barrier Booster™ Ceramide Infusion Toner (SKU 6000006005)
  Link: https://corp.mymonat.com/shop/products/barrier-booster-ceramide-infusion-toner-6000006005?globallanguage=en&globalmarket=au
  Link anterior: https://corp.mymonat.com/shop/products/barrier-booster-ceramide-infusion-toner-6000006005so?globallanguage=en&globalmarket=au
```

El link cambia cuando un producto pasa a su versión "SO" (agotado) o vuelve a la versión normal. Si el link es distinto al de la semana anterior, el reporte muestra también el **Link anterior**, para buscarlo y reemplazarlo en el array.

El idioma del link (`globallanguage`) es el del mercado:
- `en`: US, CA, GB, IE, MT, AU y NZ.
- `es`: MX, DO, PA y ES.
- `fr`: FR y BE.
- `de`: DE, AT y CH.
- `it`: IT.
- `pl`: PL.
- `lt`: LT.

Se cambia en `link_language`, dentro de `MARKETS`. Los nombres de los productos salen en el idioma de cada tienda.

## Cómo comprueba que está en el mercado correcto

El sitio usa la misma URL para todos los mercados. Por eso, en cada mercado el script abre una sesión nueva, selecciona el país con `change_site_market` (su `market_id`) y verifica:

1. **Cookie `site_market_id`**: debe ser igual al `market_id` del mercado.
2. **Bandera del encabezado**: por ejemplo "Monat DE". Malta no muestra bandera.
3. **Moneda**: al menos el 90% de los precios en la moneda del mercado. Es `$` en US, CA, MX, DO, PA, AU y NZ; `£` en GB; `€` en la eurozona; `CHF` en CH y `zł` en PL.
4. **Precio de referencia**: el precio Retail del GLOSS MODE Shine Infusion Conditioner (SKU 6000007063), solo en los mercados donde existe ese producto. Debe caer en su rango; el 18-nov-2026 costaba US $57, CA $79, GB £54, AU $91 y NZ $113.
5. **Cantidad mínima de productos**: cerca de la mitad de los que había el 18-nov-2026.

Si una señal falla, ese mercado **no se compara y su historial no se toca**. El reporte de su región avisa qué señal falló y la ejecución queda en rojo en GitHub. Las demás regiones y mercados siguen normalmente.

## Cómo detecta "agotado"

Un producto está agotado cuando su botón para agregar al carrito está deshabilitado (clase `disabled`).
Cuando un producto se agota, MONAT a veces publica una versión con el sufijo **SO** en el SKU (`6000006005` → `6000006005so`). Por eso la comparación se hace por el **SKU sin el sufijo**. Un producto cuenta como disponible si cualquiera de sus versiones lo está.

## Notificación: Asana

Secreto `ASANA_TOKEN` con un *Personal Access Token*. Se crea en https://app.asana.com/0/my-apps → *Create new token*. Pégalo directo en GitHub, nunca en un chat ni en un correo.

Cada lunes se crea **una tarea por región** en el proyecto **MONAT SHOP STOCK**: "MONAT Stock NA — AAAA-MM-DD (Wnn)", "MONAT Stock LATAM — …", "MONAT Stock EU — …" y "MONAT Stock APAC — …".
- **Asignación:** cada tarea va asignada a ti y vence ese día.
- **Descripción:** el reporte de la región, con links.
- **Subtareas:** una por cada producto que se agotó o volvió a tener stock (ej. "DE · Se agotó · …"), para marcarla al actualizar el array de ese mercado.

El script busca el proyecto por nombre (variable `ASANA_PROJECT_NAME` en el workflow). Si le cambias el nombre al proyecto, actualiza esa línea del workflow. Si no lo encuentra, la tarea va a *Mis tareas* y el log lo avisa.

Como el token actúa en nombre de tu propia cuenta, Asana puede no enviarte una notificación por una tarea que "tú mismo" creaste; las tareas igual aparecen en el proyecto con su fecha.

Otros canales que el script también soporta (un mensaje por región), cada uno con su secreto:
- **Google Chat** (`GOOGLE_CHAT_WEBHOOK_URL`): solo funciona con cuentas de Google Workspace.
- **Slack** (`SLACK_WEBHOOK_URL`).
- **Correo**: `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `MAIL_TO` y, opcionalmente, `MAIL_FROM`.

## Ejecutarlo a mano

En *Actions → MONAT stock monitor → Run workflow*. El barrido de todos los mercados tarda unos 10 minutos. Si una ejecución falla, GitHub te avisa por correo (a la cuenta dueña del repositorio).

## Archivos

- `monitor.py`: el barrido, la verificación, la comparación y el envío. La lista de mercados está en `MARKETS` y la de regiones en `REGIONS`.
- `state/<MERCADO>.json`: el último estado conocido de cada mercado. Los genera y actualiza el workflow.
- `historial/`: una carpeta por semana, con el nombre del lunes y el número de semana ISO (ej. `2026-11-23_W48/`). Dentro hay una subcarpeta por región (`NA/`, `LATAM/`, `EU/`, `APAC/`), con:
  - `reporte.md`: el mismo texto que se envía en el mensaje de esa región.
  - `estado_<MERCADO>.json`: copia exacta del estado de esa semana de cada mercado, para compararla con cualquier otra semana.

  Si el workflow se ejecuta más de una vez en la misma semana, la carpeta de esa semana se sobrescribe con el último barrido.

  El **primer barrido** (el punto de partida) de cada mercado se guarda aparte, en `primer-barrido/` dentro de su región y semana, y nunca se sobrescribe. El de AU y NZ (18-nov-2026) está en `historial/2026-11-16_W47/APAC/primer-barrido/`.
- `tests/`: pruebas sin conexión (`pip install pytest && pytest`).

Para agregar un mercado, añade una línea en `MARKETS` (en `monitor.py`) con su región, `market_id`, `language_id`, idioma del link, moneda, bandera, mínimo de productos y, si existe el producto de referencia, su rango de precio. No hace falta cambiar el workflow: corre todos los mercados de `MARKETS`.
