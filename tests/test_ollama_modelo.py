"""Ollama: el modelo configurado tiene que existir de verdad.

El error típico era «El servidor local respondió 404: model 'qwen2.5:7b-instruct'
not found», sin decir al usuario qué hacer. Aquí se prueba que el mensaje sea
accionable y que la app elija sola un modelo que sí esté descargado.
"""
import io
import urllib.error

import app as appmod


def test_mejor_modelo_instalado_evita_embeddings_y_prefiere_instruct():
    modelos = ["nomic-embed-text:latest", "llama3.2:1b", "qwen2.5:3b-instruct"]
    assert appmod.mejor_modelo_instalado(modelos) == "qwen2.5:3b-instruct"
    assert appmod.mejor_modelo_instalado([]) == ""
    # sin instrucciones explícitas, prefiere el tamaño razonable para un acta
    assert appmod.mejor_modelo_instalado(["llama3.1:70b", "mistral:7b-instruct"]) == "mistral:7b-instruct"


def test_error_modelo_no_instalado_dice_el_comando_y_los_que_hay(monkeypatch):
    monkeypatch.setattr(appmod, "modelos_instalados",
                        lambda *a, **k: ["qwen2.5:3b-instruct", "llama3.2:1b"])
    texto = appmod.error_modelo_no_instalado("http://127.0.0.1:11434/v1", "qwen2.5:7b-instruct")
    assert "ollama pull qwen2.5:7b-instruct" in texto
    assert "qwen2.5:3b-instruct" in texto          # sugiere uno que sí existe
    assert "model 'qwen2.5:7b-instruct' not found" not in texto


def test_error_modelo_no_instalado_sin_modelos(monkeypatch):
    monkeypatch.setattr(appmod, "modelos_instalados", lambda *a, **k: [])
    texto = appmod.error_modelo_no_instalado("http://127.0.0.1:11434/v1", "mistral:7b-instruct")
    assert "ollama pull mistral:7b-instruct" in texto
    assert "No hay ningún modelo descargado" in texto


def test_http_404_de_ollama_se_traduce_a_instrucciones(monkeypatch):
    monkeypatch.setattr(appmod, "modelos_instalados", lambda *a, **k: ["qwen2.5:3b-instruct"])
    cuerpo = io.BytesIO(b'{"error":{"message":"model \'qwen2.5:7b-instruct\' not found",'
                        b'"type":"not_found_error"}}')
    exc = urllib.error.HTTPError("http://x/v1/chat/completions", 404, "Not Found", {}, cuerpo)
    msg = appmod._http_error_openai(exc, "http://127.0.0.1:11434/v1", "qwen2.5:7b-instruct")
    assert "ollama pull qwen2.5:7b-instruct" in msg and "qwen2.5:3b-instruct" in msg


def test_http_error_distinto_no_se_confunde_con_modelo_faltante(monkeypatch):
    monkeypatch.setattr(appmod, "modelos_instalados", lambda *a, **k: [])
    cuerpo = io.BytesIO(b'{"error":"boom"}')
    exc = urllib.error.HTTPError("http://x/v1/chat/completions", 500, "Server Error", {}, cuerpo)
    msg = appmod._http_error_openai(exc, "http://127.0.0.1:11434/v1", "m")
    assert msg.startswith("El servidor local respondió 500")


def test_ensure_ia_defaults_cambia_el_modelo_que_no_esta_descargado(tmp_path, monkeypatch):
    monkeypatch.setattr(appmod, "IA_CONFIG_FILE", tmp_path / "ia_config.json")
    appmod.write_json(appmod.IA_CONFIG_FILE,
                      {"engine": "openai", "openai_model": "qwen2.5:7b-instruct",
                       "openai_base_url": "http://127.0.0.1:11434/v1"})
    monkeypatch.setattr(appmod, "ia_engine_ready", lambda e: True)
    monkeypatch.setattr(appmod, "modelos_instalados",
                        lambda *a, **k: ["qwen2.5:3b-instruct", "llama3.2:1b"])
    cfg = appmod.ensure_ia_defaults()
    assert cfg["openai_model"] == "qwen2.5:3b-instruct"
    assert appmod._read_ia_config()["openai_model"] == "qwen2.5:3b-instruct"


def test_ensure_ia_defaults_respeta_el_modelo_que_si_existe(tmp_path, monkeypatch):
    monkeypatch.setattr(appmod, "IA_CONFIG_FILE", tmp_path / "ia_config.json")
    appmod.write_json(appmod.IA_CONFIG_FILE,
                      {"engine": "openai", "openai_model": "llama3.2:1b"})
    monkeypatch.setattr(appmod, "ia_engine_ready", lambda e: True)
    monkeypatch.setattr(appmod, "modelos_instalados", lambda *a, **k: ["llama3.2:1b", "qwen2.5:3b-instruct"])
    assert appmod.ensure_ia_defaults()["openai_model"] == "llama3.2:1b"


def test_ia_engine_falta_openai_sugiere_un_modelo_descargado(tmp_path, monkeypatch):
    monkeypatch.setattr(appmod, "IA_CONFIG_FILE", tmp_path / "ia_config.json")
    appmod.write_json(appmod.IA_CONFIG_FILE, {"engine": "openai",
                                              "openai_base_url": "http://127.0.0.1:11434/v1"})
    monkeypatch.setattr(appmod, "modelos_instalados", lambda *a, **k: ["qwen2.5:3b-instruct"])
    texto = appmod.ia_engine_falta("openai")
    assert "qwen2.5:3b-instruct" in texto and "Modelo libre" in texto


# --------------------------------------------------------------------------
# instalar.bat llama a:  python app.py --usar-ollama <modelo>
# --------------------------------------------------------------------------
def _cfg_tmp(tmp_path, monkeypatch, cfg=None):
    monkeypatch.setattr(appmod, "IA_CONFIG_FILE", tmp_path / "ia_config.json")
    appmod.write_json(appmod.IA_CONFIG_FILE, cfg or {"engine": "local"})


def test_usar_ollama_deja_la_app_configurada(tmp_path, monkeypatch):
    _cfg_tmp(tmp_path, monkeypatch)
    monkeypatch.setattr(appmod, "probe_openai_server",
                        lambda *a, **k: (True, ["qwen2.5:7b-instruct", "llama3.2:1b"], "ok"))
    res = appmod.aplicar_motor_ollama("qwen2.5:7b-instruct")
    assert res["ok"] is True
    cfg = appmod._read_ia_config()
    assert cfg["engine"] == "openai"
    assert cfg["openai_model"] == "qwen2.5:7b-instruct"
    assert cfg["openai_base_url"].endswith("11434/v1")
    assert "Listo" in res["mensaje"]


def test_usar_ollama_con_modelo_que_no_existe_no_configura(tmp_path, monkeypatch):
    _cfg_tmp(tmp_path, monkeypatch)
    monkeypatch.setattr(appmod, "probe_openai_server",
                        lambda *a, **k: (True, ["qwen2.5:3b-instruct"], "ok"))
    res = appmod.aplicar_motor_ollama("qwen2.5:7b-instruct")
    assert res["ok"] is False
    assert "ollama pull qwen2.5:7b-instruct" in res["mensaje"]
    assert res["sugerido"] == "qwen2.5:3b-instruct"
    assert appmod._read_ia_config()["engine"] == "local"     # no se toca


def test_usar_ollama_sin_servidor_avisa(tmp_path, monkeypatch):
    _cfg_tmp(tmp_path, monkeypatch)
    monkeypatch.setattr(appmod, "probe_openai_server", lambda *a, **k: (False, [], "no"))
    res = appmod.aplicar_motor_ollama("qwen2.5:7b-instruct")
    assert res["ok"] is False and "no responde" in res["mensaje"]


def test_usar_ollama_sin_modelo_elige_el_mejor(tmp_path, monkeypatch):
    _cfg_tmp(tmp_path, monkeypatch)
    monkeypatch.setattr(appmod, "probe_openai_server",
                        lambda *a, **k: (True, ["nomic-embed-text:latest", "qwen2.5:3b-instruct"], "ok"))
    res = appmod.aplicar_motor_ollama("")
    assert res["ok"] is True
    assert appmod._read_ia_config()["openai_model"] == "qwen2.5:3b-instruct"
