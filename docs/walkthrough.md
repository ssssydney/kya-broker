# 支付方式说明 · v1.1 实战记录

这份文档配套的是 v1.1 (browser-native, zero-CLI) 架构。比 README + architecture 更具体一点 —— 用一次真实的 vast.ai $5 充值流程把整套支付怎么走、信任在哪、出了岔子怎么办都拆开讲清楚。

**v1.1 vs v1.0 区别**:v1.0 把 audit / OTP / SMTP 砍了,但 broker CLI 仍然算"必装"。v1.1 把 CLI 也变成**可选** —— SKILL.md 是用户唯一需要的文件,无任何 CLI 依赖,agent 用 bash + Chrome MCP 把所有事自己做完。

---

## 1. 这个 skill 在做什么(一句话)

> Claude Code 通过 **Claude-in-Chrome MCP** 驱动你本机的 Chrome 完成商家结账,所有需要"动钱"的步骤都在你的浏览器里你的眼皮下完成,卡号/密码/3DS 码 skill 一个字符都不接触。

不是支付服务商,是支付**编排器**。

---

## 2. 信任模型(替代了 v0.5 的 audit + OTP + email-lock)

v1.0 把这三层全删了,因为它们在重复浏览器和支付公司已经做的事。当前的信任来自:

| 信任来源 | 谁负责 | 我们是怎么用的 |
|---|---|---|
| **Chrome 解锁 + 你登录了 Google** | macOS keychain + Google | "你在场"的证据 —— skill 不需要再做身份验证 |
| **Chrome 保存的卡号 / autofill / Apple Pay** | Google + 卡组织 | "这张卡你之前批准过"的证据 —— skill 永远不输入卡号,merchant 的 Stripe iframe 由你 autofill |
| **Stripe / Visa / 发卡行** | 三家 | KYC + AML + 风控 + 3DS。$5 跨境过去他们没拦,不需要我们再叠一层 |
| **每次"动钱"前你在聊天里说 "go"** | 你 | "现在批准这一笔"的证据 —— hard rule,任何 Submit/Pay/Confirm 必须有你刚刚的 yes |

如果这四样都不可信,这个 skill 不应该被用 —— 你应该自己付钱。

---

## 3. 一次真实的支付 · vast.ai $5 充值(2026-04-26)

完整流程,带原始 ledger / DOM 状态。**没花虚的钱,没用 dry-run**。

### Step 1 · 我先不动钱,做必要 setup
```bash
broker --version              # v1.0.0(可选,装了的话用)
broker check-budget 5         # ok $5 fits within remaining caps
broker log --merchant vast.ai --amount 5 --rationale "..."
# → intent_id=d4295be3-8165-4cbb-bc31-9d4fc4a31cde, status=proposed
```

Ledger 里建了一行 `proposed` 的 intent。还没花钱。

> **v1.1 note**:这次跑的时候装了 broker CLI 所以用了它。但 v1.1 起 **CLI 变成纯可选** —— 没装也能跑全流程,agent 直接 `echo` 写 `~/.kya-payments.jsonl` 就行。装了能多用 `broker history` 之类查询。

### Step 2 · 浏览器导航到充值页(MCP 驱动,你不需要操作)
```
mcp__Claude_in_Chrome__list_connected_browsers
  → [{deviceId: "87be0d4d-...", name: "Browser 1", isLocal: true}]
mcp__Claude_in_Chrome__select_browser(deviceId: "87be0d4d-...")
mcp__Claude_in_Chrome__tabs_create_mcp
mcp__Claude_in_Chrome__navigate(url: "https://cloud.vast.ai/billing/")
```

进 vast 已经登录的 page,余额 -$0.61。

### Step 3 · 打开 modal + 配置 $5 + 选已存卡 VISA...3497

```
left_click(346, 285)               # "Add Credit" 按钮
# modal 弹出,默认 $10 + VISA 已选中
left_click(772, 288)               # "Other" radio
triple_click(880, 288) + type("5") # 改成 $5
screenshot(save_to_disk=true)      # 截图给你看
```

### Step 4 · 我停下,把截图给你 —— 你说 go 才点 Submit
这是 v1.0 安全的核心。

> 截图: $5 + VISA…3497 + Add credit 按钮蓝色待点
>
> 我说: "回 'go' 我点,回 'cancel' 我关 modal"
>
> 你回: "go"

### Step 5 · 真点 Submit
```
left_click(ref="Add credit")
wait(5s)
navigate("https://cloud.vast.ai/billing/")  # reload
get_page_text → "Credit: $4.39" + "Apr 26, 2026 — Stripe — VISA …3497 — +$5.00"
```

**没触发 3DS**(saved card 直接过)。Stripe 收据已记到 vast 的 transaction history。

### Step 6 · 更新 ledger
```bash
broker update d4295be3-... --status settled \
  --note "Apr 26 2026 — Stripe charge to VISA...3497, no 3DS triggered, vast credit now $4.39"
```

### 总耗时
- 用户在椅子上盯屏幕的时间 ≈ **15 秒**(只回了一句 "go")
- 实际银行扣款 + vast 入账 < **5 秒**
- 没邮箱 OTP,没 SMTP 配置,没 audit 框架,没 popup window

---

## 4. 哪一步谁经手了什么数据

| 数据 | 经手方 | 我(agent)看得到吗? |
|---|---|---|
| 卡号 / CVV / 有效期 | 浏览器 autofill → Stripe iframe → Stripe API | ❌ |
| 卡 last4 | vast UI 显示 | ✅ 仅显示用 |
| 你的 vast 密码 | Chrome password manager(没用上,因为已登录) | ❌ |
| Stripe 客户 token | vast 后端 ↔ Stripe | ❌ |
| 交易金额 / 收款方 | 我决定 + 你确认 | ✅ |
| 银行 3DS 码 | (这次没触发)如有,会发到你手机 | ❌ |

skill 接触的全是"元数据 / 显示用",从未接触可独立扣款的凭证。

---

## 5. 我点 Submit 之后会发生什么(分支)

```
点 "Add credit" / "Pay" / 等等
        │
        ├─ 路径 A:saved card + 小额 + 银行风控不严
        │    → 直接 settled,page text 出现 "credit added"
        │    → 这次 $5 走的是这条
        │
        ├─ 路径 B:银行触发 3DS / SMS / app push
        │    → 弹一个 iframe 或跳转
        │    → 我会停下:"你的银行要验证,在 Chrome 里完成,完了说 done"
        │    → 你完成 → page 回到正常 → settled
        │
        ├─ 路径 C:卡被风控 declined
        │    → page text "Card declined" / "Payment failed"
        │    → broker update --status failed
        │    → 卡上不扣钱(可能有 hold 1-3 天后退)
        │    → 我 NOT silently retry —— 问你下一步
        │
        └─ 路径 D:页面卡死 / playbook DOM 变了
             → screenshot 里看不到 success 也看不到 fail
             → broker update --status failed --note "..."
             → 你查 vast transaction history 验真假
```

**hard rule**:任何路径都不会无声重试。失败→ 问你。

---

## 6. 这次没踩到但**真实存在**的坑

### 6.1 Self-signed cert 阻塞 Jupyter terminal
vast 给每个 instance 起了个 Jupyter notebook server,用自签证书。Chrome 默认拦截,`thisisunsafe` 也可能不工作(HSTS 锁)。**对支付本身没影响**,只影响"租到 instance 之后跑训练"的远程操作 —— 这部分用 SSH 就好。

### 6.2 本地 VPN/代理拦 SSH 端口
如果你开了 FlClash / ClashX / Surge 等 TUN 模式 VPN,SSH 到非标端口(比如 vast 的 30898)会被代理拦在 protocol 层 —— TCP CONNECT 看似成功,SSH banner 交换被切断。

绕过办法,从快到慢:
- **暂停 VPN ~5 分钟**,SSH 进 instance,`tmux new -s xxx` + 启动训练 + detach,关 SSH,重开 VPN
- 在 VPN 里加 bypass 规则:`ssh5.vast.ai`、`172.81.x.0/24`、`*.vast.ai` 走 DIRECT
- 把所有命令塞进 vast 的 `on_start` 模板,instance 启动时自动跑(不需要交互式 SSH)

### 6.3 自签 Jupyter / 远程登录的方案对比

| 方法 | 走 VPN 吗 | 配置成本 | 适用 |
|---|---|---|---|
| Jupyter terminal in browser | 看 Chrome 设置,自签证书 chrome 拦 | 0 | 临时跑短命令 |
| SSH direct to instance | 否(直连)but VPN 拦 | 中(配 SSH key)| 长跑训练首选 |
| `vastai execute` HTTPS API | 走 HTTPS:443 ✅ | 低(API key) | **只能在 stopped instance**,运行中的不行 |
| `vastai stop instance` + execute + start | 走 HTTPS ✅ | 低 | 急用且不想关 VPN 时 |
| on_start template script | 不需要登录 | 中(写脚本) | 整个流程已知,无需交互 |

---

## 7. 这次其实没充得上的另一件事

**vast.ai 充值跑通了,但训练没实际跑**(LeWM 复现)。原因是:VPN/Clash 把 SSH 拦了,我没法登入 instance 装 stable-worldmodel + 启动训练。

但**这不是支付架构的问题** —— 是后置的远程操作问题。如果代理放开 / 有人愿意短暂关 VPN,从已充进的 $4.39 vast credit 起步,4090 跑 PushT 10 epochs ≈ $0.50-1.00 完全够。

---

## 8. 给下一次的清单(让流程更顺)

1. **付款前确认 SKILL.md 在 `~/.claude/skills/<name>/SKILL.md`**(Claude Code 才知道有这个 skill)
2. **确认 Claude for Chrome 装好且 `list_connected_browsers` 能看到设备**(无视觉化前端 / 装错 profile 都会让 list 返回 `[]`)
3. **预先 `broker budget --daily 50 --monthly 500`**(避免无 cap 失控)
4. **确认 saved card 在 merchant 那边可见**(没 saved 就需要现填,这个 skill 不输卡号)
5. **远程操作前,要么 ssh key 加好且 VPN 放开,要么写好 `on_start` 自动化脚本**(避免跟我现在一样 SSH 进不去)
6. **训练完主动 `vastai destroy instance`** —— vast deletion protection 关闭时会按 storage 一直扣

---

## 9. 这次 ledger 状态

```bash
$ broker history
                          Last 1 intents
┏━━━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━┓
┃ intent_id ┃ merchant ┃ amount ┃ status  ┃ created              ┃
┡━━━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━┩
│ d4295be3  │ vast.ai  │  $5.00 │ settled │ 2026-04-26T14:02:20Z │
└───────────┴──────────┴────────┴─────────┴──────────────────────┘
```

vast 那边:
- VISA…3497 扣 +$5.00
- 当前 credit $4.39(从 -$0.61 起算)
- instance 35630898 已 destroy
- 没遗留 storage 费用

---

## 10. 后续要做的事(给你)

1. **撤 vast API key** `lewm-repro` —— 我创建于 https://cloud.vast.ai/manage-keys/(API Keys tab),已经在对话和本地 vastai config 里出现,不长期保留。
2. **撤 GitHub PAT** `ghp_...` —— 之前给我建仓库用的,任务完成可以撤了。
3. **(可选)反思一下信任边界**:这次跑通的链是 "你→Chrome→VISA→Stripe→vast",中间没有第三方 audit / OTP。要不要长期保持这种"轻信任"模型,还是某些大额场景仍想叠一层 —— 这是个产品决策。

---

*相关文档:[README.md](../README.md) · [architecture.md](architecture.md) · [migration.md](migration.md) · [SKILL.md](../SKILL.md)*

*GitHub: https://github.com/ssssydney/kya-broker*
