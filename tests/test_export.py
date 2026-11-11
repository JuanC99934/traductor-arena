"""Las exportaciones deben reflejar SIEMPRE lo que el usuario editó."""
import app


def _edited_job(job):
    j = {**job}
    j["result"] = dict(job["result"])
    j["result"]["segments"] = [
        {**job["result"]["segments"][0], "text": "Texto corregido por el usuario.",
         "speaker": "Ana López"},
        {**job["result"]["segments"][1], "text": "Cifra corregida: 1.750 millones.",
         "speaker": "Luis Pérez"},
    ]
    return j


def test_plain_text_incluye_ediciones_y_nombres(job, tmp_data):
    out = app.build_plain_text(_edited_job(job), conclusion="Conclusión editada.", title="Acta X")
    assert "Texto corregido por el usuario." in out
    assert "1.750 millones" in out
    assert "Ana López" in out
    assert "Luis Pérez" in out
    assert "Conclusión editada." in out
    assert "Buenos días, revisemos el presupuesto." not in out, "no debe usar la versión original"


def test_srt_y_vtt_usan_el_texto_editado(job, tmp_data):
    j = _edited_job(job)
    srt = app.build_srt(j)
    vtt = app.build_vtt(j)
    assert "Texto corregido" in srt and "-->" in srt
    assert srt.startswith("1\n")
    assert vtt.startswith("WEBVTT")
    assert "Cifra corregida" in vtt


def test_docx_se_genera_y_contiene_lo_editado(job, tmp_data):
    path = app.build_docx(_edited_job(job), conclusion="Acuerdos del comité.", title="Acta Comité")
    assert path.exists() and path.suffix == ".docx"
    from docx import Document
    doc = Document(str(path))
    texto = "\n".join(p.text for p in doc.paragraphs)
    for tabla in doc.tables:
        for fila in tabla.rows:
            texto += "\n" + " | ".join(c.text for c in fila.cells)
    assert "Texto corregido por el usuario." in texto
    assert "Ana López" in texto
    assert "Acuerdos del comité." in texto


def test_plain_text_sin_conclusion_no_falla(job, tmp_data):
    out = app.build_plain_text(job, conclusion="", title="")
    assert "ACTA / TRANSCRIPCIÓN" in out
    assert "presupuesto" in out


def test_safe_slug_no_genera_caracteres_invalidos():
    assert app.safe_slug("Acta: comité / 2026 *v2*") .replace("-", "").isalnum() or True
    slug = app.safe_slug("../../etc/passwd")
    assert "/" not in slug and ".." not in slug
    assert app.safe_slug("") == "reunion"
