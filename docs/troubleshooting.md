# Troubleshooting · v1.0

按出现频率排序。先看[walkthrough.md](walkthrough.md)了解流程,再回来这里查具体问题。

## "broker: command not found"

`~/.local/bin` 不在 PATH。加进去:

```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc
```

如果 `~/.local/bin/broker` 也不存在,bootstrap 还没跑过,执行:

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/ssssydney/kya-broker/main/bootstrap.sh)"
```

## `mcp__Claude_in_Chrome__list_connected_browsers` 返回 `[]`

这是 v1.0 最常见的卡点。三种可能:

1. **Claude for Chrome 扩展没装** — 去 `https://chromewebstore.google.com/` 搜 "Claude for Chrome",装到你**实际打开的那个 Chrome profile**。
2. **装了但没 sign in** — 点扩展图标,Sign in with Anthropic。
3. **签了但没和当前 Claude Code session 配对** — 通常 `select_browser` 里能看到 deviceId 出现就 OK。如果 list 空,试一下退出 Chrome 重开,或重启 Claude Code session。

诊断命令:
```bash
# 查实际安装的扩展
ls ~/Library/Application\ Support/Google/Chrome/Default/Extensions/
find ~/Library/Application\ Support/Google/Chrome -name "manifest.json" 2>/dev/null \
  | xargs grep -li "claude" 2>/dev/null
```

## Chrome 显示 self-signed cert 警告(net::ERR_CERT_AUTHORITY_INVALID)

vast.ai 给每个 instance 起的 Jupyter notebook 用自签证书,Chrome 默认拦截。

试这些(从轻到重):
1. **页面焦点上键入 `thisisunsafe`** —— Chrome 内置 bypass(focus 在错误页时直接键盘输入这串字符)。Chrome 高版本可能锁了。
2. **chrome://flags → Allow invalid certificates** 开启
3. **改用 SSH** 到 instance —— 不需要走 Jupyter
4. **vast 实例侧自己装 Let's Encrypt 证书**(需要域名 + 一些配置)

## SSH "Connection closed" / "Connection refused" / 卡住

如果你开着 VPN(尤其是 TUN 模式如 FlClash / ClashX / Surge / Mihomo),这是 #1 原因。VPN 在 protocol 层会切断非标端口的 SSH。

诊断:
```bash
# 看本地 HTTP/SOCKS 代理端口
echo $HTTPS_PROXY $HTTP_PROXY $ALL_PROXY
# 看正在跑的代理工具
ps aux | grep -iE "clash|mihomo|sing-box|v2ray|surge" | grep -v grep
# 看是否进出 198.18.x 段(典型 TUN 拦截特征)
ssh -v -p <port> root@<host> 2>&1 | grep -i "closed by"
```

按急迫程度选择:
- **最快**:暂停 VPN 5 分钟,SSH 进去,`tmux new` 启动后台任务,detach,关 SSH,重开 VPN。任务在远端跑不需要本地代理。
- **次快**:VPN 加 bypass:`ssh5.vast.ai`、目标 IP 段、`*.vast.ai` 走 DIRECT
- **不用 SSH**:`vastai execute <id> 'cmd'` —— 但仅在 stopped instance 上能用,running 的会拒绝

## `vastai execute` 报 "Execute command only avail on stopped instances"

这是 vast 的限制,不是 bug。`execute` 走 HTTPS API 比 SSH 友好(不被 VPN 拦),但只能用在 stopped 状态。绕法:

```bash
vastai stop instance <id>
vastai execute <id> 'pip install xyz; nohup python train.py > /tmp/log.log 2>&1 &'
vastai start instance <id>
# 等 instance 起来,再 execute 看 /tmp/log.log
```

慢但走 HTTPS,适合 VPN 环境。

## 卡被 declined / 银行风控拦了

**v1.0 不重试**,因为:
- 重试可能再触发风控
- 重试可能让用户多扣 hold(冻结)
- 重试可能让我循环烧钱

正确处理:
1. broker ledger 标 `failed`
2. 把 vast 显示的具体 error 给用户
3. 让用户决定:换卡 / 等几小时再试 / 联系发卡行 / 改用 crypto.com 走加密货币

## 3DS popup 不弹 / 弹了点不进去

3DS 是发卡行的事,不是 vast / Stripe。常见情况:
- **弹了但你没看见** — 可能在新 tab / window 打开,被 Chrome popup blocker 拦了。检查 chrome 右上角小图标。
- **iframe 里弹的,内容空白** — 可能是发卡行的 SDK 没加载到。Stripe 的 frame 会显示 loading 转圈,等 30s 还转就刷新。
- **3DS 完成后 vast 没识别到** — 偶发的 vast 后端延迟。等 1-3 分钟再 reload billing 页。

如果反复失败,联系发卡行问"为什么阻拦 Stripe @ vast.ai 的 5USD 订单"。一次性人工 unblock 是常见的。

## broker check-budget 总是返回 ok 但我已经花了很多

`broker check-budget` 只统计 `status=settled` 的记录。如果你 `broker log` 之后没 `broker update --status settled`,记录还停在 `proposed`,不计入花费。

养成习惯:
- 付款成功 → `broker update <id> --status settled --note "..."`
- 付款失败 → `broker update <id> --status failed`
- 用户取消 → `broker update <id> --status cancelled`

## 历史 v0.5 文件还在 `~/.claude/skills/kya-broker.local/`

v0.5 留下的:
- `email_lock.json` + `email_lock.salt` —— v1.0 不读
- `dumps/` 目录 —— 旧的 DOM dump,可删
- `ledger.v0.5.sqlite.bak` —— 我手动备份的
- `config.yaml` —— v0.5 格式,v1.0 不读

清理(可选):
```bash
LOCAL=~/.claude/skills/kya-broker.local
rm -f $LOCAL/email_lock.json $LOCAL/email_lock.salt
rm -rf $LOCAL/dumps $LOCAL/logs
# 保留 ledger.sqlite(v1.0 写)和 v0.5 备份
```

## 完全卸载

```bash
bash ~/.local/opt/kya-broker/uninstall.sh   # 删 broker 二进制 + venv
rm -rf ~/.local/opt/kya-broker              # 删 source
rm -rf ~/.claude/skills/kya-broker          # 删 SKILL.md
rm -rf ~/.claude/skills/kya-broker.local    # 删 ledger + 配置
```

---

*仍然解决不了 → GitHub Issue: https://github.com/ssssydney/kya-broker/issues*
