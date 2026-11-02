# 导入密码

学生名单导入、成绩导入和成绩编辑共用同一个操作密码。名单剔除、导入预览及确认、成绩批次撤销也需要验证；访问者不能绕过网页直接调用这些写入接口。成绩明细、数据总览、查询、原始成绩历史及导出继续公开。

启用后，首页默认打开公开的数据总览。进入“名单导入”或“导入成绩”时显示密码验证页面；未解锁时点击“编辑”则弹出密码框，验证成功后继续打开原来选择的学生编辑窗口。验证在本浏览器内共用，有效期 8 小时；导入和编辑任一入口验证成功后，其余入口均解锁。可点击“退出操作验证”同时锁定全部写入操作；服务器重启、到期或清除浏览器 Cookie 后需要重新验证。其他浏览器需独立验证。

保存前会重新核对权限。如果编辑过程中验证到期，密码框会保留原编辑窗口和已填写内容；取消验证不保存，验证通过后继续本次保存。服务端仍检查原记录版本，遇到其他人已修改的记录会提示冲突，不能凭重新验证覆盖新数据。若在权限检查后、实际保存前过期，最多重新验证并重试一次。

密码不写入网页、浏览器本地存储、数据库或 Git。服务器配置只保存 PBKDF2-SHA256 校验摘要；浏览器保存 Secure / HttpOnly / SameSite=Strict 的随机会话 Cookie，服务端仅在内存中保存会话令牌摘要，到期不再接受。错误密码按请求来源限流，连续 5 次错误后等待 5 分钟；同一公网 IP 的用户共用该限制。配置文件损坏或不可读时拒绝启动，不会自动降级为公开导入。

## 服务配置

`STUDENT_IMPORT_AUTH_FILE` 指向 JSON 文件，内容为 `password_hash`。不设置此环境变量时保留原本不验证的行为，供本机旧流程与隔离测试使用。生产环境必须设置该变量并保留配置文件，不能把删除环境变量作为解决启动错误的方法。

配置路径：`/etc/student-score/import-access.json`，由 root 所有、student-score 组只读，权限 640，目录权限 750。在 systemd 的 `student-score.service.d/import-access.conf` 中设置：

```ini
[Service]
Environment=STUDENT_IMPORT_AUTH_FILE=/etc/student-score/import-access.json
```

设置后执行 `systemctl daemon-reload` 和 `systemctl restart student-score`。HTTPS 入口仍为 `https://sz-yiban.duckdns.org`，后端仍仅监听 `127.0.0.1:8000`。

需要更改密码时，先生成新配置（命令会提示两次输入密码，输入内容不回显，不要把密码直接写入命令行）：

```bash
cd "$(readlink -f /opt/student-score-releases/current)"
/opt/student-score-site/.venv/bin/python -m server.import_access /etc/student-score/import-access.next.json
chown root:student-score /etc/student-score/import-access.next.json
chmod 640 /etc/student-score/import-access.next.json
mv /etc/student-score/import-access.next.json /etc/student-score/import-access.json
systemctl restart student-score
```

若使用独立发布目录，应在当前生效的发布目录执行，并使用 `/opt/student-score-site/.venv/bin/python`。重启会使现有会话全部失效，不修改学生、成绩、导入历史或统计数据。

## 验证

未验证：`GET /api/import-access` 返回 `enabled=true, unlocked=false`，导入接口返回 401；查看接口正常返回。验证入口 `POST /api/import-access/unlock`，锁定入口 `POST /api/import-access/lock`；写入仍必须携带应用标记并通过原有来源检查。不要通过命令行明文密码测试正式账户，使用网页输入或私密文件。

本次测试使用独立临时数据库及虚构学生，覆盖未验证直接调用接口、错误密码、正确验证后导入和编辑、过期、伪造 Cookie、退出后令牌重放、服务重启会话失效、HTTPS Cookie 和桌面/手机页面切换；包括取消编辑验证不写入、同一个会话共用、过期时保留草稿再验证保存。
