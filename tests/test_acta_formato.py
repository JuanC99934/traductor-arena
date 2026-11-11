"""Acta completa: redacción por secciones, limpieza de huecos y cifras verificables.

El motor de IA se sustituye por una función falsa: aquí se prueba la orquestación
(índice → secciones → acuerdos) y la limpieza del texto, no la calidad del modelo.
"""
import json

import pytest

import app


PLAN = """1. INSTALACIÓN DE LA SESIÓN Y ASISTENCIA — quórum y aprobación del acta anterior
2. COMISIÓN DE CONSTRUCCIÓN — evaluación de las propuestas de estacionamiento
3. INFORMES FINANCIEROS — resultados del trimestre
4. ACUERDOS Y COMPROMISOS — lo aprobado en la sesión"""

CUERPO_CONSTRUCCION = (
    "La comisión presentó tres propuestas para el estacionamiento.\n\n"
    "* **Constructora La Seca:** ofreció 500 cajones por $950,000 pesos en 8 meses.\n"
    "* **Grupo Véritce:** propuso 420 cajones por $1,200,000 pesos.\n\n"
    "Se descartó a Véritce por costo y se aprobó La Seca."
)
ACUERDOS_JSON = json.dumps({
    "acuerdos": [{"acuerdo": "Aprobar el proyecto de La Seca", "responsable": "Ing. Pérez",
                  "fecha": "diciembre de 2026", "estatus": "aprobado"}],
    "pendientes": [{"tema": "Firmar el contrato", "responsable": "Dirección Jurídica", "fecha": ""}],
    "siguientes_pasos": ["Enviar la minuta a los participantes"],
})


@pytest.fixture()
def motor_falso(monkeypatch):
    """Motor de IA falso: responde al índice, a cada sección y al JSON de acuerdos."""
    llamadas = {"plan": 0, "secciones": 0, "json": 0, "max_tokens": []}

    def _fake(prompt, system, engine, cfg, max_tokens, on_token=None):
        llamadas["max_tokens"].append(max_tokens)
        if "propón el índice" in prompt:
            llamadas["plan"] += 1
            txt = PLAN
        elif "extrae los acuerdos" in prompt:
            llamadas["json"] += 1
            txt = "```json\n" + ACUERDOS_JSON + "\n```"
        else:
            llamadas["secciones"] += 1
            if "CONSTRUCCIÓN" in prompt:
                txt = CUERPO_CONSTRUCCION
            elif "FINANCIEROS" in prompt:
                txt = ("El director de finanzas informó ingresos por $18,400,000 pesos, "
                       "un crecimiento de 12.63 % respecto al trimestre anterior.")
            else:
                txt = ("La sesión se instaló con la asistencia del director general. "
                       "Se verificó el quórum y se aprobó el acta anterior. [Nombre Presidencia]")
        if on_token:
            for ch in txt:
                on_token(ch)
        return txt

    monkeypatch.setattr(app, "_engine_tokens", _fake)
    monkeypatch.setattr(app, "ACTA_DIRECT_CHARS", 10 ** 9)
    monkeypatch.setattr(app, "ACTA_SECCIONES", True)
    return llamadas


SEGMENTOS = [
    {"start": i * 30.0, "end": i * 30.0 + 30,
     "text": f"Hablante {i % 3 + 1}: revisamos el punto {i} con $18,400,000 pesos y 12.63 %",
     "speaker": f"Hablante {i % 3 + 1}"}
    for i in range(6)
]


def _correr(**kw):
    eventos = list(app.acta_pipeline(SEGMENTOS, "acta_completa", "openai",
                                     {"openai_model": "falso"}, title="Junta Directiva", **kw))
    done = [e[1] for e in eventos if e[0] == "done"][0]
    return eventos, done


def test_limpiar_acta_quita_huecos_y_conserva_marcas_de_tiempo():
    texto = ("Se aprobó [Nombre Presidencia] el monto de [Monto] con [Fecha].\n"
             "El acuerdo quedó a las [01:12:35].\n\n\n\nOtra línea (  ) con espacios.")
    limpio = app.limpiar_acta(texto)
    assert "[Nombre" not in limpio and "[Monto]" not in limpio and "[Fecha]" not in limpio
    assert "[01:12:35]" in limpio            # las marcas de tiempo sí se conservan
    assert "\n\n\n" not in limpio


def test_limpiar_acta_elimina_parrafos_repetidos():
    parrafo = ("La comisión revisó las tres propuestas de estacionamiento y acordó "
               "descartar la segunda por su costo.")
    limpio = app.limpiar_acta(parrafo + "\n" + parrafo + "\nUna frase corta.\nUna frase corta.")
    assert limpio.count("descartar la segunda") == 1
    assert limpio.count("Una frase corta.") == 2   # las frases cortas no se tocan


def test_plan_de_secciones_se_parsea_con_titulo_y_detalle(motor_falso):
    plan = app._plan_acta_secciones("material", "Junta", "openai", {}, [])
    assert len(plan) == 4
    assert plan[0]["titulo"].startswith("INSTALACIÓN")
    assert "quórum" in plan[0]["detalle"]
    assert motor_falso["plan"] == 1


def test_acta_por_secciones_estructura_completa(motor_falso):
    eventos, done = _correr()
    texto = done["text"]
    etapas = [e[1]["stage"] for e in eventos if e[0] == "stage"]

    assert "secciones" in etapas
    # encabezado + secciones numeradas con separadores
    assert texto.startswith("**ACTA DE SESIÓN")
    for n in range(1, 5):
        assert f"**{n}. " in texto
    assert texto.count("---") >= 4
    assert "**CIERRE**" in texto and "Presidencia" in texto
    # contenido real de cada sección, no una lista de temas
    assert "Constructora La Seca" in texto and "$950,000 pesos" in texto
    assert "12.63 %" in texto
    # sin huecos y con los acuerdos estructurados
    assert "[" + "Nombre" not in texto
    assert done["acuerdos"][0]["acuerdo"] == "Aprobar el proyecto de La Seca"
    assert done["pendientes"][0]["tema"] == "Firmar el contrato"
    assert done["siguientes_pasos"] == ["Enviar la minuta a los participantes"]
    assert motor_falso["secciones"] == 4 and motor_falso["json"] == 1
    # el acta se escribe en varias llamadas, no en una sola pasada corta
    assert max(motor_falso["max_tokens"]) <= app.AI_FORMATS["acta_completa"]["max_tokens"]


def test_acta_por_secciones_emite_tokens_y_encabezados(motor_falso):
    eventos, done = _correr()
    tokens = "".join(e[1]["text"] for e in eventos if e[0] == "token")
    assert "**1. INSTALACIÓN" in tokens          # la vista previa ve los títulos
    assert "Constructora La Seca" in tokens
    assert done["text"].startswith("**ACTA DE SESIÓN")


def test_sin_indice_cae_a_una_sola_pasada(monkeypatch):
    def _fake(prompt, system, engine, cfg, max_tokens, on_token=None):
        return ""          # el planificador no devuelve nada

    monkeypatch.setattr(app, "_engine_tokens", _fake)
    monkeypatch.setattr(app, "ACTA_DIRECT_CHARS", 10 ** 9)
    monkeypatch.setattr(app, "ACTA_SECCIONES", True)
    # el planificador vacío no debe producir un acta vacía: se cae a la pasada única y,
    # como el motor tampoco responde, se reporta el error de siempre
    with pytest.raises(RuntimeError, match="no devolvió texto"):
        list(app.acta_pipeline(SEGMENTOS, "acta_completa", "openai", {}))


def test_seccion_fallida_no_deja_titulo_vacio(monkeypatch):
    def _fake(prompt, system, engine, cfg, max_tokens, on_token=None):
        if "propón el índice" in prompt:
            return PLAN
        if "extrae los acuerdos" in prompt:
            raise RuntimeError("sin acuerdos")
        if "FINANCIEROS" in prompt:
            raise RuntimeError("el motor se cayó")
        return "Cuerpo de la sección con datos concretos y su cifra de $18,400,000 pesos."

    monkeypatch.setattr(app, "_engine_tokens", _fake)
    monkeypatch.setattr(app, "ACTA_DIRECT_CHARS", 10 ** 9)
    monkeypatch.setattr(app, "ACTA_SECCIONES", True)
    eventos = list(app.acta_pipeline(SEGMENTOS, "acta_completa", "openai", {}))
    done = [e[1] for e in eventos if e[0] == "done"][0]
    texto = done["text"]
    assert "INFORMES FINANCIEROS" not in texto      # la sección fallida no aparece vacía
    assert "**3. ACUERDOS" in texto                 # se renumera sin huecos
    assert any("FINANCIEROS" in w for w in done["warnings"])


def test_verify_figures_ignora_anios_y_cifras_menores():
    transcript = "[00:10] Ana: ingresos de 18400000 pesos, crecimiento de 12.63 por ciento"
    acta = "Los ingresos fueron de $18,400,000 pesos, un alza de 12.63 % en 2026 con 8 socios."
    res = app.verify_figures(acta, transcript)
    assert res["missing"] == []

    inventada = app.verify_figures("Se aprobaron $9,999,000 pesos", transcript)
    assert inventada["ok"] is False


def test_verify_figures_acepta_millones_y_mil_en_la_transcripcion():
    # la transcripción dice «1.2 millones» y el acta escribe «$1,200,000 pesos»
    transcript = "[00:30] Ana: el proyecto cuesta 1.2 millones y la obra son 950 mil pesos"
    res = app.verify_figures("El proyecto costó $1,200,000 pesos y la obra $950,000 pesos.", transcript)
    assert res["missing"] == []


def test_acta_completa_pide_mas_tokens_que_un_resumen():
    assert app.AI_FORMATS["acta_completa"]["max_tokens"] >= 6000
    assert app.AI_FORMATS["acta_completa"]["section_tokens"] >= 600
    assert app.AI_FORMATS["acta_completa"]["max_tokens"] > app.AI_FORMATS["resumen_directo"]["max_tokens"]


def test_las_instrucciones_prohiben_listas_de_temas_y_huecos():
    reglas = app.SECRETARY_SYSTEM_PROMPT
    assert "[Nombre Presidencia]" in reglas            # se prohíben los marcadores
    assert "palabras sueltas" in reglas
    instr = app.FORMAT_INSTRUCTIONS["acta_completa"]
    assert "SECCIONES TEMÁTICAS" in instr and "MAYÚSCULAS" in instr
    assert "viñetas" in instr and "---" in instr


def test_con_ia_local_se_piden_menos_secciones(monkeypatch):
    def _fake(prompt, system, engine, cfg, max_tokens, on_token=None):
        return "\n".join(f"{i}. SECCIÓN {i} — detalle {i}" for i in range(1, 9))

    monkeypatch.setattr(app, "_engine_tokens", _fake)
    cloud = app._plan_acta_secciones("material", "", "openai", {}, [])
    local = app._plan_acta_secciones("material", "", "local", {}, [])
    assert len(cloud) == app.ACTA_MAX_SECCIONES
    assert len(local) == app.ACTA_SECCIONES_LOCAL


def test_motor_local_respeta_el_limite_pedido(monkeypatch):
    visto = {}

    def _fake_local(prompt, system, model, max_new_tokens=None, on_token=None):
        visto["max_new"] = max_new_tokens
        return "texto"

    monkeypatch.setattr(app, "call_local_llm", _fake_local)
    app._engine_tokens("prompt", "sys", "local", {"local_model": "Qwen/Qwen2.5-3B-Instruct"}, 700, None)
    assert visto["max_new"] == 700                     # ni más ni menos de lo pedido
    app._engine_tokens("prompt", "sys", "local", {"local_model": "Qwen/Qwen2.5-3B-Instruct"}, 9000, None)
    assert visto["max_new"] == app.LOCAL_MODELS["Qwen/Qwen2.5-3B-Instruct"]["max_new"]
