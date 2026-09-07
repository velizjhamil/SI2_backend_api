"""Servicio de correo saliente usando la API HTTPS de Resend.

Se usa una API HTTPS (https://api.resend.com) en vez de SMTP porque hosts
como Render bloquean el tráfico saliente hacia puertos SMTP (465/587) a
nivel de red — el error visto en logs era `OSError: [Errno 101] Network is
unreachable`, no un problema de credenciales ni de Gmail. HTTPS (443) sí
está permitido.

Si `RESEND_API_KEY` no está configurada, el envío real se omite y solo se
loguea el enlace de recuperación, de modo que el sistema no falla en
entornos de desarrollo sin la API key.
"""
import logging

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

RESEND_API_URL = "https://api.resend.com/emails"


def send_email(
    to_email: str,
    subject: str,
    body: str,
    html_body: str | None = None,
) -> None:
    """Envía un correo (texto plano + versión HTML opcional) vía Resend.

    Si no hay API key configurada, se loguea el contenido en lugar de
    enviarlo realmente (útil para desarrollo).
    """
    if not settings.RESEND_API_KEY:
        logger.warning(
            "RESEND_API_KEY no configurada. No se envió correo real a %s. "
            "Asunto: %s | Cuerpo: %s",
            to_email, subject, body,
        )
        return

    payload = {
        "from": settings.RESEND_FROM,
        "to": [to_email],
        "subject": subject,
        "text": body,
    }
    if html_body:
        payload["html"] = html_body

    logger.info("Enviando correo a %s vía Resend", to_email)
    try:
        response = httpx.post(
            RESEND_API_URL,
            json=payload,
            headers={"Authorization": f"Bearer {settings.RESEND_API_KEY}"},
            timeout=10,
        )
        response.raise_for_status()
        logger.info(
            "Correo enviado correctamente a %s (id=%s)",
            to_email, response.json().get("id"),
        )
    except httpx.HTTPStatusError as exc:
        logger.error(
            "Resend rechazó el correo a %s (asunto=%s): %s %s",
            to_email, subject, exc.response.status_code, exc.response.text,
        )
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "Fallo enviando correo a %s (asunto=%s): %s",
            to_email, subject, exc,
        )
        raise


def send_password_reset_email(to_email: str, reset_token: str) -> None:
    """Envía (o loguea) el correo con el enlace de recuperación de contraseña.

    El enlace apunta a `${APP_FRONTEND_URL}/recuperar/<token>` y vence en
    `settings.PASSWORD_RESET_TOKEN_EXPIRE_MINUTES` minutos.
    """
    reset_url = f"{settings.APP_FRONTEND_URL.rstrip('/')}/recuperar/{reset_token}"
    expire_min = settings.PASSWORD_RESET_TOKEN_EXPIRE_MINUTES
    subject = "Recuperación de contraseña · CoopIA"

    text_body = (
        "Hola,\n\n"
        "Recibimos una solicitud para restablecer tu contraseña en CoopIA.\n\n"
        "Ingresa al siguiente enlace para elegir una nueva contraseña:\n"
        f"{reset_url}\n\n"
        f"Este enlace expira en {expire_min} minutos.\n\n"
        "Si no solicitaste este cambio, puedes ignorar este correo.\n\n"
        "— CoopIA"
    )

    # Versión HTML — mejora la deliverability (multipart/alternative) y reduce
    # que los filtros lo señalen como "correo simple sin estructura".
    safe_url = reset_url  # generado por el servidor, no input de usuario
    html_body = f"""\
<html>
  <body style="font-family: -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif;
               color: #1f2937; line-height: 1.5;">
    <h2 style="color:#1a4731; margin:0 0 12px;">Recuperación de contraseña</h2>
    <p>Hola,</p>
    <p>Recibimos una solicitud para restablecer tu contraseña en <strong>CoopIA</strong>.</p>
    <p style="margin:24px 0;">
      <a href="{safe_url}"
         style="display:inline-block;padding:12px 22px;border-radius:10px;
                background:#1a4731;color:#ffffff;text-decoration:none;
                font-weight:600;">
        Restablecer mi contraseña
      </a>
    </p>
    <p>O copia y pega este enlace en tu navegador:<br>
      <span style="color:#2563eb;word-break:break-all;">{safe_url}</span>
    </p>
    <p style="color:#6b7280;font-size:13px;">
      Este enlace expira en {expire_min} minutos. Si no solicitaste este cambio,
      puedes ignorar este mensaje.
    </p>
    <p style="color:#9ca3af;font-size:12px;margin-top:32px;">— Equipo CoopIA</p>
  </body>
</html>
"""
    send_email(to_email, subject, text_body, html_body)
