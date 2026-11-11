"""Pruebas de las funciones puras del backend (sin modelos ni audio real)."""
import app


def test_fix_question_marks_conserva_preguntas_reales():
    assert app.fix_question_marks("Buenos días ¿cómo están todos?") == "Buenos días ¿cómo están todos?"
    assert "¿Cuándo" in app.fix_question_marks("¿Cuándo entregamos el informe?")
    assert "¿verdad?" in app.fix_question_marks("Eso quedó claro, ¿verdad? sí")


def test_fix_question_marks_quita_signos_espurios():
    # Sin pronombre interrogativo y en mitad de la oración: se limpian
    assert app.fix_question_marks("revisamos ¿el presupuesto del próximo trimestre con finanzas?") == \
        "revisamos el presupuesto del próximo trimestre con finanzas"
    # Signo de apertura huérfano
    assert "¿" not in app.fix_question_marks("hablamos ¿ del tema pendiente")
    # Texto sin signos: no cambia
    assert app.fix_question_marks("hola equipo") == "hola equipo"


def test_fix_question_marks_vacio():
    assert app.fix_question_marks("") == ""


def _seg(text, start=0.0, end=None):
    return {"id": 0, "start": start, "end": end if end is not None else start + 2.0,
            "text": text, "avg_logprob": -0.2, "speaker": ""}


def test_collapse_repeats_elimina_bucles_de_alucinacion():
    segs = [_seg("Gracias por ver el video.", i * 3) for i in range(6)]
    out = app.collapse_repeats(segs)
    assert len(out) == 1
    assert out[0]["text"].startswith("Gracias")


def test_collapse_repeats_respeta_dos_repeticiones():
    segs = [_seg("Sí, correcto.", 0), _seg("Sí, correcto.", 4)]
    assert len(app.collapse_repeats(segs)) == 2


def test_collapse_repeats_quita_vacios():
    segs = [_seg("Texto uno"), _seg("   "), _seg("Texto dos")]
    out = app.collapse_repeats(segs)
    assert [s["text"] for s in out] == ["Texto uno", "Texto dos"]


def test_build_ai_transcript_respeta_presupuesto_y_orden():
    segs = [{"start": i * 10, "end": i * 10 + 9, "text": f"Intervención {i} con contenido",
             "speaker": "Ana"} for i in range(400)]
    text, used = app.build_ai_transcript(segs, budget=4000)
    assert used <= 4200
    assert text.startswith("[00:00]")
    assert text.index("Intervención 0") < text.rindex("Intervención")


def test_build_ai_transcript_vacio():
    assert app.build_ai_transcript([]) == ("", 0)


def test_evict_history_limita_entradas():
    entries = [{"job_id": f"j{i}", "created": f"2026-01-{i % 28 + 1:02d}T00:00:00", "media": ""}
               for i in range(app.MAX_HISTORY + 15)]
    kept = app.evict_history(entries)
    assert len(kept) == app.MAX_HISTORY


def test_split_acta_blocks_trocea_reuniones_largas():
    # 3 h de intervenciones de 30 s ⇒ bloques de ~20 min
    segs = []
    for i in range(360):
        segs.append({"start": i * 30.0, "end": i * 30.0 + 29.0,
                     "text": f"Intervención número {i} sobre el punto del orden del día.",
                     "speaker": "Ana"})
    blocks = app.split_acta_blocks(segs)
    assert len(blocks) >= 8
    assert all(len(b["text"]) <= app.ACTA_BLOCK_CHARS + 400 for b in blocks)
    assert blocks[0]["start"] == 0.0
    assert blocks[-1]["end"] <= 360 * 30.0
    # Los bloques cubren todos los segmentos sin repetirlos
    assert sum(len(b["segs"]) for b in blocks) == len(segs)


def test_verify_figures_detecta_cifras_inventadas():
    transcript = "[00:10] Ana: aprobamos 1500 millones para el proyecto"
    ok = app.verify_figures("Se aprobaron 1.500 millones [00:10]", transcript)
    assert ok["ok"] is True
    bad = app.verify_figures("Se aprobaron 7.300 millones", transcript)
    assert bad["ok"] is False
    assert "73" in bad["missing"] or "7300" in bad["missing"]


def test_verify_figures_ignora_marcas_de_tiempo():
    res = app.verify_figures("Acuerdo alcanzado [01:12:35]", "[00:01] hola")
    assert res["missing"] == []


def test_parse_agreements_extrae_json_y_limpia_texto():
    texto = """ACTA
Se aprobó el presupuesto.
---
```json
{"acuerdos": [{"acuerdo": "Aprobar presupuesto", "responsable": "Ana", "fecha": "30/11", "estatus": "hecho"}],
 "pendientes": [{"tema": "Enviar reporte", "responsable": "Luis", "fecha": "05/12"}],
 "siguientes_pasos": ["Convocar seguimiento"]}
```"""
    data, clean = app.parse_agreements(texto)
    assert data["acuerdos"][0]["responsable"] == "Ana"
    assert data["pendientes"][0]["tema"] == "Enviar reporte"
    assert data["siguientes_pasos"] == ["Convocar seguimiento"]
    assert "json" not in clean.lower()
    assert "Se aprobó el presupuesto" in clean


def test_parse_agreements_sin_json_usa_patrones():
    texto = "Se acuerda contratar al proveedor.\nQueda pendiente el informe de auditoría."
    data, clean = app.parse_agreements(texto)
    assert data["acuerdos"], "debe detectar el acuerdo por patrón"
    assert clean == texto.strip()


def test_window_plan_cubre_toda_la_duracion():
    plan = app._window_plan(3600.0)
    assert plan[0][0] == 0.0
    assert plan[-1][1] == 3600.0
    for (a1, b1), (a2, b2) in zip(plan, plan[1:]):
        assert a2 < b1, "las ventanas deben solaparse"
        assert b2 > b1


def test_window_plan_audio_corto_una_sola_ventana():
    assert app._window_plan(300.0) == [(0.0, 300.0)]


def test_voice_threshold_sin_voces_es_070():
    assert app.voice_match_threshold({"speakers": []}) == 0.70


def test_voice_threshold_calibrado_se_respeta():
    assert app.voice_match_threshold({"speakers": [], "eer_threshold": 0.81}) == 0.81


def test_join_same_speaker_segments_une_turnos():
    segs = [
        {"id": 0, "start": 0.0, "end": 5.0, "text": "Buenos días.", "speaker": "Ana"},
        {"id": 1, "start": 5.4, "end": 9.0, "text": "Empecemos.", "speaker": "Ana"},
        {"id": 2, "start": 9.5, "end": 12.0, "text": "De acuerdo.", "speaker": "Luis"},
    ]
    out = app.join_same_speaker_segments(segs)
    assert len(out) == 2
    assert out[0]["text"] == "Buenos días. Empecemos."
    assert [s["id"] for s in out] == [0, 1]


def test_join_same_speaker_no_une_pausas_largas():
    segs = [
        {"id": 0, "start": 0.0, "end": 5.0, "text": "Uno.", "speaker": "Ana"},
        {"id": 1, "start": 30.0, "end": 33.0, "text": "Dos.", "speaker": "Ana"},
    ]
    assert len(app.join_same_speaker_segments(segs)) == 2


def test_clean_segments_normaliza_y_descarta_vacios():
    out = app._clean_segments([
        {"start": "3.5", "end": "2.0", "text": "  hola  ", "speaker": "Ana"},
        {"start": 4, "end": 5, "text": "   "},
        {"text": "sin tiempos"},
    ])
    assert len(out) == 2
    assert out[0]["text"] == "hola"
    assert out[0]["end"] >= out[0]["start"]  # nunca end < start
    assert out[1]["start"] == 0.0


def test_fold_ignora_acentos_y_mayusculas():
    assert app._fold("Reunión de Dirección") == app._fold("reunion de direccion")
    assert "n" in app._fold("Año")
