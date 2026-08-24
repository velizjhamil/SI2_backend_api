"""Servicio de correo saliente usando SMTP (Gmail + contraseña de aplicación).

No introduce dependencias externas: usa únicamente `smtplib` y `email` de la
stdlib. La configuración (host, puerto, usuario, contraseña de aplicación de
Google) proviene de `settings` / variables de entorno.

Si `SMTP_USER` o `SMTP_PASSWORD` no están configurados, el envío real se omite
y solo se loguea el enlace de recuperación, de modo que el sistema no falla en
entornos de desarrollo sin SMTP.

Los mensajes se envían como `multipart/alternative` con versión HTML además
del texto plano, lo que mejora la deliverability (los filtros antispam penalizan
correo de "una sola parte" sin estructura MIME estándar).
"""
import logging
import smtplib
from email.message import EmailMessage

from app.core.config import settings

logger = logging.getLogger(__name__)


def _build_message(
    to_email: str,
    subject: str,
    text_body: str,
    html_body: str | None = None,
) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = settings.SMTP_FROM or settings.SMTP_USER
    msg["To"] = to_email
    # multipart/alternative: clientes modernos prefieren HTML, fallback a texto
    msg.set_content(text_body)
    if html_body:
        msg.add_alternative(html_body, subtype="html")
    return msg


def send_email(
    to_email: str,
    subject: str,
    body: str,
    html_body: str | None = None,
) -> None:
    """Envía un correo (texto plano + versión HTML opcional).

    Si no hay credenciales SMTP configuradas, se loguea el contenido en lugar
    de enviarlo realmente (útil para desarrollo).
    """
    if not settings.SMTP_USER or not settings.SMTP_PASSWORD:
        logger.warning(
            "SMTP no configurado. No se envió correo real a %s. "
            "Asunto: %s | Cuerpo: %s",
            to_email, subject, body,
        )
        return

    msg = _build_message(to_email, subject, body, html_body)
    try:
        if settings.SMTP_USE_TLS:
            with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT) as server:
                server.starttls()
                server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
                server.send_message(msg)
        else:
            with smtplib.SMTP_SSL(settings.SMTP_HOST, settings.SMTP_PORT) as server:
                server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
                server.send_message(msg)
    except Exception as exc:  # noqa: BLE001
        # Imprimimos la traza completa para que sea diagnosticable desde logs
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

