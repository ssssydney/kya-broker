# Troubleshooting

## "broker: command not found"

`~/.local/bin` isn't on your PATH. Add this to `~/.zshrc` or `~/.bashrc`:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

Then `source ~/.zshrc` (or open a new shell).

## "auditor reject: no_concerns" but I see no reason

The auditor output may have been malformed JSON; the parser generates a synthetic
reject with `concerns=["auditor output was not valid JSON: ..."]`. Check
`broker status <intent_id>` — the raw output is in the `audit_results.raw_output`
field when `observability.retain_raw_audit_output: true`.

## "codex: command not found" after setup

The skill can't find the `codex` CLI on PATH. Either:
- Install it: `pip install codex-cli` (or however OpenAI distributes it), or
- Set `audit.primary: claude` in `config.yaml` if you don't want to use Codex.

If `codex` IS installed but you still see this, check
`audit.codex.binary_path` in config — set it to the absolute path.

## "Chrome not detected"

The skill looks for Chrome at a few common locations and on PATH. If your Chrome
is elsewhere:

```yaml
chrome:
  binary_path: /path/to/your/chrome
```

Or start Chrome yourself with `--remote-debugging-port=9222 --user-data-dir=/path/to/profile`
and the broker will attach to that instance instead of launching one.

## MetaMask popup never appears

Most common cause: MetaMask is locked. Open MetaMask manually and unlock it. You
can also set "Stay signed in for X minutes" inside MetaMask if the frequent
unlock prompts bother you, at the cost of weaker physical possession guarantees.

Less common: another modal in the page is intercepting focus. The broker dumps
the DOM to `kya-broker.local/dumps/` on these failures — inspect that to confirm.

## "daily cap … would be exceeded"

You're about to spend past `thresholds.daily_cap_usd` in a 24h window. Options:

1. Wait — the cap is rolling 24h, not calendar-day.
2. Raise the cap in `config.yaml` (requires thinking about why you're doing it).
3. Not use this topup today; use merchant credit you already have.

## "playbook_broken" on a merchant I've used successfully before

The merchant changed their UI. Steps to recover:

1. `broker status <intent_id>` — find the DOM dump path in transitions metadata.
2. Open the dump HTML, compare against the playbook's selectors/labels.
3. Edit the playbook YAML to match the new UI, open a PR.

If you need to get the experiment done *now*, manually top up the merchant and
proceed without the skill for that run.

## Setting up on a machine without `sudo`

All of install.sh runs in userspace. If `~/.local/bin` isn't yet in your PATH,
create it and the wrappers still work. No root required.

## Wiping state completely

```bash
rm -rf ~/.claude/skills/kya-broker.local
bash ~/.claude/skills/kya-broker/uninstall.sh
rm -rf ~/.claude/skills/kya-broker
```
