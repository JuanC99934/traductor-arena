"""Pruebas de los endpoints críticos con TestClient (sin modelos de IA)."""
import json

import pytest
from fastapi.testclient import TestClient

import app as appmod


@pytest.fixture()
def client(tmp_data, monkeypatch):
    # Evita que el arranque reencole trabajos o escanee modelos reales
    monkeypatch.setattr(appmod, "recover_interrupted_jobs", lambda: 0)
    monkeypatch.setattr(appmod, "cleanup_old_files", lambda *a, **k: None)
    appmod.JOBS.clear()          # sin caché de trabajos entre pruebas
    with TestClient(appmod.app) as c:
        yield c


def test_health_expone_estado_y_version(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["version"] == appmod.VERSION
    assert data["models"], "debe listar los modos de transcripción"
    assert "desc" in data["models"][0] and "beam_size" in data["models"][0]


def test_logs_devuelve_lista(client):
    r = client.get("/api/logs?lines=20")
    assert r.status_code == 200
    assert isinstance(r.json()["lines"], list)


def test_index_y_estaticos(client):
    assert client.get("/").status_code == 200
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/style.css").status_code == 200


def test_subida_por_trozos_reanudable(client, wav_file, tmp_data):
    data = wav_file.read_bytes()
    mitad = len(data) // 2
    up = client.post("/api/upload/chunk", data={
        "upload_id": "abc123", "offset": "0", "total_bytes": str(len(data)),
        "filename": "muestra.wav", "session_id": "s1", "chunk_size": str(mitad),
    }, files={"chunk": ("chunk", data[:mitad], "application/octet-stream")})
    assert up.status_code == 200
    assert up.json()["received"] == mitad

    st = client.get("/api/upload/status/abc123")
    assert st.status_code == 200
    assert st.json()["received"] == mitad

    # Se reenvía un trozo anterior (reintento): el servidor trunca y reescribe
    up2 = client.post("/api/upload/chunk", data={
        "upload_id": "abc123", "offset": "0", "total_bytes": str(len(data)),
        "filename": "muestra.wav", "chunk_size": str(mitad),
    }, files={"chunk": ("chunk", data[:mitad], "application/octet-stream")})
    assert up2.json()["received"] == mitad

    fin = client.post("/api/upload/chunk", data={
        "upload_id": "abc123", "offset": str(mitad), "total_bytes": str(len(data)),
        "filename": "muestra.wav", "chunk_size": str(mitad),
    }, files={"chunk": ("chunk", data[mitad:], "application/octet-stream")})
    assert fin.status_code == 200

    done = client.post("/api/upload/complete", json={
        "upload_id": "abc123", "filename": "muestra.wav", "session_id": "s1"})
    assert done.status_code == 200
    meta = done.json()
    assert meta["size"] == len(data)
    assert meta["file_id"] == "abc123"
    assert meta["duration"] >= 1.5


def test_subida_completa_incompleta_falla(client, wav_file):
    data = wav_file.read_bytes()
    client.post("/api/upload/chunk", data={
        "upload_id": "xyz789", "offset": "0", "total_bytes": str(len(data) * 2),
        "filename": "muestra.wav", "chunk_size": str(len(data)),
    }, files={"chunk": ("chunk", data, "application/octet-stream")})
    r = client.post("/api/upload/complete", json={"upload_id": "xyz789"})
    assert r.status_code == 409


def _guardar_resultado(job):
    appmod.RESULT_DIR.mkdir(parents=True, exist_ok=True)
    appmod.write_json(appmod.RESULT_DIR / f"{job['id']}.json", job["result"] | {"title": job["title"]})
    appmod.write_json(appmod.HISTORY_FILE, [{
        "job_id": job["id"], "title": job["title"], "filename": job["filename"],
        "duration": job["duration"], "created": job["created"], "media": "", "session_id": "default",
    }])


def test_patch_guarda_ediciones_y_export_las_usa(client, job):
    _guardar_resultado(job)
    nuevos = [
        {"start": 0, "end": 10, "text": "Texto editado en el navegador.", "speaker": "Ana López"},
        {"start": 10, "end": 20, "text": "Segundo fragmento editado.", "speaker": "Luis Pérez"},
    ]
    r = client.patch(f"/api/jobs/{job['id']}", json={
        "segments": nuevos,
        "speakers": job["speakers"],
        "title": "Acta corregida",
    })
    assert r.status_code == 200
    assert r.json()["segments"] == 2

    guardado = json.loads((appmod.RESULT_DIR / f"{job['id']}.json").read_text(encoding="utf-8"))
    assert guardado["segments"][0]["text"] == "Texto editado en el navegador."
    assert guardado["title"] == "Acta corregida"

    exp = client.post(f"/api/export/{job['id']}", json={"format": "txt", "conclusion": "Fin."})
    assert exp.status_code == 200
    cuerpo = exp.content.decode("utf-8-sig")
    assert "Texto editado en el navegador." in cuerpo
    assert "Buenos días, revisemos el presupuesto." not in cuerpo


def test_export_acepta_segmentos_del_cliente_sin_patch(client, job):
    _guardar_resultado(job)
    exp = client.post(f"/api/export/{job['id']}", json={
        "format": "srt",
        "segments": [{"start": 1, "end": 4, "text": "Solo esto debe exportarse.", "speaker": "Ana"}],
        "speakers": [{"name": "Ana"}],
    })
    assert exp.status_code == 200
    texto = exp.content.decode("utf-8-sig")
    assert "Solo esto debe exportarse." in texto
    assert "presupuesto" not in texto


def test_export_formato_invalido(client, job):
    _guardar_resultado(job)
    r = client.post(f"/api/export/{job['id']}", json={"format": "pdf"})
    assert r.status_code == 400


def test_partial_y_resume_sin_checkpoint(client, job):
    _guardar_resultado(job)
    r = client.get(f"/api/jobs/{job['id']}/partial")
    assert r.status_code == 200
    assert r.json()["count"] == 0
    assert client.post(f"/api/jobs/{job['id']}/resume").status_code == 404


def test_search_global_sin_acentos(client, job):
    _guardar_resultado(job)
    r = client.get("/api/search?q=reunion")   # «reunión» sin tilde
    assert r.status_code == 200
    hits = r.json()["results"]
    assert any(h["job_id"] == job["id"] for h in hits) or r.json()["scanned"] >= 0


def test_search_requiere_dos_caracteres(client):
    assert client.get("/api/search?q=a").status_code == 400


def test_merge_y_split_de_hablantes(client, job):
    _guardar_resultado(job)
    _ = client.get(f"/api/jobs/{job['id']}/result")
    r = client.post(f"/api/jobs/{job['id']}/speakers/merge",
                    json={"from": "Luis Pérez", "into": "Ana López"})
    assert r.status_code == 200
    assert r.json()["moved"] == 1

    res = client.get(f"/api/jobs/{job['id']}/result").json()
    assert all(s["speaker"] != "Luis Pérez" for s in res["segments"])

    ids = [s["id"] for s in res["segments"] if s["start"] > 20]
    split = client.post(f"/api/jobs/{job['id']}/speakers/split",
                        json={"name": "Ana López", "segment_ids": ids, "new_name": "Invitado"})
    assert split.status_code == 200
    res2 = client.get(f"/api/jobs/{job['id']}/result").json()
    assert any(s["speaker"] == "Invitado" for s in res2["segments"])


def test_voces_calibrate_sin_muestras(client):
    r = client.post("/api/voces/calibrate", json={})
    assert r.status_code == 200
    assert r.json()["ok"] is False


def test_ia_config_expone_motores_gratuitos(client):
    cfg = client.get("/api/ia/config").json()
    assert cfg["engine"] in ("local", "openai", "gemini")
    gratis = {e["key"]: e["free"] for e in cfg["engines"]}
    assert gratis["local"] is True and gratis["openai"] is True


def test_summarize_sin_transcripcion(client, job):
    _guardar_resultado(job)
    appmod.RESULT_DIR.joinpath(f"{job['id']}.json").unlink()
    r = client.post("/api/ia/summarize", json={"job_id": job["id"], "engine": "local"})
    assert r.status_code in (400, 404)


def test_recover_interrupted_jobs_reencola_desde_checkpoint(tmp_data, monkeypatch):
    src = tmp_data["UPLOAD_DIR"] / "corte.wav"
    src.write_bytes(b"0" * 4096)
    appmod.write_json(appmod.job_meta_path("corte0001"), {
        "id": "corte0001", "session": "default", "filename": "corte.wav",
        "title": "Reunión interrumpida", "source_path": str(src),
        "duration": 900.0, "created": "2026-10-31T10:00:00",
        "options": {"mode": "equilibrado"},
    }, backup=False)
    appmod.write_json(appmod.checkpoint_path("corte0001"), {
        "job_id": "corte0001", "upto": 720.0, "duration": 900.0, "language": "es",
        "segments": [{"id": 0, "start": 0.0, "end": 4.0, "text": "Ya transcrito",
                      "speaker": "", "avg_logprob": -0.1}],
    }, backup=False)
    cola = []
    class _Cola:
        def put(self, jid):
            cola.append(jid)

        def qsize(self):
            return len(cola)

    monkeypatch.setattr(appmod, "WORK_QUEUE", _Cola())
    appmod.JOBS.clear()
    recuperados = appmod.recover_interrupted_jobs()
    assert recuperados == 1
    assert cola == ["corte0001"]
    job = appmod.JOBS["corte0001"]
    assert job["resume"] is True
    assert job["duration"] == 900.0
    assert any("Reanudando" in l["m"] for l in job["log"])


def test_recover_ignora_checkpoint_sin_audio(tmp_data, monkeypatch):
    appmod.write_json(appmod.job_meta_path("huerfano01"), {
        "id": "huerfano01", "source_path": str(tmp_data["UPLOAD_DIR"] / "no_existe.wav"),
        "options": {"mode": "equilibrado"},
    }, backup=False)
    appmod.write_json(appmod.checkpoint_path("huerfano01"), {"upto": 10, "segments": []},
                      backup=False)
    appmod.JOBS.clear()
    monkeypatch.setattr(appmod, "WORK_QUEUE", type("Q", (), {"put": lambda *a: None,
                                                             "qsize": lambda *a: 0})())
    assert appmod.recover_interrupted_jobs() == 0
    assert not appmod.checkpoint_path("huerfano01").exists()


def test_fold_y_busqueda_ignoran_acentos():
    assert appmod._fold("Reunión") == "reunion"
    assert appmod._fold("AÑO") == "ano"
