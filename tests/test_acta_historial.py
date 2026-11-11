"""Acta institucional, versiones, acuerdos editables y compromisos del historial.

Cubre las mejoras de las entregas 1 y 2: plantilla del acta, guardado de la
edición como versión oficial, versiones y restauración, acuerdos/pendientes
editables de cualquier reunión, buscador del historial, protección y borrado
completo (audio + transcripción + acta).
"""
import json

import pytest
from fastapi.testclient import TestClient

import app as appmod


@pytest.fixture()
def client(tmp_data, monkeypatch):
    monkeypatch.setattr(appmod, "recover_interrupted_jobs", lambda: 0)
    monkeypatch.setattr(appmod, "cleanup_old_files", lambda *a, **k: None)
    monkeypatch.setattr(appmod, "ensure_ia_defaults", lambda: appmod._read_ia_config())
    appmod.JOBS.clear()
    with TestClient(appmod.app) as c:
        yield c


@pytest.fixture()
def acta_de_ejemplo(tmp_data):
    """Una entrada de historial con acta guardada (texto + acuerdos + versiones)."""
    job_id = "jobacta00001"
    appmod.write_json(appmod.HISTORY_FILE, {"entries": [{
        "job_id": job_id, "title": "Junta Directiva Octubre", "created": "2026-10-30T10:00:00",
        "duration": 3600.0, "duration_human": "1 h", "language": "es", "speakers": 3,
        "words": 4200, "media_bytes": 1024, "has_acta": True, "acuerdos_n": 2,
        "pendientes_n": 1, "acta_version": 1, "serie": "Junta Directiva", "protegido": False,
    }, {
        "job_id": "jobsinacta01", "title": "Comité de calidad", "created": "2026-10-20T09:00:00",
        "duration": 1800.0, "duration_human": "30 min", "media_bytes": 512, "has_acta": False,
        "acuerdos_n": 0, "pendientes_n": 0, "protegido": True,
    }]})
    acta = {
        "job_id": job_id, "title": "Junta Directiva Octubre",
        "texto_oficial": "**ACTA DE SESIÓN DE LA JUNTA DIRECTIVA**\n\nSe aprobó el proyecto.",
        "text": "**ACTA DE SESIÓN DE LA JUNTA DIRECTIVA**\n\nSe aprobó el proyecto.",
        "version": 2, "editado": False,
        "acuerdos": [{"acuerdo": "Aprobar el estacionamiento [00:30]", "responsable": "Ing. Pérez",
                      "fecha": "diciembre de 2026", "estatus": "aprobado"},
                     {"acuerdo": "Reforzar la cobranza", "responsable": "", "fecha": "", "estatus": ""}],
        "pendientes": [{"tema": "Firmar el contrato [00:41]", "responsable": "Jurídico", "fecha": ""}],
        "siguientes_pasos": ["Enviar la minuta"],
        "versiones": [
            {"v": 1, "texto": "primera", "motor": "openai", "modelo": "qwen2.5:3b", "creada": "2026-10-30T10:05:00", "chars": 7},
            {"v": 2, "texto": "**ACTA DE SESIÓN DE LA JUNTA DIRECTIVA**\n\nSe aprobó el proyecto.",
             "motor": "openai", "modelo": "qwen2.5:3b", "creada": "2026-10-30T10:20:00", "chars": 60},
        ],
    }
    appmod.write_json(appmod.RESULT_DIR / f"{job_id}.acta.json", acta)
    return job_id


# --------------------------------------------------------------- plantilla
def test_plantilla_se_guarda_y_encabeza_el_acta(client, tmp_data):
    r = client.post("/api/plantilla", json={
        "organizacion": "Hospital San Rafael", "organo": "Junta Directiva",
        "ciudad": "Ciudad de México", "presidencia": "Lic. Ana Pérez",
        "presidencia_cargo": "Presidenta del Consejo", "secretaria": "Ing. Luis Ruiz",
        "secretaria_cargo": "Secretario Técnico",
    })
    assert r.status_code == 200
    pl = client.get("/api/plantilla").json()["plantilla"]
    assert pl["organizacion"] == "Hospital San Rafael"
    assert pl["presidencia"] == "Lic. Ana Pérez"

    encabezado = appmod._encabezado_acta("Sesión ordinaria", pl)
    assert "ACTA DE SESIÓN DE LA JUNTA DIRECTIVA" in encabezado
    assert "Hospital San Rafael" in encabezado and "Sesión ordinaria" in encabezado

    cierre = appmod._cierre_acta(pl)
    assert "Lic. Ana Pérez" in cierre and "Ing. Luis Ruiz" in cierre
    assert "Ciudad de México" in cierre


def test_plantilla_vacia_usa_los_huecos_de_firma(tmp_path, monkeypatch):
    monkeypatch.setattr(appmod, "IA_CONFIG_FILE", tmp_path / "ia_config.json")
    appmod.write_json(appmod.IA_CONFIG_FILE, {})
    cierre = appmod._cierre_acta()
    assert "______" in cierre and "Presidencia" in cierre


def test_articulo_del_organo():
    assert appmod._encabezado_acta("", {"organo": "Comité de Calidad"}).startswith(
        "**ACTA DE SESIÓN DEL COMITÉ DE CALIDAD")
    assert "DE LA JUNTA DIRECTIVA" in appmod._encabezado_acta("", {"organo": "Junta Directiva"})


# ------------------------------------------------------------- edición/versiones
def test_guardar_acta_editada_actualiza_texto_acuerdos_e_historial(client, acta_de_ejemplo):
    nuevo = "**ACTA DE SESIÓN**\n\nTexto corregido por el usuario."
    r = client.put(f"/api/jobs/{acta_de_ejemplo}/acta", json={
        "texto": nuevo,
        "acuerdos": [{"acuerdo": "Aprobar el estacionamiento", "responsable": "Dirección",
                      "fecha": "2026-12-01", "estatus": "hecho"}],
    })
    assert r.status_code == 200
    guardada = client.get(f"/api/jobs/{acta_de_ejemplo}/acta").json()
    assert guardada["texto_oficial"] == nuevo
    assert guardada["editado"] is True
    assert guardada["acuerdos"][0]["estatus"] == "hecho"
    entrada = appmod.load_history()[0]
    assert entrada["acuerdos_n"] == 1
    assert "Texto corregido" in entrada["acta_resumen"]


def test_guardar_acta_sin_acta_previa_da_404(client, tmp_data):
    r = client.put("/api/jobs/noexiste/acta", json={"texto": "x"})
    assert r.status_code == 404


def test_versiones_lista_y_restaura(client, acta_de_ejemplo):
    r = client.get(f"/api/jobs/{acta_de_ejemplo}/acta/versiones").json()
    assert [v["v"] for v in r["versiones"]] == [1, 2]
    assert r["actual"] == 2

    r = client.post(f"/api/jobs/{acta_de_ejemplo}/acta/restaurar", json={"v": 1})
    assert r.status_code == 200 and r.json()["texto"] == "primera"
    assert client.get(f"/api/jobs/{acta_de_ejemplo}/acta").json()["version_oficial"] == 1

    assert client.post(f"/api/jobs/{acta_de_ejemplo}/acta/restaurar", json={"v": 99}).status_code == 404


# ---------------------------------------------------------------- compromisos
def test_compromisos_agrega_todas_las_reuniones(client, acta_de_ejemplo):
    data = client.get("/api/compromisos").json()
    assert data["total"] == 3                     # 2 acuerdos + 1 pendiente
    assert data["pendientes"] >= 2
    primero = next(i for i in data["items"] if i["tipo"] == "acuerdo")
    assert primero["job_id"] == acta_de_ejemplo
    assert primero["minuto"] == "00:30"           # la marca ayuda a saltar al audio


def test_compromiso_editable_por_campo(client, acta_de_ejemplo):
    r = client.patch(f"/api/compromisos/{acta_de_ejemplo}", json={
        "tipo": "acuerdo", "idx": 1, "campo": "estatus", "valor": "cumplido"})
    assert r.status_code == 200 and r.json()["item"]["estatus"] == "cumplido"
    r = client.patch(f"/api/compromisos/{acta_de_ejemplo}", json={
        "tipo": "pendiente", "idx": 0, "campo": "responsable", "valor": "Dirección Jurídica"})
    assert r.json()["item"]["responsable"] == "Dirección Jurídica"
    # el acta guardada queda actualizada (no solo la vista)
    guardada = client.get(f"/api/jobs/{acta_de_ejemplo}/acta").json()
    assert guardada["pendientes"][0]["responsable"] == "Dirección Jurídica"
    # campo no permitido e índice fuera de rango
    assert client.patch(f"/api/compromisos/{acta_de_ejemplo}", json={
        "tipo": "acuerdo", "idx": 0, "campo": "hackeado", "valor": "x"}).status_code == 400
    assert client.patch(f"/api/compromisos/{acta_de_ejemplo}", json={
        "tipo": "acuerdo", "idx": 40, "campo": "estatus", "valor": "x"}).status_code == 404


# ------------------------------------------------------------------ historial
def test_historial_renombra_etiqueta_y_protege(client, acta_de_ejemplo):
    r = client.post(f"/api/historial/{acta_de_ejemplo}", json={
        "title": "Junta Directiva · sesión 12", "serie": "Junta Directiva 2026", "protegido": True})
    assert r.status_code == 200
    entrada = next(e for e in appmod.load_history() if e["job_id"] == acta_de_ejemplo)
    assert entrada["title"] == "Junta Directiva · sesión 12"
    assert entrada["serie"] == "Junta Directiva 2026"
    assert entrada["protegido"] is True


def test_historial_dice_que_se_va_a_borrar(client, acta_de_ejemplo, monkeypatch):
    monkeypatch.setattr(appmod, "MAX_HISTORY", 1)
    data = client.get("/api/historial").json()
    assert data["count"] == 2 and data["max_entries"] == 1
    assert [p["job_id"] for p in data["proximos_a_borrar"]] == [acta_de_ejemplo]


def test_borrar_historial_elimina_acta_y_sesion(client, acta_de_ejemplo, tmp_data):
    appmod.save_session_payload("default", {"job_id": acta_de_ejemplo, "summary": "acta"})
    assert (appmod.RESULT_DIR / f"{acta_de_ejemplo}.acta.json").exists()
    r = client.delete(f"/api/historial/{acta_de_ejemplo}")
    assert r.status_code == 200
    assert not (appmod.RESULT_DIR / f"{acta_de_ejemplo}.acta.json").exists()
    sesion = appmod.get_session("default")
    assert acta_de_ejemplo not in (sesion.get("jobs") or {})


def test_evict_history_respeta_las_protegidas(tmp_data, monkeypatch):
    monkeypatch.setattr(appmod, "MAX_HISTORY", 1)
    entradas = [
        {"job_id": "nueva", "created": "2026-11-01T10:00:00", "protegido": True},
        {"job_id": "vieja", "created": "2026-10-01T10:00:00", "protegido": False},
        {"job_id": "media", "created": "2026-10-15T10:00:00", "protegido": False},
    ]
    appmod.write_json(appmod.RESULT_DIR / "vieja.acta.json", {"texto": "x"})
    quedan = appmod.evict_history(entradas)
    ids = {e["job_id"] for e in quedan}
    assert "nueva" in ids                       # la protegida nunca se va
    assert len(quedan) <= 1 or "vieja" not in ids
    assert not (appmod.RESULT_DIR / "vieja.acta.json").exists()   # su acta también se borra


# ------------------------------------------------------------------ .docx
def test_docx_renderiza_markdown_del_acta(tmp_path, monkeypatch):
    monkeypatch.setattr(appmod, "RESULT_DIR", tmp_path)
    texto = ("**ACTA DE SESIÓN DE LA JUNTA DIRECTIVA**\n\n"
             "Se presentaron propuestas.\n\n"
             "* **Constructora La Seca:** 500 cajones por $950,000 pesos.\n\n"
             "| Firma | Cargo |\n| --- | --- |\n| Ana Pérez | Presidencia |\n")
    path = appmod.build_acta_docx("Junta", texto, {"organizacion": "Hospital San Rafael"})
    from docx import Document
    doc = Document(str(path))
    estilos = [p.style.name for p in doc.paragraphs]
    assert "Heading 1" in estilos and "List Bullet" in estilos
    assert any("Constructora La Seca" in p.text for p in doc.paragraphs)
    assert doc.tables and doc.tables[0].rows[1].cells[0].text == "Ana Pérez"
    assert "*" not in "".join(p.text for p in doc.paragraphs)


def test_exportar_solo_el_acta(client, acta_de_ejemplo):
    r = client.post(f"/api/export/{acta_de_ejemplo}", json={"format": "acta"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.wordprocessingml")


# ------------------------------------------------------- B2 · acta anterior
def test_acta_previa_toma_la_reunion_anterior_de_la_serie(client, tmp_data):
    appmod.write_json(appmod.HISTORY_FILE, {"entries": [
        {"job_id": "nueva", "title": "Junta Directiva Noviembre", "created": "2026-11-02T10:00:00",
         "has_acta": True, "serie": "Junta Directiva"},
        {"job_id": "vieja", "title": "Junta Directiva Octubre", "created": "2026-10-02T10:00:00",
         "has_acta": True, "serie": "Junta Directiva"},
    ]})
    appmod.write_json(appmod.RESULT_DIR / "vieja.acta.json", {
        "acuerdos": [{"acuerdo": "Aprobar el presupuesto 2027", "responsable": "Dirección",
                      "fecha": "noviembre", "estatus": "pendiente"}],
        "pendientes": [{"tema": "Contratar al auditor externo", "responsable": "Jurídico"}],
    })
    appmod.write_json(appmod.RESULT_DIR / "nueva.acta.json", {"acuerdos": [], "pendientes": []})

    ctx = appmod._contexto_acta_previa({"id": "nueva", "title": "Junta Directiva Noviembre"})
    assert ctx["id"] == "vieja"
    assert "Aprobar el presupuesto 2027" in ctx["texto"]
    assert "Contratar al auditor externo" in ctx["texto"]
    # solo acuerdos y pendientes: nunca el texto completo del acta
    assert "ACTA DE SESIÓN" not in ctx["texto"]

    # elección explícita del usuario
    assert appmod._contexto_acta_previa({"id": "nueva"}, "vieja")["id"] == "vieja"
    assert appmod._contexto_acta_previa({"id": "nueva"}, "noexiste")["id"] == ""
    # sin serie ni título coincidente no se inventa contexto
    assert appmod._contexto_acta_previa({"id": "otra", "title": "Comité"})["id"] == ""


def test_acta_previa_entra_en_el_prompt(client, tmp_data, monkeypatch):
    appmod.write_json(appmod.HISTORY_FILE, {"entries": [
        {"job_id": "nueva", "title": "Junta", "created": "2026-11-02T10:00:00"}
    ]})
    capturado = {}

    def falso_engine(prompt, system, engine, cfg, tokens, stream=False):
        capturado["prompt"] = prompt
        if False:
            yield ""
        return "**ACTA DE SESIÓN**\n\nTexto."

    monkeypatch.setattr(appmod, "_emitir_engine", falso_engine)
    segs = [{"start": 0, "end": 5, "text": "Se aprueba el proyecto.", "speaker": "Ana"}]
    list(appmod.acta_pipeline(
        segs, fmt="resumen", engine="openai",
        cfg={"api_key": "", "local_model": "", "openai_base_url": "http://x", "openai_model": "m"},
        acta_previa="Reunión anterior de la serie: Junta\n- Acuerdo: Aprobar el presupuesto",
    ))
    assert "Acta anterior de la misma serie" in capturado["prompt"]
    assert "Aprobar el presupuesto" in capturado["prompt"]


# ---------------------------------------------------------- C4 · duplicar
def test_duplicar_acta_crea_una_copia_editable(client, acta_de_ejemplo):
    r = client.post(f"/api/historial/{acta_de_ejemplo}/duplicar", json={})
    assert r.status_code == 200
    nuevo = r.json()["job_id"]
    assert nuevo != acta_de_ejemplo
    assert "copia" in r.json()["title"]

    acta = client.get(f"/api/jobs/{nuevo}/acta").json()
    assert acta["duplicado"] is True
    assert acta["duplicado_de"] == acta_de_ejemplo
    assert "JUNTA DIRECTIVA" in acta["texto_oficial"]
    assert len(acta["acuerdos"]) == 2

    # la copia se abre en la interfaz aunque no tenga audio
    resultado = client.get(f"/api/jobs/{nuevo}/result").json()
    assert resultado["segments"] == []
    assert resultado["job"]["duplicado"] is True

    # y se puede editar y guardar como cualquier acta
    r = client.put(f"/api/jobs/{nuevo}/acta", json={"texto": "**ACTA**\n\nCopia editada."})
    assert r.status_code == 200
    entrada = next(e for e in appmod.load_history() if e["job_id"] == nuevo)
    assert entrada["duplicado"] is True and "Copia editada" in entrada["acta_resumen"]


def test_duplicar_sin_acta_falla(client, tmp_data):
    appmod.write_json(appmod.HISTORY_FILE, {"entries": [
        {"job_id": "sinacta", "title": "Reunión", "created": "2026-11-01T10:00:00"}]})
    assert client.post("/api/historial/sinacta/duplicar", json={}).status_code == 404


# -------------------------------------------------- C6 · continuar reunión
def _reunion_falsa(job_id: str, inicio: float, segmentos):
    jid_i = 0
    segs = []
    for i, seg in enumerate(segmentos):
        jid_i += 1
        segs.append({"id": jid_i, "start": float(seg[0]), "end": float(seg[1]),
                     "text": seg[2], "speaker": seg[3] if len(seg) > 3 else "Ana"})
    result = {"job_id": job_id, "title": "Junta", "segments": segs, "speakers": [],
              "stats": {"words": sum(len(s["text"].split()) for s in segs),
                        "segments": len(segs)},
              "info": {"duration": float(inicio)}, "duration": float(inicio)}
    appmod.write_json(appmod.RESULT_DIR / f"{job_id}.json", result)
    return result


def test_fusionar_parte_desplaza_tiempos_y_recalcula(client, tmp_data, monkeypatch):
    appmod.write_json(appmod.HISTORY_FILE, {"entries": [
        {"job_id": "reunion1", "title": "Junta", "created": "2026-11-01T09:00:00",
         "duration": 60.0, "duration_human": "1 min", "media": "", "media_bytes": 0,
         "has_acta": True, "partes": 1},
    ]})
    appmod.write_json(appmod.RESULT_DIR / "reunion1.acta.json",
                      {"texto_oficial": "**ACTA**", "acuerdos": [], "pendientes": []})
    _reunion_falsa("reunion1", 60.0, [(0.0, 4.0, "Primera parte"), (4.0, 8.0, "Sigue")])
    parte = _reunion_falsa("parte2", 30.0, [(0.0, 3.0, "Tras el receso")])
    padre = appmod.ensure_job_loaded("reunion1")
    padre["result"] = _reunion_falsa("reunion1", 60.0,
                                     [(0.0, 4.0, "Primera parte"), (4.0, 8.0, "Sigue")])
    monkeypatch.setattr(appmod, "concatenar_audio", lambda rutas, destino: None)

    job2 = {"id": "parte2", "continua_de": "reunion1", "parte": 2,
            "duration": 30.0, "result": parte, "filename": "parte2.mp3",
            "source_path": "", "title": "Junta", "warnings": []}
    actualizado = appmod.fusionar_parte_en_reunion(job2)
    assert actualizado is not None
    segs = actualizado["result"]["segments"]
    assert len(segs) == 3
    assert segs[2]["start"] == 60.0 and segs[2]["parte"] == 2   # desplazado por la 1.ª parte
    assert actualizado["result"]["duration"] == 90.0
    assert [p["n"] for p in actualizado["result"]["partes"]] == [1, 2]
    assert actualizado["result"]["stats"]["segments"] == 3
    # el acta avisa de que hay que volver a redactarla
    acta = appmod.leer_acta_guardada("reunion1")
    assert acta["desactualizada"] is True
    entrada = appmod.load_history()[0]
    assert entrada["duration"] == 90.0 and entrada["partes"] == 2


def test_continuar_reunion_valida_el_estado(client, tmp_data):
    assert client.post("/api/jobs/noexiste/continuar", json={"file_id": "x"}).status_code == 404
    appmod.write_json(appmod.HISTORY_FILE, {"entries": [
        {"job_id": "reunion1", "title": "Junta", "created": "2026-11-01T09:00:00"}]})
    _reunion_falsa("reunion1", 60.0, [(0.0, 4.0, "Hola")])
    appmod.JOBS.pop("reunion1", None)
    r = client.post("/api/jobs/reunion1/continuar", json={"file_id": "noexiste"})
    assert r.status_code == 404      # falta subir el audio de la parte nueva


def test_continuar_reunion_encola_la_parte(client, tmp_data, monkeypatch):
    appmod.write_json(appmod.HISTORY_FILE, {"entries": [
        {"job_id": "reunion1", "title": "Junta", "created": "2026-11-01T09:00:00"}]})
    _reunion_falsa("reunion1", 60.0, [(0.0, 4.0, "Hola")])
    appmod.JOBS.pop("reunion1", None)
    audio = appmod.UPLOAD_DIR / "archparte1.mp3"
    audio.write_bytes(b"\x00" * 4000)
    appmod.UPLOADS["archparte1"] = {"file_id": "archparte1", "filename": "receso.mp3",
                                    "path": str(audio), "duration": 42.0, "size": 4000}
    monkeypatch.setattr(appmod.WORK_QUEUE, "put", lambda *a, **k: None)

    r = client.post("/api/jobs/reunion1/continuar", json={"file_id": "archparte1"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["parte"] == 2 and data["reunion"] == "reunion1"
    nuevo = appmod.JOBS[data["job_id"]]
    assert nuevo["continua_de"] == "reunion1" and nuevo["status"] == "queued"
    assert nuevo["title"] == "Junta"

    # una segunda parte simultánea se rechaza
    r2 = client.post("/api/jobs/reunion1/continuar", json={"file_id": "archparte1"})
    assert r2.status_code == 409


def test_historial_normaliza_entradas_antiguas(client, tmp_data):
    """Las entradas creadas antes de la 2.5 no traían chips ni protección."""
    appmod.write_json(appmod.HISTORY_FILE, {"entries": [{
        "job_id": "antigua1", "title": "Reunión vieja", "created": "2026-09-01T09:00:00",
        "size": 4096, "media": "historial/antigua1.m4a",
    }]})
    appmod.write_json(appmod.RESULT_DIR / "antigua1.acta.json", {
        "version": 3, "texto_oficial": "**ACTA**\n\nTexto.",
        "acuerdos": [{"acuerdo": "Uno"}, {"acuerdo": "Dos"}],
        "pendientes": [{"tema": "Tres"}],
        "plantilla": {"organo": "Junta Directiva"},
    })
    entrada = client.get("/api/historial").json()["entries"][0]
    assert entrada["has_acta"] is True
    assert entrada["acuerdos_n"] == 2 and entrada["pendientes_n"] == 1
    assert entrada["acta_version"] == 3
    assert entrada["media_bytes"] == 4096
    assert entrada["protegido"] is False
    assert entrada["serie"] == "Junta Directiva"
    assert "ACTA" in entrada["acta_resumen"]
    # y queda guardado, no se recalcula en cada visita
    assert appmod.load_history()[0]["has_acta"] is True


def test_versiones_de_un_acta_antigua_muestran_la_vigente(client, tmp_data):
    """Las actas anteriores a la 2.5 no tienen historial de versiones."""
    appmod.write_json(appmod.HISTORY_FILE, {"entries": [
        {"job_id": "vieja1", "title": "Reunión", "created": "2026-09-01T09:00:00"}]})
    appmod.write_json(appmod.RESULT_DIR / "vieja1.acta.json", {
        "version": 2, "engine": "openai", "created": "2026-09-01T10:00:00",
        "texto_oficial": "**ACTA**\n\nTexto vigente.",
    })
    data = client.get("/api/jobs/vieja1/acta/versiones").json()
    assert len(data["versiones"]) == 1
    assert data["versiones"][0]["v"] == 2
    assert data["versiones"][0]["chars"] == len("**ACTA**\n\nTexto vigente.")


def test_la_version_de_la_interfaz_coincide_con_el_servidor():
    """Evita que el navegador se quede con un app.js viejo sin darse cuenta."""
    import re
    from pathlib import Path
    js = (Path(appmod.BASE_DIR) / "static" / "app.js").read_text(encoding="utf-8")
    m = re.search(r"const JS_VERSION = '([^']+)'", js)
    assert m, "app.js debe declarar JS_VERSION"
    assert m.group(1) == appmod.VERSION, (
        f"JS_VERSION ({m.group(1)}) y VERSION ({appmod.VERSION}) deben coincidir")
    html = (Path(appmod.BASE_DIR) / "static" / "index.html").read_text(encoding="utf-8")
    assert "app.js?v=__V__" in html and "style.css?v=__V__" in html
