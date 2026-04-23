# KYA-Broker 项目现状总览

**状态**: v0.4 已落地（2026-04-22） · 67 tests passing · 已接入 OpenRouter / vast.ai / Anthropic 三个 merchant

**一句话**: 一个 Claude Code skill，让 agent 自己替用户向任何 allowlisted merchant 付钱 —— 信用卡、加密钱包、邮件魔链、3D-Secure、OTP 都走同一个 HumanGate 抽象；broker 驱动浏览器到需要人操作的那一步，然后把那一步清晰地弹给用户，等用户操作完继续。

---

## 1. 项目定位

### 1.1 目标

让下列工作流在 Claude Code 里变成一条命令就能跑完：

> "把 OpenRouter 充 $10，然后用 gpt-4o-mini 跑这个论文复现 pipeline"
>
> "在 vast.ai 上租一块 H100 复现这篇论文，预算 $15"
>
> "给我 Anthropic API 充 $50 的额度"

以前这些任务的瓶颈是**付钱**：agent 跑到需要充钱的步骤就卡住，等用户手动打开浏览器、输卡号、点支付、回 terminal 告诉 agent "可以继续了"。本项目把**付钱**变成 agent 能调用的 skill，而不是中断点。

### 1.2 关键原则

1. **Agent 永远不掌握任何可直接转账的凭证** —— 不存卡号、不存私钥、不存密码。
2. **每笔支付都必须由独立的 auditor（默认 Codex）审核**，防止 prompt injection 让 agent 乱花钱。Codex 是跨模型家族审计的密码学等价物 —— 用 Claude 审 Claude 共享训练偏见。
3. **最终授权靠 rail 本身的人机交互机制** —— 用户在 Stripe iframe 里输卡号、在 MetaMask 扩展里输密码、在邮件里点链接、在手机上看 OTP。这些 moment 物理上不可自动化，broker 只是把它们**清晰地呈现给用户**。
4. **Portable** —— 任何装了 Python 3.11+、Chrome 的 macOS/Linux 用户 `git clone` → `install.sh` → `broker setup` 即用。

### 1.3 不在范围内

- ACH / SEPA 银行电汇（保留给 v0.5+）
- 多台机器共用一把钱包并发下单（nonce / 重复扣款问题，文档明确不支持）
- 代用户识别/对抗浏览器里的恶意扩展（用户自己的浏览器卫生问题）
- 替用户决定**要不要**花钱（这是人的决定，skill 只执行已批准的意图）

---

## 2. 版本演进

| 版本 | 关键变化 | 状态 |
|---|---|---|
| v0.1 | Stripe Issuing API-first，agent 调 Stripe 虚拟卡 API 直接扣款 | 弃用（agent 拿到卡数据，信任边界错误）|
| v0.2 | 浏览器级人类模拟 + WebAuthn + content-script 拦截器 | 弃用（架构过重，4 个主体难协调）|
| v0.3 | Python skill + MetaMask 原生弹窗作 L2 授权 | 已实现 |
| v0.3.1 | 新增 dual-auditor（Codex + Claude shadow mode） | 已实现 |
| **v0.4** | **泛化 rails：card / crypto / email_link 都走 HumanGate 统一抽象；新增 OpenRouter / Anthropic 等 merchant** | **当前** |
| v0.5 (计划) | bank_transfer rail · marketplace / playbook 社区共建 · 真实 dogfood 数据 | 未开始 |

**v0.3.1 → v0.4 的关键 insight**：MetaMask 弹窗、Stripe 卡输入、3DS 挑战、邮件 magic link、SMS OTP —— 这些**看起来不一样的东西其实都是同一个 primitive**：一个需要人类在某个物理受信任界面里完成的动作，broker 的任务不是代替而是等待 + 呈现。统一到 `HumanGate` 之后，加一个新 rail 只需要写一个 YAML 和可能声明一个新的 `HumanGateReason`。

---

## 3. 架构概览

### 3.1 四个主体

```
┌───────────────┐  propose_intent    ┌────────────────┐
│ Claude Code   │ ─────────────────▶ │ Broker (py)    │
│ (agent)       │                    │ 状态机 + 账本    │
└───────────────┘                    └────────┬───────┘
                                              │ 审计
                                              ▼
                                    ┌────────────────┐
                                    │ Codex (primary)│   跨家族独立审计
                                    │ Claude (fb/影子)│
                                    └────────┬───────┘
                                             │ 通过 + L0/L1 判级
                                             ▼
                                    ┌────────────────┐
                                    │ Chrome (用户)   │
                                    │  导航、填表      │
                                    │  HumanGate ────┐│ 
                                    └────────┬───────┘│
                                             │        │
                        (card / MetaMask / email / OTP)
                                             │        │
                                             ◀────────┘
                                             │ 回执 + tx hash
                                             ▼
                                    ┌────────────────┐
                                    │ Merchant 到账   │
                                    │ (OpenRouter /   │
                                    │  vast / ...)    │
                                    └────────────────┘
```

- **Agent** 能做：提 intent、查状态。不能做：签名、输入卡号、跳过 gate。
- **Broker** 能做：校验、落账、调 auditor、驱动 Chrome 到 gate。不能做：签名、输入卡号。
- **Auditor** 能做：对 intent 说 approve / reject。不能做：直接影响执行（除了 primary 否决权）。
- **Browser + 人** 能做：登录、输卡、签名、3DS、OTP。不能做：提 intent、改策略。

任何单个主体都拿不到完整的"转账能力"链 —— 必须至少一次人类动作在 rail 的原生授权点。

### 3.2 HumanGate：核心新抽象

```python
HumanGateRequest(
    reason = metamask_sign | card_details | card_3ds | email_magic_link
           | email_otp | sms_otp | login | saved_card_confirm | passkey | generic,
    prompt = "面向用户的中文/英文说明，terminal + 可选 macOS banner 会展示",
    timeout_seconds = 240,
    on_completion = <predicate>,   # 轮询 DOM / URL / selector 判断完成
    on_decline    = <predicate>,   # 短路出 declined
    optional      = True,          # 仅在页面出现 presence_check 时才等待（如 3DS）
    presence_check = <predicate>,
)
→ HumanGateResult(outcome = completed | declined | timeout | skipped, ...)
```

每一个 rail 的"等用户"片段都复用这一个 primitive。在 playbook YAML 里就是 `wait_for_human:` 步骤，运行时 chrome_bridge 把它翻译成上面这些 predicate。

通知通道可插拔：`terminal` / `osascript_notify`（macOS 横幅）/ `osascript_say`（朗读）/ 自定义 callable（挂 Slack webhook、push 通知等）。

### 3.3 Intent 生命周期

```
        propose_intent
            │
            ▼
      ┌──────────┐   auditor 拒绝   ┌──────────┐
      │ proposed │ ─────────────▶  │ rejected │
      └──────────┘                 └──────────┘
            │ auditor 通过
            ▼
      ┌──────────┐
      │ audited  │
      └──────────┘
       │        │
       │ L0     │ L1
       ▼        ▼
  ┌──────────┐ ┌──────────────┐
  │ executing│ │awaiting_user │
  └──────────┘ └──────────────┘
                    │ resume
                    ▼
              ┌──────────┐
              │ executing│
              └──────────┘
               │  │  │  │
      settled  │  user_declined
               │  │
               │ failed / playbook_broken
```

**授权分级**：

- `L0` (amount ≤ l0_ceiling_usd): auditor 通过即自动执行，rail 自己的 gate 仍会触发
- `L1` (≤ l1_ceiling_usd): auditor + 显式 HumanGate
- `L2` (> l1_ceiling_usd): broker 直接拒绝，要 agent 先让人决定

### 3.4 Rail 选择

```python
select_rail(cfg, intent) -> Rail:
    候选顺序 = [intent.rail_hint?, merchant.preferred_rail, *cfg.rails]
    for rail in 候选:
        if 用户已在 config.payment_methods 里 enroll 该 rail:
            if merchant 对该 rail 有 playbook:
                return Rail(name=rail, playbook=<file>)
    raise RailUnavailableError(<每个候选被拒原因>)
```

---

## 4. 目录结构

```
kya-broker/                              # 只读 skill 代码（git-pullable）
├── SKILL.md                             # Claude Code 读这个发现 skill
├── README.md                            # 用户文档
├── policy.default.yaml                  # 默认策略（setup 拷贝到 .local/）
├── install.sh / uninstall.sh
├── pyproject.toml
│
├── playbooks/                           # 每个 merchant × rail 一个 YAML
│   ├── openrouter_topup_card.yaml
│   ├── openrouter_topup_crypto.yaml
│   ├── vast_topup_crypto.yaml
│   ├── vast_topup_card.yaml
│   └── anthropic_topup_card.yaml
│
├── prompts/                             # auditor 和 chrome agent 的 system prompts
│   ├── audit_system.md
│   ├── audit_codex.md                   # Codex 特化（sandbox / 反 prompt injection）
│   ├── audit_claude.md                  # Claude 特化（强制 JSON 输出）
│   └── chrome_agent.md                  # Claude-in-Chrome 的硬边界（绝不点 MetaMask 确认）
│
├── src/
│   ├── intent.py                        # 数据模型 + 状态机（含 rail_hint）
│   ├── ledger.py                        # SQLite（schema v3）
│   ├── auditor/
│   │   ├── base.py                      # Auditor ABC + Verdict 解析
│   │   ├── codex.py                     # codex CLI subprocess
│   │   ├── claude.py                    # Anthropic SDK 独立调用
│   │   ├── mock.py                      # dry-run 用
│   │   └── runner.py                    # primary + shadow 编排
│   ├── human_gate.py                    # ⭐ 新：HumanGate 抽象
│   ├── chrome_bridge.py                 # CDP 后端 + dry-run 模拟器
│   ├── rail_selector.py                 # 泛化到 card/crypto/email_link
│   ├── broker.py                        # 高层编排
│   ├── config.py                        # payment_methods + merchants.playbooks 字典
│   ├── mcp_server.py                    # stdio MCP（4 个 tool）
│   ├── cli.py                           # `broker` 命令
│   └── setup_wizard.py                  # 交互式 enroll + 白名单 review
│
├── tests/                               # 67 tests, <3s 跑完
│   ├── test_intent.py
│   ├── test_ledger.py
│   ├── test_auditor.py
│   ├── test_human_gate.py               # ⭐ 新
│   ├── test_rail_selector.py
│   ├── test_config_merchants.py         # ⭐ 新
│   └── test_broker.py
│
├── docs/
│   ├── architecture.md                  # 这篇 overview 的详细版
│   ├── playbook_authoring.md            # 怎么加新 merchant
│   ├── troubleshooting.md
│   └── overview.md                      # ← 本文件
│
└── examples/
    ├── example_intent.json              # OpenRouter $10 card 充值
    └── example_context.json

~/.claude/skills/kya-broker.local/       # 用户本地状态（永不进 git）
├── ledger.sqlite
├── config.yaml
├── .env                                 # OPENAI_API_KEY / ANTHROPIC_API_KEY
├── dumps/                               # playbook 失败时 DOM + 截图
└── logs/
```

---

## 5. 已实现功能清单

### 5.1 核心运行时

- [x] Intent 数据模型 + 状态机 + 过期机制
- [x] SQLite 账本（append-only state events、audit results、executions 三张表 + audit_comparison view）
- [x] Schema 迁移（v1 → v2 → v3 添加 rail_hint 列）
- [x] L0 / L1 / L2 授权分级
- [x] 每日 / 每月花费上限
- [x] Merchant 白名单
- [x] 审计失败 fallback（可选 config 开关）

### 5.2 Dual Auditor

- [x] `Auditor` ABC + `CodexAuditor`（CLI subprocess）+ `ClaudeAuditor`（Anthropic SDK 独立调用）+ `MockAuditor`（dry-run）
- [x] Shadow mode：并发跑 Codex + Claude，写对称 ledger 行，不影响 primary 决策
- [x] JSON 解析鲁棒性：脱 markdown fence / 剥 preamble / 不合规当 reject
- [x] `broker analyze-audits` 导出 Codex vs Claude A/B 对比

### 5.3 Rails & HumanGate（v0.4 重点）

- [x] `HumanGate` primitive + 10 种 reason + 默认 completion/decline 关键词
- [x] 3 个通知通道（terminal / macOS banner / 朗读）+ 可挂自定义 callable
- [x] `card` rail（Stripe / Chrome autofill / 1Password / Apple Pay / 手动输入都可）
- [x] `crypto` rail（MetaMask / WalletConnect via USDC）
- [x] `email_link` rail（魔链 / 邮件 OTP）
- [x] 3DS 挑战支持（`optional: true` + presence_check）
- [x] `intent.rail_hint` agent 可以表达偏好

### 5.4 Merchant Playbooks

- [x] OpenRouter: `card` + `crypto`
- [x] vast.ai: `crypto`（主）+ `card`
- [x] Anthropic Console: `card`

### 5.5 Setup 向导

- [x] Prerequisites 检查（Python 3.11+、Chrome）
- [x] Audit layer 配置（Codex / Claude / shadow）
- [x] Payment methods enroll（多个 card / crypto / email_link，带 last4 / wallet 地址 / per-method 上限）
- [x] Merchant 白名单 review
- [x] 花费阈值配置
- [x] Dry-run 端到端 smoke test

### 5.6 Claude Code 接入

- [x] `SKILL.md` 声明 skill 触发条件和 workflow
- [x] stdio MCP server（4 个 tool：`propose_intent` / `get_status` / `get_history` / `check_balance`）
- [x] `broker` CLI 覆盖所有 MCP 能力 + `resume` / `analyze-audits` / `export-logs`

### 5.7 测试

- [x] 67 个测试，涵盖 intent state machine、ledger CRUD、auditor JSON 解析、rail selection、human gate outcomes、config round-trip、broker 端到端（stub auditor + dry-run chrome）
- [x] `KYA_BROKER_DRY_RUN=1` + `_AUDITOR=approve` + `_HUMAN_GATE=completed` 让端到端可以在没 API key 没浏览器的环境里跑

---

## 6. 使用方式

### 6.1 安装

```bash
git clone <repo> ~/.claude/skills/kya-broker
cd ~/.claude/skills/kya-broker
bash install.sh
export PATH="$HOME/.local/bin:$PATH"
broker setup             # 交互式向导 ~10 分钟
```

### 6.2 从 Claude Code 里触发

用户在 Claude Code 说：

> "给 OpenRouter 充 $10，然后用 gpt-4o-mini 跑 scripts/run_pipeline.py"

Claude Code 看到 SKILL.md 触发条件匹配，自己做这些事：

1. 估算：pipeline 25 次调用 × ~400k 输入 token ≈ $4-6
2. `broker check-balance` → OpenRouter 当前 $0.12
3. 生成 intent.json（merchant=openrouter.ai, amount_usd=10, rail_hint=card, rationale=…）
4. `broker propose-intent intent.json --context-file ctx.json`
5. Broker 调 Codex 审核 → 通过 → 因为 $10 > L0 ceiling $2，进入 L1 状态 `awaiting_user`
6. Claude Code 告诉用户："请在 Chrome 里完成卡支付"，然后 `broker resume <intent_id>`
7. Broker 驱动 Chrome 到 openrouter.ai/credits，HumanGate 触发，terminal 弹出：

```
╭──── 🔔 ACTION NEEDED IN BROWSER ────╮
│ OpenRouter's Stripe checkout is    │
│ ready. Enter your card (or autofill│
│ from 1Password / Chrome / Apple    │
│ Pay) and click Pay.                │
│                                    │
│ reason: card_details · 240s        │
╰────────────────────────────────────╯
```

8. 用户操作，broker 检测 "Payment successful" 出现 → `settled`
9. Claude Code 看到 settled，继续跑 pipeline

### 6.3 直接 CLI

```bash
broker check-balance
broker propose-intent examples/example_intent.json --context-file examples/example_context.json
broker status <intent_id>
broker history --format json
broker analyze-audits --since 2026-04-01
broker export-logs out.json
```

### 6.4 Dry-run（不花钱测试）

```bash
export KYA_BROKER_DRY_RUN=1
export KYA_BROKER_DRY_RUN_AUDITOR=approve     # 跳过 Codex/Claude
export KYA_BROKER_DRY_RUN_HUMAN_GATE=completed # 跳过人机交互
broker propose-intent examples/example_intent.json
```

---

## 7. 数据契约速查

### Intent schema

```json
{
  "merchant": "openrouter.ai",
  "amount_usd": 10.0,
  "rationale": "需要 $10 OpenRouter 额度跑 gpt-4o-mini 的论文复现 pipeline，估算 $6 用量 + 40% buffer",
  "estimated_actual_cost_usd": 6.0,
  "references": ["scripts/run_pipeline.py"],
  "rail_hint": "card"
}
```

### Verdict schema（auditor 输出）

```json
{
  "intent_id": "…",
  "verdict": "approve" | "reject",
  "concerns": ["人类可读的一句", "…"],
  "recommended_amount_usd": null
}
```

任何 preamble、markdown code fence、额外解释都会触发 reject（JSON 解析规则）。

---

## 8. 下一步

### 近期（v0.4.x 修补）

- [ ] 真实跑通一次 OpenRouter card 充值（到目前为止都是 dry-run）
- [ ] 真实跑通一次 vast.ai crypto 充值
- [ ] 文档化每个 merchant 实际 UI 的 selector（playbook 里的 `click_visual: "Add Credits"` 这种要和真实 DOM 对上）
- [ ] `broker analyze-audits` 积累首批真实数据后写 `docs/auditor_comparison.md`

### 中期（v0.5）

- [ ] `bank_transfer` rail（ACH / SEPA）
- [ ] Playbook 社区贡献流程：PR 模板、reviewer checklist、UI drift 监控
- [ ] 月度花费复盘自动生成：`broker report --month 2026-05`

### 研究线（KYA 论文支点）

- [ ] Shadow mode 在真实意图上跑 1 个月，对比 Codex / Claude verdict 分歧 case
- [ ] 构造 adversarial intent 数据集，测两种 auditor 的 detection 率
- [ ] 写成小论文：*Cross-model-family auditing as a hedge against shared-bias prompt injection in agent payment*

---

## 9. 用户马上可以做的一件事

当用户把论文丢过来，期望的 workflow 是：

1. Claude Code 读论文 → 估算 API 开销 → `broker propose-intent`（rail=card）给 OpenRouter 充 $X
2. HumanGate 触发，用户在 Chrome 里按一下 autofill + Pay
3. Broker 检测到 settled
4. Claude Code 直接开始调 OpenRouter API 跑 pipeline
5. 结果落地到本地文件，Claude Code 汇报

整个过程用户**物理参与的时间 ≤ 15 秒**（就是按一次 "Pay"，可能再按一次 3DS），其他全是 agent。

---

## 10. 设计决策备忘

几个不那么明显但很关键的选择：

- **为什么 merchant.playbooks 是 dict 而不是 list**：因为 rail 是 lookup key。改成 list 就要每次线性扫描，而且 duplicated rail 的语义不清。
- **为什么 rail_hint 是 soft 而不是 hard**：agent 估错的概率不低。hard 会让 "用户只 enroll 了 card 但 agent hint crypto" 这种 case 直接失败。soft 降级到配置偏好，报错信息更有用。
- **为什么 HumanGate 不把"完成检测"交给 LLM 判断**：LLM 判断更鲁棒，但慢、贵、非确定性。我们只需要 polling 几个关键词 / URL / selector，用 LLM 是过度工程。真要做可以作为 fallback。
- **为什么保留 `wait_for_metamask_popup` alias**：v0.3.1 写的 playbook（如果社区有）不用改。新代码都用 `wait_for_human`。
- **为什么 audit 走 subprocess 而不是直接 API**：Codex CLI 可以运行在 read-only sandbox，降低 prompt injection 影响面；同时也是跟本地-模型 Codex clone 保持一致接口的方式。Claude auditor 目前走 SDK 直调（Anthropic 没 CLI 等价物），但如果以后有也可以切换。

---

*本文件是 v0.4 快照（2026-04-22）。更细节的设计论证见 `docs/architecture.md`，写 playbook 见 `docs/playbook_authoring.md`。*
