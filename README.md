# wxread — GitHub Actions 专用

基于 [findmover/wxread](https://github.com/findmover/wxread) 的微信读书接口上报脚本。本 fork 仅保留 GitHub Actions 定时／手动运行和离线回归测试，不再维护容器发布或常驻服务部署。

接口受理不等于实际阅读时长入账，也不保证签到、排行榜或挑战赛计时。请遵守微信读书与 GitHub Actions 的使用规则。

## 配置与运行

1. Fork 后打开 **Actions**，启用 `wxread` workflow。
2. 在已登录的微信读书网页版正常阅读，使用浏览器 DevTools 的 Network 找到 `https://weread.qq.com/web/book/read` 请求，选择 **Copy as cURL (bash)**。这份内容包含登录凭据，请勿提交到仓库、贴到日志或公开分享。
3. 在 **Settings → Secrets and variables → Actions** 配置下表。阅读 job 使用 `AutoRead` environment；如设置了环境级 Secret／Variable，它可能覆盖同名仓库配置，环境保护规则也可能要求审批。
4. 在 **Actions → wxread → Run workflow** 手动验证，再在微信读书中核对实际新增时长。

| 名称 | 位置 | 用途 |
| --- | --- | --- |
| `WXREAD_CURL_BASH` | Secret，必填 | 完整的 cURL bash 抓包内容，须含非空 `wr_skey` 与 `wr_vid` |
| `READ_NUM` | Variable，可选 | 目标受理次数，正整数；未设置时默认 40。每次受理后等待 30 秒，不代表实际新增时长 |
| `PUSH_METHOD` | Secret，可选 | `pushplus`、`wxpusher`、`telegram` 或 `serverchan`；留空不推送 |
| `PUSHPLUS_TOKEN` | Secret | 使用 PushPlus 时必填 |
| `WXPUSHER_SPT` | Secret | 使用 WxPusher 时必填 |
| `TELEGRAM_BOT_TOKEN`、`TELEGRAM_CHAT_ID` | Secret | 使用 Telegram 时必填 |
| `SERVERCHAN_SPT` | Secret | 使用 ServerChan 时必填 |
| `http_proxy`、`https_proxy` | Secret，可选 | HTTP／HTTPS 请求代理地址 |

唯一启用的 cron 在 [deploy.yml](.github/workflows/deploy.yml)：`7 18 * * *`，即 UTC 每天 18:07／北京时间次日 02:07。GitHub 定时任务可能延迟或被丢弃，实际触发须以 `event=schedule` 的运行记录为准。手动触发与定时触发使用相同入口和配置；同名工作流的新运行会取消旧运行。

## 成功与时长的含义

- 上报成功条件：HTTP 2xx、`succ == 1` 且 `synckey` 非 `None`，没有需要恢复的认证错误。
- 进度／通知报告受理次数，以及已受理请求提交的 `rt` 秒数合计，并明确标注“实际入账未验证”。脚本耗时、请求次数乘 30 秒和 `rt` 合计都不能当作实际入账时长。
- `rt` 沿用现有墙钟差生成方式，受网络等待、恢复退避和时钟跳变影响；最后一次受理后的等待不再上报。脚本不自动查询账号统计、排行榜或挑战赛。
- cURL 解析器只读取显式 headers／cookies，不执行 shell、变量展开或文件读取，也不采用抓包正文。阅读正文与签名沿用现有实现，不承诺和真实阅读器完全一致。
- 每次从 Secret 的 Cookie 开始，续期结果只在当前进程内存中保留；不改写 Secrets，不上传 Cookie artifact／cache。登录失效后需重新抓包。
- 阅读和通知的认证请求不自动跟随重定向。推送服务受理不保证最终送达；Telegram 首次请求遵循环境代理，失败后有限重试直连。

| 退出码 | 含义 |
| --- | --- |
| `0` | 达到目标受理次数，且通知被受理或未配置推送；不证明时长入账 |
| `1` | 配置错误或阅读上报失败 |
| `2` | 达到目标受理次数，但通知未被受理 |

## 定时任务排查

先检查 Actions 是否产生了运行记录，再检查真实阅读步骤，而不是把离线测试中的预期错误当成阅读失败。

- **没有运行记录**：检查 workflow 是否启用、cron 是否在默认分支、`AutoRead` 保护规则及仓库页面的暂停提示。公开 fork 的定时工作流默认禁用；公开仓库长期无活动也可能自动禁用。
- **GitHub 暂停 fork**：按页面提示恢复权限。已有 keepalive job 只尝试启用当前 workflow，不能保证解除仓库级暂停；整个工作流被阻止时也无法自救。
- **任务绿色但时长没增加**：绿色只证明请求／通知通过脚本的受理条件。核对同一账号、同一统计页面及统计周期；不要仅凭上报日志判断实际入账。
- **登录失败**：重新抓包更新 `WXREAD_CURL_BASH`；不要将 Cookie 明文写进源码。

## 开发验证

保留阅读脚本、配置解析、通知和 workflow 的离线回归。push／pull request 会运行 [tests.yml](.github/workflows/tests.yml)，不会执行真实阅读；`wxread` 的定时／手动 job 也先运行回归，再执行阅读。

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
```

测试使用自编假数据和模拟接口，不证明服务端入账。Python 3.10 是阅读 job 的运行版本，离线 CI 另覆盖 Python 3.14。仓库不再提供镜像构建发布或本地定时部署入口。
