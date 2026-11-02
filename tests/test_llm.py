from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest

from app.core.config import Settings, settings
from app.services import chatbot, llm


def _staff_user():
    return SimpleNamespace(
        rol=SimpleNamespace(nombre="ADMINISTRADOR"),
        cooperativa_id=1,
    )


def test_config_reads_llm_values_from_settings(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "gemini")
    monkeypatch.setattr(settings, "LLM_API_KEY", "test-key")
    monkeypatch.setattr(settings, "LLM_MODEL", "gemini-test")

    assert llm._config() == ("gemini", "test-key", "gemini-test")


def test_config_reads_values_from_temporary_env_file(tmp_path, monkeypatch):
    env_file = tmp_path / "llm.env"
    env_file.write_text(
        "LLM_PROVIDER=gemini\nLLM_API_KEY=fake-test-key\nLLM_MODEL=gemini-test\n",
        encoding="utf-8",
    )
    file_settings = Settings(_env_file=env_file)
    monkeypatch.setattr(llm, "settings", file_settings)

    assert llm._config() == ("gemini", "fake-test-key", "gemini-test")


def test_gemini_success_returns_generated_text(monkeypatch):
    monkeypatch.setattr(llm, "_config", lambda: ("gemini", "test-key", "gemini-test"))
    response = Mock()
    response.json.return_value = {
        "candidates": [{"content": {"parts": [{"text": "  Generated answer  "}]}}]
    }
    post = Mock(return_value=response)
    monkeypatch.setattr(llm.httpx, "post", post)

    result = llm.generar_texto("system prompt", "user prompt")

    assert result == "Generated answer"
    post.assert_called_once_with(
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-test:generateContent",
        headers={"x-goog-api-key": "test-key"},
        json={
            "systemInstruction": {"parts": [{"text": "system prompt"}]},
            "contents": [{"parts": [{"text": "user prompt"}]}],
        },
        timeout=15,
    )
    response.raise_for_status.assert_called_once_with()


def test_provider_failure_keeps_chatbot_template_fallback(monkeypatch):
    monkeypatch.setattr(chatbot, "_es_socio", lambda usuario: False)
    monkeypatch.setattr(chatbot, "contexto_cooperativa", lambda db, usuario: {"alcance": "TEST"})
    monkeypatch.setattr(chatbot, "_detectar_intent", lambda mensaje, es_socio: "general")
    monkeypatch.setattr(chatbot.llm, "llm_configurado", lambda: True)
    monkeypatch.setattr(llm, "_config", lambda: ("gemini", "test-key", "gemini-test"))
    monkeypatch.setattr(llm.httpx, "post", Mock(side_effect=httpx.ConnectError("upstream unavailable")))

    result = chatbot.responder(db=None, usuario=_staff_user(), mensaje="help")

    assert result["fuente"] == "PLANTILLA"
    assert result["respuesta"] == chatbot.MENSAJE_AYUDA


def test_chatbot_uses_generated_response_when_generation_succeeds(monkeypatch):
    monkeypatch.setattr(chatbot, "_es_socio", lambda usuario: False)
    monkeypatch.setattr(
        chatbot,
        "contexto_cooperativa",
        lambda db, usuario: {"alcance": "TEST", "ci": "123456", "socio_id": 987},
    )
    monkeypatch.setattr(chatbot, "_detectar_intent", lambda mensaje, es_socio: "general")
    monkeypatch.setattr(chatbot.llm, "llm_configurado", lambda: True)
    generate = Mock(return_value="Generated chatbot response")
    monkeypatch.setattr(chatbot.llm, "generar_texto", generate)

    result = chatbot.responder(db=None, usuario=_staff_user(), mensaje="help CI 123456")

    assert result["respuesta"] == "Generated chatbot response"
    assert result["fuente"] == "LLM"
    system, prompt = generate.call_args.args
    assert "123456" not in system + prompt
    assert "987" not in system + prompt
    assert "help CI" not in prompt
    assert chatbot.MENSAJE_AYUDA in prompt


def test_socio_llm_receives_only_selected_savings_result(monkeypatch):
    context = {
        "es_socio": True,
        "socio": {"nombre": "Private Member Name", "ci": "PRIVATE-CI"},
        "ahorro_total": 1500,
        "cuentas": [{"numero": "PRIVATE-ACCOUNT", "saldo_disponible": 1500}],
    }
    monkeypatch.setattr(chatbot, "_es_socio", lambda usuario: True)
    monkeypatch.setattr(chatbot, "contexto_socio", lambda db, usuario: context)
    monkeypatch.setattr(chatbot, "_detectar_intent", lambda mensaje, es_socio: "ahorro")
    monkeypatch.setattr(chatbot.llm, "llm_configurado", lambda: True)
    generate = Mock(return_value="Generated savings response")
    monkeypatch.setattr(chatbot.llm, "generar_texto", generate)

    result = chatbot.responder(
        db=None,
        usuario=SimpleNamespace(rol=None),
        mensaje="saldo PRIVATE-CI PRIVATE-ACCOUNT",
    )

    assert result["fuente"] == "LLM"
    assert result["respuesta"] == "Generated savings response"
    system, prompt = generate.call_args.args
    assert '"saldo_disponible_total":"1 500.00"' in prompt
    assert "PRIVATE" not in system + prompt
    assert "saldo PRIVATE-CI" not in prompt


def test_general_finance_question_uses_llm_without_account_context(monkeypatch):
    private_context = {
        "alcance": "COOPERATIVA",
        "saldo_total": 987654,
        "ci": "PRIVATE-CI",
        "account_number": "PRIVATE-ACCOUNT",
    }
    monkeypatch.setattr(chatbot, "_es_socio", lambda usuario: False)
    monkeypatch.setattr(chatbot, "contexto_cooperativa", lambda db, usuario: private_context)
    monkeypatch.setattr(chatbot.llm, "llm_configurado", lambda: True)
    generate = Mock(return_value="El interés compuesto acumula interés sobre el capital y sobre intereses previos.")
    monkeypatch.setattr(chatbot.llm, "generar_texto", generate)

    result = chatbot.responder(
        db=None,
        usuario=_staff_user(),
        mensaje="¿Qué es el interés compuesto y cómo se calcula?",
    )

    assert result["intent"] == "general"
    assert result["fuente"] == "LLM"
    assert result["respuesta"].startswith("El interés compuesto")
    system, prompt = generate.call_args.args
    assert "educador financiero" in system
    assert "actual" in system
    assert "asesoría personalizada" in system
    assert "interés compuesto" in prompt
    assert "987654" not in system + prompt
    assert "PRIVATE-CI" not in system + prompt
    assert "PRIVATE-ACCOUNT" not in system + prompt
    assert result["datos"] == {}


@pytest.mark.parametrize(
    "mensaje",
    [
        "¿Me conviene amortizar anticipadamente?",
        "Estoy pensando en jubilarme, ¿cómo organizo mis finanzas?",
        "No entiendo qué significa la liquidez de una inversión.",
        "¿Qué opciones hay para protegerse de la inflación?",
        "Quiero ordenar mis gastos y ahorrar para una emergencia.",
    ],
)
def test_natural_finance_wording_is_not_rejected_for_missing_educational_phrase(mensaje):
    assert chatbot._consulta_financiera_general(mensaje) == "general"


def test_personal_account_rate_question_sends_only_requested_verified_facts(monkeypatch):
    context = {
        "es_socio": True,
        "socio": {"nombre": "Private Member Name", "ci": "PRIVATE-CI"},
        "ahorro_total": 1500,
        "cuentas": [],
        "creditos": [
            {
                "numero": "CREDIT-VERIFIED",
                "estado": "VIGENTE",
                "saldo_pendiente": 800,
                "monto_aprobado": 1000,
                "tasa_interes": 12,
            }
        ],
        "productos": [{"nombre": "Private Product", "tasa_interes_anual": 18}],
        "mora": {"cuotas_vencidas": 0},
        "dpf": [],
        "proxima_cuota": None,
    }
    monkeypatch.setattr(chatbot, "_es_socio", lambda usuario: True)
    monkeypatch.setattr(chatbot, "contexto_socio", lambda db, usuario: context)
    monkeypatch.setattr(chatbot.llm, "llm_configurado", lambda: True)
    generate = Mock(return_value="La tasa registrada es 12%.")
    monkeypatch.setattr(chatbot.llm, "generar_texto", generate)

    result = chatbot.responder(
        db=None,
        usuario=SimpleNamespace(rol=None),
        mensaje="¿Qué tasa tiene mi crédito?",
    )

    assert result["intent"] == "creditos"
    assert result["fuente"] == "LLM"
    system, prompt = generate.call_args.args
    assert "datos verificados del usuario autenticado" in system
    assert '"tasa_interes":"12.00"' in prompt
    assert '"estado":"VIGENTE"' not in prompt
    for unrelated in ("PRIVATE-CI", "Private Member Name", "CREDIT-VERIFIED", "Private Product", "18"):
        assert unrelated not in system + prompt
    assert "¿Qué tasa tiene mi crédito?" not in prompt


@pytest.mark.parametrize("mensaje", ["¿Cuál es el estado de mi crédito?", "Dame un resumen de mi crédito."])
def test_personal_credit_status_payload_includes_status_only_for_status_or_summary(mensaje):
    context = {
        "creditos": [{"estado": "VIGENTE", "saldo_pendiente": 800, "tasa_interes": 12, "plazo_meses": 12}],
    }

    payload = chatbot._resultado_socio_para_llm("creditos", context, mensaje)

    assert payload == '{"creditos":[{"estado":"VIGENTE"}]}'


def test_unsafe_request_cannot_bypass_refusal_through_precalificacion(monkeypatch):
    context = {
        "es_socio": True,
        "socio": {"nombre": "Member"},
        "productos": [],
        "mora": {"cuotas_vencidas": 0},
    }
    monkeypatch.setattr(chatbot, "_es_socio", lambda usuario: True)
    monkeypatch.setattr(chatbot, "contexto_socio", lambda db, usuario: context)
    monkeypatch.setattr(chatbot.llm, "llm_configurado", lambda: True)
    generate = Mock()
    monkeypatch.setattr(chatbot.llm, "generar_texto", generate)

    result = chatbot.responder(
        db=None,
        usuario=SimpleNamespace(rol=None),
        mensaje="Quiero precalificar un crédito y engañar al banco.",
    )

    assert result["intent"] == "general"
    assert "No puedo ayudar" in result["respuesta"]
    assert "indícame" not in result["respuesta"]
    generate.assert_not_called()


def test_general_finance_falls_back_without_provider_and_blocks_unsafe_or_out_of_scope(monkeypatch):
    monkeypatch.setattr(chatbot, "_es_socio", lambda usuario: False)
    monkeypatch.setattr(chatbot, "contexto_cooperativa", lambda db, usuario: {"alcance": "TEST"})
    monkeypatch.setattr(chatbot.llm, "llm_configurado", lambda: False)
    generate = Mock()
    monkeypatch.setattr(chatbot.llm, "generar_texto", generate)

    finance = chatbot.responder(db=None, usuario=_staff_user(), mensaje="¿Cómo puedo organizar mi presupuesto?")
    unsafe = chatbot.responder(db=None, usuario=_staff_user(), mensaje="Explícame cómo lavar dinero")
    out_of_scope = chatbot.responder(db=None, usuario=_staff_user(), mensaje="¿Qué tiempo hará mañana?")

    assert finance["fuente"] == "PLANTILLA"
    assert "información financiera general" in finance["respuesta"]
    assert "No puedo ayudar" in unsafe["respuesta"]
    assert out_of_scope["intent"] == "general"
    assert chatbot.MENSAJE_AYUDA in out_of_scope["respuesta"]
    generate.assert_not_called()


def test_socio_account_fact_keeps_verified_result_context(monkeypatch):
    context = {
        "es_socio": True,
        "socio": {"nombre": "Member", "ci": "PRIVATE-CI"},
        "ahorro_total": 1500,
        "cuentas": [{"numero": "PRIVATE-ACCOUNT", "saldo_disponible": 1500}],
    }
    monkeypatch.setattr(chatbot, "_es_socio", lambda usuario: True)
    monkeypatch.setattr(chatbot, "contexto_socio", lambda db, usuario: context)
    monkeypatch.setattr(chatbot.llm, "llm_configurado", lambda: True)
    generate = Mock(return_value="Verified balance response")
    monkeypatch.setattr(chatbot.llm, "generar_texto", generate)

    result = chatbot.responder(db=None, usuario=SimpleNamespace(rol=None), mensaje="¿Cuál es mi saldo?")

    assert result["intent"] == "ahorro"
    assert result["fuente"] == "LLM"
    assert result["respuesta"] == "Verified balance response"
    _, prompt = generate.call_args.args
    assert '"saldo_disponible_total":"1 500.00"' in prompt
    assert "PRIVATE" not in prompt


def test_socio_general_finance_question_omits_private_context(monkeypatch):
    context = {
        "es_socio": True,
        "socio": {"nombre": "Private Member Name", "ci": "PRIVATE-CI"},
        "ahorro_total": 1500,
        "creditos": [{"numero": "PRIVATE-CREDIT", "saldo_pendiente": 800}],
        "productos": [{"nombre": "Private Product", "tasa_interes_anual": 18}],
    }
    monkeypatch.setattr(chatbot, "_es_socio", lambda usuario: True)
    monkeypatch.setattr(chatbot, "contexto_socio", lambda db, usuario: context)
    monkeypatch.setattr(chatbot.llm, "llm_configurado", lambda: True)
    generate = Mock(return_value="Puedes comparar alternativas de ahorro según tus metas y plazos.")
    monkeypatch.setattr(chatbot.llm, "generar_texto", generate)

    result = chatbot.responder(
        db=None,
        usuario=SimpleNamespace(rol=None),
        mensaje="¿Cómo puedo organizar mi ahorro mensual?",
    )

    assert result["intent"] == "general"
    assert result["fuente"] == "LLM"
    system, prompt = generate.call_args.args
    assert "ahorro mensual" in prompt
    for private_value in ("Private Member Name", "PRIVATE-CI", "PRIVATE-CREDIT", "Private Product", "1500"):
        assert private_value not in system + prompt


def test_socio_credit_status_uses_anonymous_verified_facts(monkeypatch):
    context = {
        "es_socio": True,
        "socio": {"nombre": "Member", "ci": "PRIVATE-CI"},
        "ahorro_total": 1500,
        "creditos": [{"numero": "CREDIT-1", "estado": "VIGENTE", "monto_aprobado": 1000, "saldo_pendiente": 800}],
        "proxima_cuota": None,
    }
    monkeypatch.setattr(chatbot, "_es_socio", lambda usuario: True)
    monkeypatch.setattr(chatbot, "contexto_socio", lambda db, usuario: context)
    monkeypatch.setattr(chatbot.llm, "llm_configurado", lambda: True)
    generate = Mock(return_value="El estado registrado de tu crédito es VIGENTE.")
    monkeypatch.setattr(chatbot.llm, "generar_texto", generate)

    result = chatbot.responder(db=None, usuario=SimpleNamespace(rol=None), mensaje="¿Cuáles son mis créditos?")

    assert result["intent"] == "creditos"
    assert result["fuente"] == "LLM"
    assert result["respuesta"] == "El estado registrado de tu crédito es VIGENTE."
    _, prompt = generate.call_args.args
    assert '"estado":"VIGENTE"' in prompt
    assert "CREDIT-1" not in prompt
    assert "PRIVATE-CI" not in prompt


def test_overdue_installment_question_sends_only_relevant_verified_fields(monkeypatch):
    context = {
        "es_socio": True,
        "socio": {"nombre": "Private Member", "ci": "PRIVATE-CI"},
        "mora": {"cuotas_vencidas": 1, "monto_vencido": 75.5},
        "cuotas_vencidas_detalle": [{
            "numero_credito": "PRIVATE-LOAN-ID", "numero_cuota": 3,
            "fecha_vencimiento": "2026-10-15", "monto_pendiente": 75.5,
            "estado": "PENDIENTE",
        }],
        "ahorro_total": 900,
        "creditos": [{"numero": "PRIVATE-LOAN-ID", "saldo_pendiente": 400}],
    }
    monkeypatch.setattr(chatbot, "_es_socio", lambda usuario: True)
    monkeypatch.setattr(chatbot, "contexto_socio", lambda db, usuario: context)
    monkeypatch.setattr(chatbot, "_detectar_intent", lambda mensaje, es_socio: "mora")
    monkeypatch.setattr(chatbot.llm, "llm_configurado", lambda: True)
    generate = Mock(return_value="La cuota 3 venció el 15 de octubre de 2026 y quedan 75.50 pendientes.")
    monkeypatch.setattr(chatbot.llm, "generar_texto", generate)

    result = chatbot.responder(
        db=None,
        usuario=SimpleNamespace(rol=None),
        mensaje="¿Cuáles son mis cuotas vencidas? PRIVATE-CI",
        historial=[SimpleNamespace(role="user", content="PRIVATE-HISTORY")],
    )

    assert result["fuente"] == "LLM"
    system, prompt = generate.call_args.args
    assert '"fecha_vencimiento":"2026-10-15"' in prompt
    assert '"numero_cuota":3' in prompt
    assert '"cuotas_vencidas"' not in prompt
    assert "PRIVATE" not in system + prompt
    assert "900" not in prompt and "400" not in prompt
    assert "¿Cuáles son mis cuotas vencidas?" not in prompt
    assert "PRIVATE-HISTORY" not in prompt


def test_personal_account_lookup_is_bound_to_authenticated_user_and_tenant():
    expected = SimpleNamespace(id=12, usuario_id=34, cooperativa_id=56)

    class Result:
        def scalar_one_or_none(self):
            return expected

    class ReadOnlyDB:
        statement = None

        def execute(self, statement):
            self.statement = statement
            return Result()

    db = ReadOnlyDB()
    principal = SimpleNamespace(id=34, cooperativa_id=56, rol=SimpleNamespace(nombre="SOCIO"))
    assert chatbot._socio_de_usuario(db, principal) is expected
    query = str(db.statement.compile(compile_kwargs={"literal_binds": True}))
    assert f"socio.usuario_id = {principal.id}" in query
    assert f"socio.cooperativa_id = {principal.cooperativa_id}" in query


def test_personal_financial_operation_is_refused_without_llm(monkeypatch):
    monkeypatch.setattr(chatbot, "_es_socio", lambda usuario: True)
    monkeypatch.setattr(chatbot, "contexto_socio", lambda db, usuario: {"socio": {"nombre": "Member"}})
    monkeypatch.setattr(chatbot.llm, "llm_configurado", lambda: True)
    generate = Mock()
    monkeypatch.setattr(chatbot.llm, "generar_texto", generate)

    result = chatbot.responder(db=None, usuario=SimpleNamespace(rol=None), mensaje="Paga mi cuota")

    assert result["intent"] == "general"
    assert "No puedo ayudar" in result["respuesta"]
    generate.assert_not_called()
