# Presentación

Diapositivas en [Slidev](https://sli.dev), en inglés: todo el contenido está en [`slides.md`](slides.md) y los
estilos en `style.css`. Reflejan el estado al 9 de octubre de 2026. El guion completo, con los datos de cada
diapositiva, es [`2026-10-09-estado-del-proyecto.md`](../docs/archive/presentations/2026-10-09-estado-del-proyecto.md).
La versión del 5 de octubre, en español, queda en el historial de git.

Requiere Node 20 o más reciente. La primera vez, `npm install` descarga unos 500 MB en `node_modules/`
(no se versiona).

| Comando | Qué hace |
| :--- | :--- |
| `npm install` | Instala Slidev (una sola vez) |
| `npm run dev` | Abre la presentación en el navegador, con recarga al guardar |
| `npm run build` | Genera una versión estática en `dist/` |
| `npm run export` | Exporta a PDF. La primera vez pide instalar `playwright-chromium` |

Durante la presentación: flechas para avanzar, `o` para ver todas las diapositivas, `f` para pantalla
completa y la ruta `/presenter` para el modo presentador, con las notas del orador y el cronómetro.
