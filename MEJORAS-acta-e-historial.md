# Qué podemos mejorar en el **acta** y en el **historial**

> **Estado: implementado en la v2.5.0 (entregas 1 y 2 completas).** Plantilla por
> defecto: **Junta Directiva** de un hospital. Las 23 pruebas nuevas están en
> `tests/test_acta_historial.py`; el detalle de lo entregado, en
> `CAMBIOS-v3-visual.md` §14.

Revisión del código actual (v2.4.0): `app.py` (acta: `acta_pipeline`, `_acta_por_secciones`,
`build_docx`, `_ia_save_result`; historial: `add_history_entry`, `evict_history`,
`/api/historial`), `static/app.js` (`summarize`, `renderActaPreview`, `renderHistoryList`)
y `static/index.html` (panel de acta y `#historyDrawer`).

Ordenado por **impacto / esfuerzo**. Todo cabe en los 4 archivos y sin dependencias nuevas
(salvo PDF, que usaría el mismo `python-docx`→LibreOffice o `reportlab`, y por eso queda al final).

---

## 1. Cinco cosas que hoy están rotas o a medias (arreglar primero, riesgo bajo)

| # | Qué pasa | Por qué importa | Arreglo |
|---|---|---|---|
| **A1** | Al borrar una reunión del historial **no se borra su acta**: `api_history_delete` elimina el audio y `resultados/{id}.json`, pero deja `resultados/{id}.acta.json`; `evict_history` hace lo mismo. Tampoco se limpia el `summary` de `sesiones.json`. | Quedan actas huérfanas en disco para siempre (privacidad + espacio), y el botón «Eliminar» miente. | Borrar los 3 (media + result + acta) en ambos caminos y limpiar la sesión. 15 min. |
| **A2** | El **.docx** no entiende el acta nueva: `**1. COMISIÓN DE CONSTRUCCIÓN**` sale como párrafo plano, las viñetas `* **Constructora X:** …` pierden negritas y las tablas (firmas, acuerdos) salen como líneas con `\|`. | El acta se ve bien en pantalla y **peor** al exportar, justo cuando se imprime o se firma. | Renderizar el markdown en `build_docx`: `**…**`→`Heading 2`, `*`→`List Bullet` con negritas internas, `\|`→tabla real, `---`→salto. 1–2 h. |
| **A3** | El campo **«Participantes»** nunca se rellena solo, aunque la app conoce a los hablantes y sus nombres corregidos. El acta escribe «Hablante 1» o «el director de finanzas». | Es el dato que más «abofetea» en un acta formal y ya lo tenemos. | Prellenar `#iaParticipants` con los nombres de `S.speakers` al cargar el resultado. 20 min. |
| **A4** | Las marcas `[01:12:35]` del acta **no son clicables** (en la tabla de acuerdos sí: `data-seek`). | Verificar una cifra contra el audio es lo que hace confiable un acta. | En `mdActa`, emitir `<button class="ts" data-seek="…">`. 30 min. |
| **A5** | El historial no dice si la reunión **tiene acta**, ni cuántos acuerdos/pendientes tiene. | Hay que abrir una por una para saber dónde quedó cada cosa. | Guardar `has_acta`, `acuerdos`, `pendientes` en la entrada (al guardar el acta) y mostrarlos como chips. 45 min. |

---

## 2. Acta: lo que más la haría «acta de verdad»

**B1. Plantilla institucional (impacto alto, 2 h).**
Hoy el encabezado es genérico y el cierre dice «Presidencia | Secretaría Técnica» en blanco.
Añadir una sección **Plantilla del acta** en la configuración (organización, órgano —Junta
Directiva, Comité…—, ciudad, quién preside, quién es el secretario técnico, logo opcional en
el .docx). Se usa en tres sitios: el prompt (`_plan_acta_secciones` + `_acta_por_secciones`),
el encabezado que arma la app y `build_docx`. Evita que cada acta invente su propio formato.

**B2. «Acta anterior» como contexto (impacto alto, 1,5 h).**
Al redactar, elegir la reunión previa de la misma serie y pasarle **solo** sus acuerdos y
pendientes. El acta puede decir «se dio seguimiento a lo acordado en la sesión del 12 de
octubre: …», y de paso se detectan acuerdos repetidos o contradictorios. Es lo que convierte
actas sueltas en una serie coherente.

**B3. Acuerdos editables y persistentes (impacto alto, 2 h).**
La tabla de acuerdos es de solo lectura y al volver a redactar se pierde lo que hubieras
corregido. Permitir editar **responsable / fecha / estatus** en la propia tabla (inputs en la
celda), guardar en `{id}.acta.json` y no pisarlo si solo se regenera el texto.

**B4. Versiones del acta por reunión (1,5 h).**
Cada vez que se redacta se sobrescribe. Guardar `versiones: [{v, texto, motor, formato,
fecha, chars}]` con botones **Comparar** y **Restaurar**. Muy útil cuando pruebas 1.5B → 7B
y quieres quedarte con la mejor.

**B5. Verificación de cifras accionable (1 h).**
Hoy las cifras dudosas son un toast que desaparece. Pasarlas a un panel bajo el acta, con la
frase donde aparecen y un botón para ir al minuto del audio o corregir la cifra. El usuario
decide; la app no inventa nada.

**B6. Una sola fuente de verdad para el acta editada (45 min).**
Lo que escribes se guarda en `sesiones.json` (`summary`) y el `.acta.json` sigue con el texto
generado: al reabrir, gana uno u otro según el orden. Guardar la edición también en
`.acta.json` como `texto_oficial` y exportar desde ahí.

**B7. Exportaciones que faltan (1–2 h).**
«Solo el acta» (sin transcripción, para enviar a terceros), **PDF** y `.docx` con la
plantilla corporativa. Hoy el .docx siempre lleva transcripción completa.

---

## 3. Historial: de lista de reuniones a herramienta de seguimiento

**C1. Buscador y filtros (1 h).** Caja de búsqueda (título, participantes, texto del acta),
filtro por fecha, idioma, «con acta», «con pendientes», y orden (reciente / más larga).
Con 60 reuniones, la lista actual es solo scroll.

**C2. Panel «Compromisos» (impacto alto, 2–3 h).** Vista transversal de **todos** los
acuerdos y pendientes de todas las reuniones: texto, responsable, fecha, estatus, reunión de
origen y botón para ir al minuto. Marcar hecho / vencido. Es la función que hace que el
historial valga más que el acta suelta.

**C3. Series y etiquetas (1,5 h).** Etiquetar cada reunión («Junta Directiva 2026»,
«Comité de calidad») y agrupar/colapsar por serie; el acta de cada serie hereda la plantilla
de B1. Renombrar la reunión desde el historial (hoy el título solo se puede cambiar antes de
transcribir).

**C4. Acciones por entrada (1 h).** Descargar el acta o el .docx **sin abrir** la reunión,
duplicar como plantilla y eliminar mostrando **qué se borra** (audio, acta, transcripción).

**C5. Retención transparente (45 min).** Al llegar a 60 reuniones / 800 MB se borra la más
antigua sin avisar. Mostrar «se borrarán X, Y» y dejar **proteger** una entrada para que no
se elimine nunca.

**C6. Continuar una reunión (2 h).** Añadir un segundo audio a la misma reunión (sesión
reanudada tras un receso) y redactar **un acta** con las dos partes, en vez de dos actas
sueltas.

---

## 4. Lo que NO recomiendo

- Buscar dentro del audio con embeddings: mucho coste (modelo + índice) para lo que ya
  resuelven las marcas `[HH:MM:SS]` clicables (A4).
- Reintentar la IA automáticamente «hasta que salga bien»: duplica tiempo y gasta CPU sin
  que el usuario sepa por qué tarda.
- Un editor WYSIWYG del acta: el textarea + vista formateada cubre el 95 % y no rompe la
  exportación.

---

## 5. Orden propuesto (dos entregas)

**Entrega 1 — «arreglos + lo que más se nota» (≈ 5 h)**
A1, A2, A3, A4, A5, B1, C1, B5.

**Entrega 2 — «seguimiento» (≈ 6 h)**
C2, B3, B4, B6, C3, C4, C5.

Quedan para después: B2, B7 (PDF) y C6.
