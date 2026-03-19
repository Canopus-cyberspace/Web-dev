import logging
import os
import re
import smtplib
from email.header import Header
from email.message import EmailMessage
from email.utils import formataddr

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


def get_smtp_config() -> dict:
    try:
        port = int(os.getenv("SMTP_PORT", "25"))
    except ValueError as exc:
        raise RuntimeError("SMTP_PORT 必须是整数") from exc

    use_tls = str_to_bool(os.getenv("SMTP_USE_TLS"), default=False)
    use_ssl = str_to_bool(os.getenv("SMTP_USE_SSL"), default=False)

    if use_tls and use_ssl:
        raise RuntimeError("SMTP_USE_TLS 和 SMTP_USE_SSL 不能同时为 true")

    username = os.getenv("SMTP_USERNAME", "").strip()
    password = os.getenv("SMTP_PASSWORD", "").strip()

    if bool(username) ^ bool(password):
        raise RuntimeError("SMTP_USERNAME 和 SMTP_PASSWORD 必须同时配置或同时留空")

    return {
        "host": os.getenv("SMTP_HOST", "127.0.0.1").strip(),
        "port": port,
        "username": username,
        "password": password,
        "use_tls": use_tls,
        "use_ssl": use_ssl,
        "from_email": os.getenv("SMTP_FROM_EMAIL", "").strip(),
        "timeout": int(os.getenv("SMTP_TIMEOUT", "10")),
    }


def is_valid_email(value: str) -> bool:
    return bool(EMAIL_RE.fullmatch(value or ""))


def normalize_form_data(raw: dict) -> dict:
    return {
        "name": (raw.get("name") or "").strip(),
        "email": (raw.get("email") or "").strip(),
        "to_email": (raw.get("to_email") or "").strip(),
        "subject": (raw.get("subject") or "").strip(),
        "message": (raw.get("message") or "").strip(),
    }


def validate_form(data: dict) -> dict:
    errors = {}

    if not data["name"]:
        errors["name"] = "姓名不能为空"
    elif len(data["name"]) > MAX_NAME_LEN:
        errors["name"] = f"姓名不能超过 {MAX_NAME_LEN} 个字符"

    if not data["email"]:
        errors["email"] = "发件人邮箱不能为空"
    elif not is_valid_email(data["email"]):
        errors["email"] = "发件人邮箱格式不正确"

    if not data["to_email"]:
        errors["to_email"] = "收件人邮箱不能为空"
    elif not is_valid_email(data["to_email"]):
        errors["to_email"] = "收件人邮箱格式不正确"

    if not data["subject"]:
        errors["subject"] = "邮件主题不能为空"
    elif len(data["subject"]) > MAX_SUBJECT_LEN:
        errors["subject"] = f"邮件主题不能超过 {MAX_SUBJECT_LEN} 个字符"

    if not data["message"]:
        errors["message"] = "邮件正文不能为空"
    elif len(data["message"]) > MAX_MESSAGE_LEN:
        errors["message"] = f"邮件正文不能超过 {MAX_MESSAGE_LEN} 个字符"

    return errors


def build_email(data: dict, smtp_config: dict) -> EmailMessage:
    from_email = smtp_config["from_email"] or data["email"]
    sender_name = str(Header(data["name"], "utf-8"))

    mail = EmailMessage()
    mail["Subject"] = data["subject"]
    mail["From"] = formataddr((sender_name, from_email))
    mail["To"] = data["to_email"]

    # 如果 SMTP 发件地址和表单中的发件地址不同，优先把真实提交者放到 Reply-To。
    if from_email.lower() != data["email"].lower():
        mail["Reply-To"] = formataddr((sender_name, data["email"]))

    body = (
        "网页邮件发送系统\n\n"
        f"提交人姓名: {data['name']}\n"
        f"提交人邮箱: {data['email']}\n"
        f"收件人邮箱: {data['to_email']}\n"
        f"邮件主题: {data['subject']}\n\n"
        "邮件正文:\n"
        f"{data['message']}\n"
    )
    mail.set_content(body)
    return mail


def send_mail(data: dict) -> None:
    smtp_config = get_smtp_config()
    mail = build_email(data, smtp_config)

    smtp_cls = smtplib.SMTP_SSL if smtp_config["use_ssl"] else smtplib.SMTP

    with smtp_cls(
        host=smtp_config["host"],
        port=smtp_config["port"],
        timeout=smtp_config["timeout"],
    ) as smtp:
        smtp.ehlo()

        if smtp_config["use_tls"]:
            smtp.starttls()
            smtp.ehlo()

        if smtp_config["username"] and smtp_config["password"]:
            smtp.login(smtp_config["username"], smtp_config["password"])

        smtp.send_message(
            mail,
            from_addr=smtp_config["from_email"] or data["email"],
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
            "Form validation failed from ip=%s errors=%s",
            request.remote_addr,
            errors,
        )
        return (
            jsonify(
                {
                    "ok": False,
                    "message": "表单校验失败",
                    "errors": errors,
                }
            ),
            400,
        )

    # 生产环境建议补充限流，并在公网场景下增加登录、验证码或收件人白名单，
    # 否则任何人都可以把它当成任意收件人的发信入口。
    try:
        send_mail(data)
    except Exception as exc:
        logger.exception(
            "Mail send failed from ip=%s sender=%s receiver=%s",
            request.remote_addr,
            data["email"],
            data["to_email"],
        )
        return (
            jsonify(
                {
                    "ok": False,
                    "message": f"邮件发送失败: {exc}",
                }
            ),
            500,
        )

    logger.info(
        "Mail sent successfully from ip=%s sender=%s receiver=%s subject=%s",
        request.remote_addr,
        data["email"],
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
