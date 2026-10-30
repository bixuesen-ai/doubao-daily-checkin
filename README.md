# 豆包「天天领额度」每日自动签到

> 每天凌晨自动帮你领掉豆包电脑端「天天领额度」的 200 活动额度，连续签到不断签。
> 跑在 GitHub Actions 上，零服务器、零依赖、零成本。

![Python](https://img.shields.io/badge/Python-3.9%2B-blue)
![Dependencies](https://img.shields.io/badge/dependencies-0-brightgreen)
![License](https://img.shields.io/badge/License-MIT-green)
![Platform](https://img.shields.io/badge/platform-GitHub%20Actions-black)

---

## 这是什么

豆包电脑端（PC 版）有个「天天领额度」活动：每天签到一次送 **200 活动额度**，实时到账，连续签到还有累计奖励。额度能用在图片生成、AI PPT、深度研究这些高消耗功能上。

问题是——**得每天记着点那一一下**。断一天，连续天数从头算。

这个项目把「点那一下」交给 GitHub Actions：每天北京时间 00:05 自动请求签到接口，成功与否通过 Server 酱 / 企业微信 / Telegram / Bark 推给你，签到结果同时写进 Actions 任务页的摘要里。

**它不是外挂，也不模拟客户端**，只是替你发出那一次 HTTP 请求。所以：

- 不需要开着电脑，不需要装豆包客户端
- 不需要任何第三方库（纯 Python 标准库）
- 不需要服务器和域名

---

## 特性

| 能力 | 说明 |
| --- | --- |
| ⏰ 凌晨自动执行 | 北京时间 00:05 主跑 + 00:35 兜底重跑，防止 GitHub 定时任务丢跑 |
| 🔁 幂等 | 「今日已签到」被识别为成功，跑两次也不会报错、不会重复领 |
| 🧠 智能判定 | 关键词 + JSON 字段双重判定，兼容接口返回 `\u7b7e\u5230` 转义中文 |
| 🛡️ 凭据安全 | Cookie 只存 GitHub Secrets，日志全程脱敏，仓库里不存在任何明文 |
| 📮 多渠道通知 | Server 酱 / 企业微信机器人 / Telegram / Bark / 自定义 Webhook |
| 🧪 可测试 | 32 项单元 + 端到端测试，本地起假接口就能验证整条链路 |
| 🔧 配置驱动 | 接口地址、请求体、判定关键词全部走环境变量，官方改版改配置即可，不用改代码 |
| 🩺 保活机制 | 每月自动心跳提交，避免公开仓库 60 天无活动导致定时任务被暂停 |

---

## 五分钟部署

### 第 1 步：Fork 这个仓库

点右上角 **Fork**，复制到你自己的账号下。

> 为什么要 Fork 而不是直接用？因为 Secrets 只能配在自己的仓库里。

### 第 2 步：抓两个值

你需要拿到 **签到接口地址** 和 **Cookie**。完整图文步骤见 [docs/抓包指南.md](docs/抓包指南.md)，简述如下：

1. 电脑浏览器打开 <https://www.doubao.com> 并登录
2. 按 `F12` 打开开发者工具 → `Console` 面板
3. 复制 [tools/capture-snippet.js](tools/capture-snippet.js) 全部内容粘贴进去，回车
4. 回到豆包页面，点一下「签到」
5. 控制台会直接打印出可以粘贴的配置，以及接口的原始响应

如果你已经知道接口地址，也可以跳过脚本，直接在 `Network` 面板里找到那条请求，右键 → `Copy as cURL`，从中取出 URL、请求体和 Cookie。

### 第 3 步：配置 Secrets

进入你 Fork 后的仓库 → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**，至少添加这两个：

| Name | Value |
| --- | --- |
| `DOUBAO_COOKIE` | 浏览器里豆包的完整 Cookie 字符串 |
| `DOUBAO_CHECKIN_URL` | 签到接口的完整地址，`https://` 开头 |

按抓包结果，可能还需要补 `DOUBAO_CHECKIN_METHOD`、`DOUBAO_CHECKIN_BODY`、`DOUBAO_EXTRA_HEADERS`。

### 第 4 步：手动跑一次验证

**Actions** 标签页 → 左侧「每日自动签到」→ 右侧 **Run workflow**。

想先保险一点，把 `dry_run` 勾上 —— 它只打印将要发出的请求，不会真的签到：

```
[INFO ] 配置检查通过  method=POST  url=https://...
[INFO ] Cookie  sess***e=11 (len=428)
[INFO ] 将请求 POST https://...
[OK   ] dry-run 结束，未产生任何真实请求
```

确认请求长得对，再取消 `dry_run` 跑一次真实的，看日志里是不是 `签到成功`。

### 第 5 步：等它自己跑

什么都不用做了，每天 00:05 自动执行。任务失败 GitHub 会发邮件提醒你。

---

## 配置项

所有配置都走环境变量。本地开发时复制 `.env.example` 为 `.env` 填写即可（`.env` 已被 `.gitignore` 排除）。

### 必填

| 变量 | 说明 |
| --- | --- |
| `DOUBAO_COOKIE` | 豆包登录后的完整 Cookie，**最关键的凭据** |
| `DOUBAO_CHECKIN_URL` | 签到接口地址 |

### 请求参数

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `DOUBAO_CHECKIN_METHOD` | `POST` | 支持 `GET` / `POST` / `PUT` |
| `DOUBAO_CHECKIN_BODY` | 空 | 请求体，必须是合法 JSON；GET 请求可留空 |
| `DOUBAO_EXTRA_HEADERS` | `{}` | 额外请求头，JSON 对象格式。接口要求 `Referer`、`X-Csrf-Token` 时填这里 |
| `DOUBAO_TIMEOUT` | `20` | 单次请求超时（秒） |
| `DOUBAO_RETRY` | `3` | 失败重试次数 |
| `DOUBAO_RETRY_BACKOFF` | `3` | 重试退避基数（秒），实际间隔会加随机抖动 |

### 结果判定

接口改版后，通常只需要改这几项就能救回来，不用动代码。

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `DOUBAO_SUCCESS_KEYWORDS` | `签到成功,领取成功,签到完成,success` | 命中任意一个即判成功 |
| `DOUBAO_ALREADY_KEYWORDS` | `已签到,已经签到,今日已领,重复签到,already` | 命中即判「今天签过了」，按成功处理 |
| `DOUBAO_SUCCESS_JSON_PATH` | 空 | 精准判定，格式 `a.b.c=值`，多条用逗号分隔 |
| `DOUBAO_ALREADY_JSON_PATH` | 空 | 幂等重放判定，格式 `a.b.c=值`，优先级高于成功规则 |
| `DOUBAO_AUTH_KEYWORDS` | `未登录,登录已过期,unauthorized,401,403` | 命中即提示 Cookie 失效 |
| `DOUBAO_RISK_KEYWORDS` | `风控,验证,captcha,risk,verify,频繁` | 命中即提示疑似触发风控 |

判定优先级：**已签到关键词 → 已签到 JSON 规则 → 成功 JSON 规则 → 成功关键词 → 风控 → 鉴权 → 业务错误码 → HTTP 2xx 兜底**。

「已签到」排在第一位是有原因的：这类接口的「已签到」响应里往往同时包含「签到」二字，顺序反了就会把重复签到误报成新领取。

### 通知

| 变量 | 说明 |
| --- | --- |
| `DOUBAO_NOTIFY` | 通道列表，逗号分隔：`serverchan,wecom,telegram,bark,webhook` |
| `DOUBAO_NOTIFY_ON_ALREADY` | 设为 `1` 时，即使今天已签到也发通知（默认不发，避免噪音） |
| `SERVERCHAN_KEY` | Server 酱 SendKey |
| `WECOM_WEBHOOK` | 企业微信群机器人 Webhook 完整地址 |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | Telegram 机器人 |
| `BARK_URL` | Bark 推送地址，形如 `https://api.day.app/你的key` |
| `CUSTOM_WEBHOOK` | 自定义接收端，会 POST `{"title": ..., "content": ...}` |

不配 `DOUBAO_NOTIFY` 就完全不发通知，静默运行。

---

## 本地运行

```bash
git clone https://github.com/<your-name>/doubao-daily-checkin.git
cd doubao-daily-checkin

cp .env.example .env
# 编辑 .env，填入 DOUBAO_COOKIE 和 DOUBAO_CHECKIN_URL

python main.py --check     # 只检查配置是否完整
python main.py --dry-run   # 只打印请求，不发出去
python main.py             # 真的签到
```

不需要 `pip install` 任何东西，Python 3.9+ 直接跑。

其他用法：

```bash
python main.py --json       # 输出 JSON，方便被别的程序调用
python main.py --no-notify  # 这次不发通知
python main.py --version
```

退出码：`0` 成功或今日已签到 / `1` 签到失败 / `2` 配置错误。可以直接接 `cron` 或 Windows 任务计划，见 [docs/部署方式.md](docs/部署方式.md)。

跑测试：

```bash
python -m unittest discover -s tests -v
```

其中的端到端测试会在本地起一个假接口，完整验证「配置 → 请求 → 判定 → 退出码」链路，不需要真实账号。

---

## 工作原理

```
GitHub Actions (UTC 16:05)
        │
        ▼
   读取 Secrets ──► 构造 HTTP 请求（Cookie + Body）
        │
        ▼
   发送到签到接口 ──► 判定结果
        │              ├── 签到成功 ──► 通知 + 写入任务摘要
        │              ├── 今日已签到 ──► 视为成功，静默
        │              └── 失败/风控/掉登录 ──► 重试 → 通知告警
        ▼
   退出码回传给 Actions
```

北京时间 00:05 对应 UTC 的 16:05（前一天），所以 `cron: "5 16 * * *"`。

---

## 项目结构

```
doubao-daily-checkin/
├── main.py                      # 命令行入口
├── src/
│   ├── config.py                # 环境变量 → 配置对象，含校验
│   ├── client.py                # 签到请求 + 结果判定 + 重试
│   ├── http.py                  # 极简 HTTP 客户端（标准库）
│   ├── notifier.py              # 五种通知通道
│   └── logger.py                # 日志 + 脱敏 + Actions 分组
├── tools/
│   └── capture-snippet.js       # 浏览器控制台抓包脚本
├── tests/
│   ├── test_core.py             # 判定逻辑单元测试
│   └── test_integration.py      # 假接口端到端测试
├── docs/
│   ├── 抓包指南.md
│   └── 部署方式.md
└── .github/workflows/
    ├── checkin.yml              # 每日定时签到
    ├── ci.yml                   # 推送时跑测试
    └── keepalive.yml            # 防止定时任务被暂停
```

---

## 常见问题

**Q：Cookie 多久失效？**
不固定，通常几周到几个月。失效后日志会出现 `AUTH_FAILED` 并推送告警，重新抓一次 Cookie 更新 Secret 即可。别把 Cookie 贴到任何公开地方——它等同于你的登录状态。

**Q：为什么接口地址要自己抓，不能写死在代码里？**
官方没有公开这个接口，而且活动页随时改版。写死地址的脚本一改版就全废，还得等作者更新。配置化之后，你自己改一个 Secret 就恢复了。这也是为什么项目里带了一个抓包脚本。

**Q：会不会被封号？**
风险自担。每天一次低频请求，行为上跟手动签到没有本质区别，但自动化行为本身可能不符合服务条款。**建议只给自己用、只跑一个账号、不要改成高频轮询。** 如果你介意，就别用。

**Q：Actions 的定时任务准吗？**
不准。GitHub 的高峰期延迟几分钟到几十分钟都很常见，所以项目配了 00:05 和 00:35 两次。因为脚本是幂等的，多跑一次没任何副作用。

**Q：为什么同时有两个 cron，不会领两次吗？**
不会。第二次请求通常返回「今日已签到」，脚本判定为 `ALREADY`，按成功处理并退出 0。

**Q：公开仓库的定时任务会不会自己停？**
会。GitHub 对 60 天没有任何提交的公开仓库会暂停其中的 scheduled workflow。项目里的 `keepalive.yml` 每月提交一次心跳文件来规避这个问题。不想要就删掉那个文件，代价是每两个月手动点一次 Actions。

**Q：能跑多个账号吗？**
当前版本一个部署一个账号。多账号最简单的做法是 Fork 多份。如果需要，把 Cookie 改成用 `;` 分隔的数组循环请求也是一行改动的事。

**Q：签到成功但额度没到账？**
日志里的 `amount` 只是从响应文本里正则提取的，仅供参照。真实余额去豆包客户端「设置 → 订阅豆包专业版 → 订阅与额度管理」看。注意活动额度有有效期，过期清零。

---

## 免责声明

- 本项目仅供**个人学习与自动化技术研究**，不得用于商业用途。
- 使用本项目产生的任何后果（包括但不限于账号异常、额度未到账、违反服务条款）由使用者自行承担。
- 请遵守豆包及字节跳动的用户协议。若官方明确禁止此类自动化，请立即停止使用。
- 项目与豆包、字节跳动无任何关联。
- 活动规则、额度数值、有效期均以官方页面实时显示为准，本项目不保证任何特定收益。

---

## License

[MIT](LICENSE)
