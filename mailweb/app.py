import logging
import os
import re
import socket
import smtplib
import ssl
from email.header import Header
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from html import escape

from flask import Flask, jsonify, render_template, request
from werkzeug.middleware.proxy_fix import ProxyFix


logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("mailweb")

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024  # 1 MB
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
MAX_NAME_LEN = 100
MAX_SUBJECT_LEN = 200
MAX_MESSAGE_LEN = 5000


def str_to_bool(value: str, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def clean_text(value: str) -> str:
    return (value or "").replace("\x00", "").strip()


def ensure_safe_header(value: str, field_name: str) -> str:
    if "\r" in value or "\n" in value:
        raise ValueError(f"{field_name} 不能包含换行符")
    return value


def is_valid_email(value: str) -> bool:
    return bool(EMAIL_RE.fullmatch(value or ""))


def get_mail_config() -> dict:
    smtp_host = clean_text(os.getenv("SMTP_HOST", ""))
    if not smtp_host:
        raise RuntimeError("SMTP_HOST 未配置，推荐使用外部 SMTP 中继")

    try:
        smtp_port = int(clean_text(os.getenv("SMTP_PORT", "587")))
    except ValueError as exc:
        raise RuntimeError("SMTP_PORT 必须是整数") from exc

    if not 1 <= smtp_port <= 65535:
        raise RuntimeError("SMTP_PORT 超出有效范围")

    use_tls = str_to_bool(os.getenv("SMTP_USE_TLS"), default=True)
    use_ssl = str_to_bool(os.getenv("SMTP_USE_SSL"), default=False)
    if use_tls and use_ssl:
        raise RuntimeError("SMTP_USE_TLS 和 SMTP_USE_SSL 不能同时为 true")

    smtp_username = clean_text(os.getenv("SMTP_USERNAME", ""))
    smtp_password = os.getenv("SMTP_PASSWORD", "")
    if smtp_username and not smtp_password:
        raise RuntimeError("配置 SMTP_USERNAME 时必须同时配置 SMTP_PASSWORD")
    if smtp_password and not smtp_username:
        raise RuntimeError("配置 SMTP_PASSWORD 时必须同时配置 SMTP_USERNAME")

    mail_from = ensure_safe_header(clean_text(os.getenv("MAIL_FROM", "")), "MAIL_FROM")
    if not is_valid_email(mail_from):
        raise RuntimeError("MAIL_FROM 未配置或格式不正确")

    mail_from_name = ensure_safe_header(
        clean_text(os.getenv("MAIL_FROM_NAME", "Mail Web")),
        "MAIL_FROM_NAME",
    )

    mail_reply_to = ensure_safe_header(
        clean_text(os.getenv("MAIL_REPLY_TO", "")),
        "MAIL_REPLY_TO",
    )
    if mail_reply_to and not is_valid_email(mail_reply_to):
        raise RuntimeError("MAIL_REPLY_TO 格式不正确")

    try:
        mail_timeout = int(clean_text(os.getenv("MAIL_TIMEOUT", "15")))
    except ValueError as exc:
        raise RuntimeError("MAIL_TIMEOUT 必须是整数") from exc

    if mail_timeout < 1:
        raise RuntimeError("MAIL_TIMEOUT 必须大于 0")

    return {
        "smtp_host": smtp_host,
        "smtp_port": smtp_port,
        "smtp_username": smtp_username,
        "smtp_password": smtp_password,
        "use_tls": use_tls,
        "use_ssl": use_ssl,
        "mail_from": mail_from,
        "mail_from_name": mail_from_name,
        "mail_reply_to": mail_reply_to,
        "mail_timeout": mail_timeout,
    }


def normalize_form_data(raw: dict) -> dict:
    return {
        "name": clean_text(raw.get("name")),
        "email": clean_text(raw.get("email")),
        "to_email": clean_text(raw.get("to_email")),
        "subject": clean_text(raw.get("subject")),
        "message": clean_text(raw.get("message")),
    }


def validate_form(data: dict) -> dict:
    errors = {}

    if not data["name"]:
        errors["name"] = "姓名不能为空"
    elif len(data["name"]) > MAX_NAME_LEN:
        errors["name"] = f"姓名不能超过 {MAX_NAME_LEN} 个字符"

    if not data["email"]:
        errors["email"] = "联系邮箱不能为空"
    elif not is_valid_email(data["email"]):
        errors["email"] = "联系邮箱格式不正确"

    if not data["to_email"]:
        errors["to_email"] = "收件人邮箱不能为空"
    elif not is_valid_email(data["to_email"]):
        errors["to_email"] = "收件人邮箱格式不正确"

    if not data["subject"]:
        errors["subject"] = "邮件主题不能为空"
    elif len(data["subject"]) > MAX_SUBJECT_LEN:
        errors["subject"] = f"邮件主题不能超过 {MAX_SUBJECT_LEN} 个字符"
    else:
        try:
            ensure_safe_header(data["subject"], "邮件主题")
        except ValueError as exc:
            errors["subject"] = str(exc)

    if not data["message"]:
        errors["message"] = "邮件正文不能为空"
    elif len(data["message"]) > MAX_MESSAGE_LEN:
        errors["message"] = f"邮件正文不能超过 {MAX_MESSAGE_LEN} 个字符"

    return errors


def build_plain_text_body(data: dict) -> str:
    return (
        "这是一封由网页邮件发送系统投递的事务性邮件。\n\n"
        f"收件人邮箱: {data['to_email']}\n"
        f"提交人姓名: {data['name']}\n"
        f"联系邮箱: {data['email']}\n"
        f"邮件主题: {data['subject']}\n\n"
        "正文内容:\n"
        f"{data['message']}\n"
    )


def build_html_body(data: dict) -> str:
    message_html = "<br>".join(escape(line) for line in data["message"].splitlines())
    if not message_html:
        message_html = escape(data["message"])

    return f"""<!DOCTYPE html>
<html lang=\"zh-CN\">
<body style=\"margin:0;padding:24px;font-family:Arial,'Microsoft YaHei',sans-serif;color:#1f2937;background:#f8fafc;\">
  <div style=\"max-width:720px;margin:0 auto;background:#ffffff;border:1px solid #e5e7eb;border-radius:12px;padding:24px;\">
    <h2 style=\"margin:0 0 16px;font-size:20px;\">事务性邮件通知</h2>
    <p style=\"margin:0 0 16px;line-height:1.7;\">这是一封由网页邮件发送系统投递的事务性邮件，请查看以下内容。</p>
    <table style=\"width:100%;border-collapse:collapse;margin:0 0 18px;\">
      <tr><td style=\"padding:8px 0;color:#6b7280;width:120px;\">收件人邮箱</td><td style=\"padding:8px 0;\">{escape(data['to_email'])}</td></tr>
      <tr><td style=\"padding:8px 0;color:#6b7280;\">提交人姓名</td><td style=\"padding:8px 0;\">{escape(data['name'])}</td></tr>
      <tr><td style=\"padding:8px 0;color:#6b7280;\">联系邮箱</td><td style=\"padding:8px 0;\">{escape(data['email'])}</td></tr>
      <tr><td style=\"padding:8px 0;color:#6b7280;\">邮件主题</td><td style=\"padding:8px 0;\">{escape(data['subject'])}</td></tr>
    </table>
    <div style=\"padding:16px;background:#f9fafb;border-radius:8px;line-height:1.8;\">
      {message_html}
    </div>
  </div>
</body>
</html>"""


def build_email(data: dict, mail_config: dict) -> EmailMessage:
    message = EmailMessage()
    message["Subject"] = ensure_safe_header(data["subject"], "邮件主题")
    message["From"] = formataddr(
        (
            str(Header(mail_config["mail_from_name"], "utf-8")),
            mail_config["mail_from"],
        )
    )
    # 推荐使用外部 SMTP 中继，并让 MAIL_FROM 尽量与 SMTP 账号所属域一致。
    # 发给 QQ 邮箱时，发信域最好能通过 SPF 或 DKIM，且 From / Sender 应保持一致。
    message["Sender"] = mail_config["mail_from"]
    message["To"] = data["to_email"]
    message["Date"] = formatdate(localtime=False)
    message["Message-ID"] = make_msgid(domain=mail_config["mail_from"].split("@", 1)[1])

    if mail_config["mail_reply_to"]:
        message["Reply-To"] = mail_config["mail_reply_to"]

    message.set_content(build_plain_text_body(data), subtype="plain", charset="utf-8")
    message.add_alternative(
        build_html_body(data),
        subtype="html",
        charset="utf-8",
    )
    return message


def send_mail(data: dict) -> None:
    mail_config = get_mail_config()
    message = build_email(data, mail_config)
    tls_context = ssl.create_default_context()

    smtp_kwargs = {
        "host": mail_config["smtp_host"],
        "port": mail_config["smtp_port"],
        "timeout": mail_config["mail_timeout"],
    }
    if mail_config["use_ssl"]:
        smtp_kwargs["context"] = tls_context
        smtp = smtplib.SMTP_SSL(**smtp_kwargs)
    else:
        smtp = smtplib.SMTP(**smtp_kwargs)

    with smtp:
        smtp.ehlo()

        if mail_config["use_tls"]:
            smtp.starttls(context=tls_context)
            smtp.ehlo()

        if mail_config["smtp_username"]:
            smtp.login(
                mail_config["smtp_username"],
                mail_config["smtp_password"],
            )

        smtp.send_message(
            message,
            from_addr=mail_config["mail_from"],
            to_addrs=[data["to_email"]],
        )


@app.get("/")
def index():
    return render_template("index.html")


@app.post("/send")
def send():
    payload = request.get_json(silent=True)
    if payload is None:
        payload = request.form.to_dict()

    data = normalize_form_data(payload)
    errors = validate_form(data)

    if errors:
        logger.warning(
            "Form validation failed from ip=%s receiver=%s errors=%s",
            request.remote_addr,
            data["to_email"],
            errors,
        )
        return (
            jsonify(
                {
                    "ok": False,
                    "error": "表单校验失败",
                    "errors": errors,
                }
            ),
            400,
        )

    try:
        send_mail(data)
    except RuntimeError as exc:
        logger.error("Mail configuration error: %s", exc)
        return (
            jsonify(
                {
                    "ok": False,
                    "error": f"邮件服务配置错误: {exc}",
                }
            ),
            500,
        )
    except smtplib.SMTPAuthenticationError as exc:
        logger.warning(
            "SMTP authentication failed host=%s receiver=%s code=%s",
            clean_text(os.getenv("SMTP_HOST", "")),
            data["to_email"],
            getattr(exc, "smtp_code", ""),
        )
        return (
            jsonify(
                {
                    "ok": False,
                    "error": "SMTP 认证失败，请检查 SMTP_USERNAME / SMTP_PASSWORD",
                }
            ),
            502,
        )
    except smtplib.SMTPRecipientsRefused as exc:
        logger.warning(
            "SMTP recipient refused receiver=%s detail=%s",
            data["to_email"],
            exc.recipients,
        )
        return (
            jsonify(
                {
                    "ok": False,
                    "error": "SMTP 拒绝该收件人地址，请检查收件箱地址或上游 SMTP 策略",
                }
            ),
            502,
        )
    except (socket.timeout, TimeoutError) as exc:
        logger.warning("SMTP timeout receiver=%s error=%s", data["to_email"], exc)
        return (
            jsonify(
                {
                    "ok": False,
                    "error": "连接 SMTP 超时，请稍后重试",
                }
            ),
            504,
        )
    except smtplib.SMTPException as exc:
        logger.exception(
            "SMTP error from ip=%s receiver=%s",
            request.remote_addr,
            data["to_email"],
        )
        return (
            jsonify(
                {
                    "ok": False,
                    "error": f"SMTP 发送失败: {exc}",
                }
            ),
            502,
        )
    except OSError as exc:
        logger.exception(
            "Mail transport error from ip=%s receiver=%s",
            request.remote_addr,
            data["to_email"],
        )
        return (
            jsonify(
                {
                    "ok": False,
                    "error": f"网络或连接异常: {exc}",
                }
            ),
            502,
        )
    except Exception as exc:
        logger.exception(
            "Unexpected mail error from ip=%s receiver=%s",
            request.remote_addr,
            data["to_email"],
        )
        return (
            jsonify(
                {
                    "ok": False,
                    "error": f"邮件发送失败: {exc}",
                }
            ),
            500,
        )

    logger.info(
        "Mail sent successfully from ip=%s receiver=%s subject=%s",
        request.remote_addr,
        data["to_email"],
        data["subject"],
    )
    return jsonify({"ok": True, "message": "邮件发送成功。"}), 200


if __name__ == "__main__":
    app.run(
        host=os.getenv("APP_HOST", "127.0.0.1"),
        port=int(os.getenv("APP_PORT", "5000")),
        debug=False,
    )