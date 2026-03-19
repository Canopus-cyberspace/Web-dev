# 公网部署补充说明

本项目现在优先面向外部 SMTP 中继部署，推荐使用 587 + AUTH + STARTTLS，或 465 + SSL。
不再默认推荐 127.0.0.1:25 的本机无认证直发。

## 推荐公网部署流程

1. 准备域名，例如 `submit.example.com`
2. 将域名的 A 记录指向 Ubuntu 服务器公网 IP
3. 准备外部 SMTP 中继账号，并确认其支持事务性邮件投递
4. 部署 Flask 应用和 systemd 服务
5. 安装 Nginx 并启用 `deploy/nginx-mailweb.conf`
6. 确认 `http://submit.example.com` 能访问
7. 申请 HTTPS 证书
8. 切换到 `deploy/nginx-mailweb-https.conf` 或直接使用 `certbot --nginx`

## 推荐环境变量

```env
SMTP_HOST=smtp.your-provider.com
SMTP_PORT=587
SMTP_USERNAME=your_account
SMTP_PASSWORD=your_password_or_app_password
SMTP_USE_TLS=true
SMTP_USE_SSL=false
MAIL_FROM=your_account@your-domain.com
MAIL_FROM_NAME=Mail Web
MAIL_REPLY_TO=your_account@your-domain.com
MAIL_TIMEOUT=15
```

## QQ 邮箱投递建议

- 尽量使用可信外部 SMTP 中继，不要默认依赖本机 Postfix 直发
- `MAIL_FROM` 尽量与 SMTP 账号所属域一致
- 发给 QQ 邮箱时，发信域最好已经配置 SPF 或 DKIM
- `From` 和 `Sender` 应保持一致，避免出现额外伪造的发件人
- 邮件正文尽量简洁、直接，不要只发图片或只有一个链接