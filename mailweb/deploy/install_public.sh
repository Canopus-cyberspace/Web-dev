#!/usr/bin/env bash
set -Eeuo pipefail

APP_NAME="mailweb"
APP_USER="mailweb"
APP_GROUP="mailweb"
APP_DIR="/opt/mailweb"
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

DOMAIN="${DOMAIN:-}"
SMTP_HOST="${SMTP_HOST:-smtp.your-provider.com}"
SMTP_PORT="${SMTP_PORT:-587}"
SMTP_USERNAME="${SMTP_USERNAME:-}"
SMTP_PASSWORD="${SMTP_PASSWORD:-}"
SMTP_USE_TLS="${SMTP_USE_TLS:-true}"
SMTP_USE_SSL="${SMTP_USE_SSL:-false}"
MAIL_FROM="${MAIL_FROM:-}"
MAIL_FROM_NAME="${MAIL_FROM_NAME:-Mail Web}"
MAIL_REPLY_TO="${MAIL_REPLY_TO:-}"
MAIL_TIMEOUT="${MAIL_TIMEOUT:-15}"
ENABLE_HTTPS="${ENABLE_HTTPS:-0}"
CERTBOT_EMAIL="${CERTBOT_EMAIL:-}"
INSTALL_POSTFIX="${INSTALL_POSTFIX:-0}"

log() {
  echo "[${APP_NAME}] $*"
}

fail() {
  echo "[${APP_NAME}] ERROR: $*" >&2
  exit 1
}

require_root() {
  if [[ "${EUID}" -ne 0 ]]; then
    fail "请使用 sudo 或 root 执行此脚本"
  fi
}

require_var() {
  local name="$1"
  if [[ -z "${!name:-}" ]]; then
    fail "缺少必填环境变量: ${name}"
  fi
}

install_packages() {
  log "安装系统依赖"
  apt-get update
  apt-get install -y nginx python3 python3-venv rsync

  if [[ "${INSTALL_POSTFIX}" == "1" ]] && ! dpkg -s postfix >/dev/null 2>&1; then
    log "安装 Postfix（仅在你明确需要本机 MTA 时启用）"
    echo "postfix postfix/mailname string ${DOMAIN}" | debconf-set-selections
    echo "postfix postfix/main_mailer_type string 'Local only'" | debconf-set-selections
    DEBIAN_FRONTEND=noninteractive apt-get install -y postfix
  fi

  systemctl enable --now nginx

  if dpkg -s postfix >/dev/null 2>&1; then
    systemctl enable --now postfix
  fi
}

ensure_user_and_dir() {
  if ! id -u "${APP_USER}" >/dev/null 2>&1; then
    log "创建系统用户 ${APP_USER}"
    adduser --system --group --home "${APP_DIR}" "${APP_USER}"
  fi

  mkdir -p "${APP_DIR}"
}

sync_project() {
  log "同步项目文件到 ${APP_DIR}"
  rsync -a --delete \
    --exclude '.git' \
    --exclude '__pycache__' \
    --exclude '.venv' \
    "${PROJECT_ROOT}/" "${APP_DIR}/"
}

write_env_file() {
  log "写入 ${APP_DIR}/.env"
  cat > "${APP_DIR}/.env" <<EOF
APP_HOST=127.0.0.1
APP_PORT=5000
LOG_LEVEL=INFO
SMTP_HOST=${SMTP_HOST}
SMTP_PORT=${SMTP_PORT}
SMTP_USERNAME=${SMTP_USERNAME}
SMTP_PASSWORD=${SMTP_PASSWORD}
SMTP_USE_TLS=${SMTP_USE_TLS}
SMTP_USE_SSL=${SMTP_USE_SSL}
MAIL_FROM=${MAIL_FROM}
MAIL_FROM_NAME=${MAIL_FROM_NAME}
MAIL_REPLY_TO=${MAIL_REPLY_TO}
MAIL_TIMEOUT=${MAIL_TIMEOUT}
EOF
}

install_python_deps() {
  log "创建 Python 虚拟环境并安装依赖"
  chown -R "${APP_USER}:${APP_GROUP}" "${APP_DIR}"
  sudo -u "${APP_USER}" python3 -m venv "${APP_DIR}/.venv"
  sudo -u "${APP_USER}" "${APP_DIR}/.venv/bin/pip" install --upgrade pip
  sudo -u "${APP_USER}" "${APP_DIR}/.venv/bin/pip" install -r "${APP_DIR}/requirements.txt"
}

install_systemd_service() {
  log "安装 systemd 服务"
  cp "${APP_DIR}/deploy/mailweb.service" /etc/systemd/system/mailweb.service
  systemctl daemon-reload
  systemctl enable --now mailweb
}

install_nginx_site() {
  log "安装 Nginx 站点配置"
  sed "s/submit\.example\.com/${DOMAIN}/g" \
    "${APP_DIR}/deploy/nginx-mailweb.conf" > /etc/nginx/sites-available/mailweb

  ln -sf /etc/nginx/sites-available/mailweb /etc/nginx/sites-enabled/mailweb
  rm -f /etc/nginx/sites-enabled/default

  nginx -t
  systemctl reload nginx
}

open_firewall_if_needed() {
  if command -v ufw >/dev/null 2>&1 && ufw status | grep -q "Status: active"; then
    log "放行 80/443 端口"
    ufw allow 80/tcp || true
    ufw allow 443/tcp || true
  fi
}

issue_https_certificate() {
  if [[ "${ENABLE_HTTPS}" != "1" ]]; then
    log "跳过 HTTPS 证书申请（ENABLE_HTTPS=0）"
    return
  fi

  require_var CERTBOT_EMAIL

  log "安装 certbot 并申请 HTTPS 证书"
  apt-get install -y certbot python3-certbot-nginx
  certbot --nginx \
    --non-interactive \
    --agree-tos \
    --redirect \
    -m "${CERTBOT_EMAIL}" \
    -d "${DOMAIN}"

  systemctl reload nginx
}

show_summary() {
  log "部署完成"
  echo
  echo "访问地址: http://${DOMAIN}"
  if [[ "${ENABLE_HTTPS}" == "1" ]]; then
    echo "访问地址: https://${DOMAIN}"
  fi
  echo "应用目录: ${APP_DIR}"
  echo "SMTP 主机: ${SMTP_HOST}:${SMTP_PORT}"
  echo "MAIL_FROM: ${MAIL_FROM}"
  echo
  echo "提醒: 推荐使用外部 SMTP 中继，并让 MAIL_FROM 与 SMTP 账号所属域保持一致。"
  echo
  echo "可用排查命令:"
  echo "  sudo systemctl status mailweb --no-pager"
  echo "  sudo systemctl status nginx --no-pager"
  echo "  sudo journalctl -u mailweb -f"
  echo "  sudo tail -f /var/log/mail.log"
}

main() {
  require_root
  require_var DOMAIN
  require_var SMTP_USERNAME
  require_var SMTP_PASSWORD
  require_var MAIL_FROM

  install_packages
  ensure_user_and_dir
  sync_project
  write_env_file
  install_python_deps
  install_systemd_service
  install_nginx_site
  open_firewall_if_needed
  issue_https_certificate
  systemctl restart mailweb
  show_summary
}

main "$@"