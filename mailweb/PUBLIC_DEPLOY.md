# 公网部署补充说明

本项目现在是通用收件人版本，收件人邮箱由前端表单里的 `to_email` 字段决定，而不是后端固定写死。

## 推荐公网部署流程

1. 准备域名，例如 `submit.example.com`
2. 将域名的 A 记录指向 Ubuntu 服务器公网 IP
3. 部署 Flask 应用和 systemd 服务
4. 安装 Nginx 并启用 `deploy/nginx-mailweb.conf`
5. 确认 `http://submit.example.com` 能访问
6. 申请 HTTPS 证书
7. 切换到 `deploy/nginx-mailweb-https.conf` 或直接使用 `certbot --nginx`

## 证书申请命令

```bash
sudo apt update
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d submit.example.com
```

如果你更喜欢手动维护证书路径，可以把 `deploy/nginx-mailweb-https.conf` 复制到
`/etc/nginx/sites-available/mailweb` 后再执行：

```bash
sudo nginx -t
sudo systemctl reload nginx
```

## 公网安全建议

- 当前版本允许用户自行填写 `to_email`
- 这意味着它天然更接近“任意收件人发信入口”
- 如果你要公开上线，强烈建议至少增加验证码、登录机制或收件人白名单
- 保留 Nginx 中的 `limit_req`
- 把 `SMTP_FROM_EMAIL` 配成你控制的真实发件地址
