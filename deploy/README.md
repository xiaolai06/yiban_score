# Ubuntu 云服务器部署（现有网站共用 80/443）

本方案适用于已有 Nginx 网站的服务器。已确认 `sz-yiban.duckdns.org` 当前没有被其他应用使用，直接用它部署学生成绩网站。Nginx 在 443 按域名转发到本机 `127.0.0.1:8000`；8000 不需要在云安全组或防火墙开放。应用没有登录系统，因此最终 Nginx 配置默认只允许你的公网 IP 访问；公网 IP 改变时需更新白名单。**如果删除白名单，任何知道网址的人都能读取、修改、导出真实学生数据。**

代码仓库只放程序。真实 `students.sqlite3`、Excel/CSV 原件、备份和 DuckDNS token 不上传 GitHub。

## 先准备

1. DuckDNS 现有的 `sz-yiban.duckdns.org` 已指向服务器公网 IP `47.106.109.59`，直接使用这一行即可。服务器私网 IP `172.17.23.205` 不填，IPv6 没有配置则留空。服务器固定公网 IP 时无需安装 DuckDNS 自动更新脚本。
2. 查出你**打开网站的电脑或手机所在网络**的出口公网 IPv4，填入 `__YOUR_PUBLIC_IP__`（只写 IPv4，模板已含 `/32`）。它不是服务器公网 IP `47.106.109.59`，也不是服务器私网 IP `172.17.23.205`。若需要从多个固定网络访问，可在 `deny all;` 前增加多条 `allow IP/32;`。手机流量/家庭网络 IP 变化后，先通过 SSH 更新 Nginx 白名单。
3. 确认云平台安全组允许 TCP 80、443；服务器上它们已被 Nginx 使用，保留原状。SSH 22 按现有配置。当前登录用户是 `admin`，不能直接写 `/opt`；先在服务器终端运行 `sudo -v` 和 `sudo -i`，看到 `root@...#` 提示符后再执行后续命令。若 `admin` 没有 sudo 权限，使用云控制台的管理员终端。不要给 `/opt` 设置 777 权限。
4. 记录当前 Nginx 配置位置。以下按 Ubuntu 常见 `/etc/nginx/sites-available` 和 `sites-enabled` 写；若是宝塔管理的 Nginx，须在面板里给此域名新增独立站点，按模板设置反向代理与证书，并以实际 `nginx -T` 输出为准。**不要覆盖其他站点配置，也不要另外安装第二套 Nginx。**

## 1. 拉代码和安装后端

先在当前 `admin` 的服务器终端执行以下两行；第二行成功后提示符应从 `admin@...$` 变成 `root@...#`：

```bash
sudo -v
sudo -i
```

**随后在 root 终端执行**：

```bash
git clone https://github.com/xiaolai06/yiban_score.git /opt/student-score-site
python3 --version
python3 -m venv /opt/student-score-site/.venv
/opt/student-score-site/.venv/bin/python -m pip install -r /opt/student-score-site/requirements.txt
id student-score || useradd --system --home /var/lib/student-score --shell /usr/sbin/nologin student-score
install -d -o student-score -g student-score -m 700 /var/lib/student-score
```

需要 Python 3.11+、`python3-venv`、Git、Nginx 和 Certbot；缺哪个再用系统包管理器安装。当前网站的 `/opt` 目录若已有其他项目，本项目独占 `/opt/student-score-site`，勿覆盖既有目录。若 `/opt/student-score-site` 已存在，先检查内容，不要直接覆盖。启动前可用 `ss -lntp | grep ':8000'` 确认后端端口空闲；没有输出表示当前无人监听。首次启动会在 `/var/lib/student-score` 建一个**空数据库**；有旧数据时，部署完成后按第 5 节迁移，在迁移前不要录入新数据。

## 2. 安装证书签发用的临时 Nginx 站点

**在服务器终端执行**，替换域名；先确认 `nginx -T` 显示的是当前正在使用的 Nginx：

```bash
APP_DOMAIN='sz-yiban.duckdns.org'
getent ahostsv4 "$APP_DOMAIN"
nginx -T | head -40
install -d -m 755 /var/www/letsencrypt
sed "s/__DOMAIN__/$APP_DOMAIN/g" /opt/student-score-site/deploy/nginx-http.conf.template > /etc/nginx/sites-available/student-score.conf
ln -s /etc/nginx/sites-available/student-score.conf /etc/nginx/sites-enabled/student-score.conf
nginx -t && systemctl reload nginx
certbot certonly --webroot -w /var/www/letsencrypt -d "$APP_DOMAIN"
```

如果 `sites-available`/`sites-enabled` 不存在，请根据 `nginx -T` 所示目录创建**单独的新站点配置**，并避免重复 include；不要直接执行上述写入和软链接两行，且第 3 节的最终配置也要写到这个新站点的实际路径。`getent` 结果应指向 `47.106.109.59`。证书申请必须在 DNS 生效、80 端口可从公网访问之后执行。Certbot 申请证书时，网站在此域名的普通路径暂时返回 404，其他站点不受这个文件影响。

## 3. 配置开机自启与 HTTPS 转发

**在服务器终端执行**，将 `MY_PUBLIC_IP` 换成你实际公网 IPv4：

```bash
APP_DOMAIN='sz-yiban.duckdns.org'
MY_PUBLIC_IP='你的公网IPv4'
sed "s/__DOMAIN__/$APP_DOMAIN/g" /opt/student-score-site/deploy/student-score.service.template > /etc/systemd/system/student-score.service
systemctl daemon-reload
systemctl enable --now student-score
systemctl status student-score --no-pager
sed -e "s/__DOMAIN__/$APP_DOMAIN/g" -e "s/__YOUR_PUBLIC_IP__/$MY_PUBLIC_IP/g" /opt/student-score-site/deploy/nginx-https.conf.template > /etc/nginx/sites-available/student-score.conf
nginx -t && systemctl reload nginx
```

首次签发证书后才安装 HTTPS 配置，否则 Nginx 因证书文件尚不存在而无法加载。若 Nginx 测试失败，**不要执行 reload**；查看报错并恢复临时配置。云安全组不要添加 8000 端口规则，服务也只绑定 `127.0.0.1`。`STUDENT_PUBLIC_HOST` 应为 `sz-yiban.duckdns.org`。

## 4. 核对运行情况

**在服务器终端执行**：

```bash
systemctl status student-score --no-pager
journalctl -u student-score -n 80 --no-pager
ss -lntp | grep ':8000'
nginx -t
```

`ss` 应只显示 `127.0.0.1:8000`。在白名单 IP 的浏览器打开 `https://sz-yiban.duckdns.org/`，应能打开首页；其他 IP 应得到 403。服务器自身用该域名请求时，也可能因不在白名单而得到 403，这并不代表服务故障。`/api/health` 可用于检查服务，但不要在公开日志中粘贴完整学生数据。若允许访问的设备返回 502，先查 `journalctl`；返回 403，先核对 `MY_PUBLIC_IP`；证书错误，核对 DNS 和 Certbot 签发结果。可用 `certbot renew --dry-run` 检查续签（需确保系统已安装 Certbot 定时续签服务）。

## 5. 迁移真实数据库（可选，始终走私下传输）

在原网站使用“使用说明 → 下载 SQLite 数据库备份”取得一致性备份。不要在原服务运行时仅复制主 `.sqlite3` 文件。通过 SSH/SCP 以 `admin` 用户将备份传至该用户的家目录，或使用云控制台上传。**不要把备份提交到公开 GitHub 仓库。**

**在服务器终端执行**：

```bash
systemctl stop student-score
ADMIN_HOME=$(getent passwd admin | cut -d: -f6)
install -o student-score -g student-score -m 600 "$ADMIN_HOME/students-backup.sqlite3" /var/lib/student-score/students.sqlite3
systemctl start student-score
systemctl status student-score --no-pager
```

迁移前若已有云端数据，请先通过网站备份功能保存；上述安装命令会覆盖云端同名数据库。迁移后在页面核对学生数量、学院和导入记录。

## 6. 日后更新与备份

**在服务器终端执行**：

```bash
cd /opt/student-score-site
git pull --ff-only
.venv/bin/python -m pip install -r requirements.txt
systemctl restart student-score
systemctl status student-score --no-pager
```

数据库在 `/var/lib/student-score/students.sqlite3`，不在 Git 仓库里。更新前通过网页下载备份；数据库结构迁移时程序会在数据目录下创建备份。域名更改时同时修改 systemd 的 `STUDENT_PUBLIC_HOST`、两个 Nginx `server_name` 与证书路径，并重新签发证书。公网 IP 白名单更改时只需修改 Nginx 新站点文件后执行 `nginx -t && systemctl reload nginx`。
