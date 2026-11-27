---
name: claude-env-setup
description: Master orchestrator skill for Claude ENV Setup. Guides AI agents step-by-step to prepare a clean, consistent Windows or macOS environment for Claude (especially for users recovering from banned accounts or setting up Claude for the first time) by chaining three sub-skills in strict order: 1) claude-env-cleanup (clean local Claude state & credentials) -> 2) claude-network (configure Clash Verge Rev with a fixed overseas exit, DNS/IPv6 leak protection, and kill switch) -> 3) claude-browser-env (align system timezone/region/language and browser/antidetect browser setup), followed by a unified end-to-end acceptance report.
---

# Claude ENV Setup — Master Orchestrator Skill

This skill is the top-level entry point for **Claude ENV Setup**. It instructs the AI agent to execute three sub-skills in a strict sequence and produce a single **Final End-to-End Acceptance Report** at the end.

## Core Principles (Read Before Starting)

1. **Follow the strict order — do not skip or reorder steps**:
   - **Step 1: Local Environment Cleanup** (`claude-env-cleanup/SKILL.md`)
   - **Step 2: Network & Fixed Overseas Exit** (`claude-network/SKILL.md`)
   - **Step 3: Timezone, Language & Browser Environment** (`claude-browser-env/SKILL.md`)
   - **Step 4: Final End-to-End Acceptance Report**
   - *Why*: Step 3 detects the overseas exit IP and city configured in Step 2 to determine the matching system timezone and language. Step 1 removes stale accounts, relay keys, and credentials first to avoid cross-account contamination or accidental routing through old relays.
2. **Clear Separation of Responsibilities**:
   - **AI runs**: Read-only audits, result summaries, consolidated user questions, plan/script generation, and verification checks.
   - **User runs**: All write operations and admin/`sudo` scripts in a real terminal (manually typing `CONFIRM` / `RESTORE`), plus manual browser or antidetect browser settings per checklist.
   - **Never hand-craft destructive shell commands** (`rm`, `Remove-Item`, `reg delete`, `pfctl`, `tzutil`, `systemsetup`). Always use the bundled scripts in each sub-skill.
3. **Critical Operational Warnings**:
   - **Do not run Step 1 write operations from inside Claude Code**: A running Claude Code instance using `~/.claude` cannot clean itself. If `under_claude_code` is true, stop after `--audit` and ask the user to run the cleanup script in a regular terminal.
   - **Deep cleanup vs. `~/.claude/skills/`**: If the user installed this repo inside `~/.claude/skills/`, running Step 1 with `--mode deep` will move `~/.claude/skills/` into `~/ClaudeCleanup/<timestamp>/moved/06-自定义配置/`. Warn the user beforehand to keep this repo outside `~/.claude/skills/` (or re-copy the skills after deep cleanup).
   - **Step 2 temporarily interrupts network connectivity**: The AI itself relies on the host's proxy. Never split proxy restarts or kill-switch changes into manual step-by-step commands — always use `claude-network`'s self-healing background scripts or user-run admin scripts.
4. **Scope & Compliance**:
   - This workflow cleans local leftovers, prevents DNS/IPv6/WebRTC leaks, and keeps local environment signals consistent.
   - It does **not** guarantee avoiding future bans and cannot unban suspended accounts (bans live server-side and can only be resolved via official Anthropic appeals). Always comply with Anthropic's Terms of Service and supported-region requirements.

---

## Step 0: Quick Triage (Ask Once Upfront)

Before running any scripts, gather these basics in a single message:

1. **OS**: Windows or macOS?
2. **Current situation**:
   - Used Claude before or had an account banned → **Start at Step 1** (`--mode deep` recommended after a ban).
   - Brand-new machine never used with Claude → Run Step 1 `--audit` to confirm clean, then proceed to Step 2.
   - Only fixing network or browser/timezone → Verify prerequisites are met, then jump to that step.
3. **Prerequisites**:
   - **Python 3.9+** installed (`python3 --version` on macOS, `py -3 --version` on Windows)?
   - **Clash Verge Rev 2.x** installed with an active subscription? (Step 2 automates Clash Verge Rev only.)
   - Planning to log in to Claude via a **regular local browser** (Chrome/Edge/Firefox) or an **antidetect browser** (AdsPower, BitBrowser, etc.)?

---

## Step 1: Clean Local Environment (`claude-env-cleanup`)

> Read [`claude-env-cleanup/SKILL.md`](claude-env-cleanup/SKILL.md) in full and follow its exact procedure.

1. **Read-only audit**: Run `claude-env-cleanup/scripts/claude_env_cleanup.py --audit --json` and summarize:
   - Active config directory and whether legacy `.config.json` is active;
   - Credential locations, recorded projects, MCP servers, and any relay/proxy key names in `settings.json` (**never echo secret values**);
   - `shell_relay_vars` (relay URLs or API keys in shell profiles / user env vars — remind the user to remove these manually).
2. **User executes cleanup in a real terminal**:
   - **Basic (`--mode basic`)**: Cleans caches, credentials, Keychain/Credential Manager entries, account fields, relay keys, sessions/projects/memory, and rotates `userID`/`machineID` (including backups), while preserving MCP/plugins/skills/custom configs.
   - **Deep (`--mode deep`)**: Moves almost the entire config directory and global config (preserving only telemetry keys with their original values). **Strongly recommended after an account ban**, along with clearing Claude Desktop data.
   - User quits all Claude Code / Desktop processes, runs `claude daemon stop`, runs the script in a real terminal, and types `CONFIRM`.
3. **Gate check before Step 2**:
   - Re-run `--audit --json` to confirm old credentials and relay keys are gone and no partial-failure warning remains.
   - Record the backup batch path (`~/ClaudeCleanup/<timestamp>/`) for the final report.

---

## Step 2: Configure Network & Fixed Overseas Exit (`claude-network`)

> Read [`claude-network/SKILL.md`](claude-network/SKILL.md) (and its `references/` as needed) and follow Steps 1–7 strictly.

1. **Read-only check & node latency**: Run `cnet doctor` and `cnet nodes`. Resolve any `✗` blockers and review `?` warnings (other proxies, Tailscale DNS override, missing Service Mode).
2. **Confirm plan in one message**:
   - **Plan A (Clean VPS / fixed-IP datacenter node)**: Must not be in Mainland China, Hong Kong, or Macau, and must have a single fixed exit IP.
   - **Plan B (Static Residential / ISP SOCKS5 chained proxy)**: Recommended if airport/shared nodes rotate IPs or trigger frequent verifications. Pass residential credentials strictly via **stdin** to `cnet plan` — never via CLI arguments or logs.
3. **Mandatory Backup & Emergency Sheet**:
   - Run `cnet backup` and `cnet emergency`. Send the **entire** emergency sheet text to the user and ask them to photograph it with their phone before making any changes.
4. **Plan & Background Switch**:
   - Run `cnet plan` (validated with `mihomo -t`) → show diff summary → run `cnet apply --plan <dir>` → check `cnet status --plan <dir>` until `OK`.
5. **DNS / IPv6 Hardening + Verify Items 1–6**:
   - Run `cnet harden` (user runs generated admin script) → run `cnet verify --items 1,2,3,4,5,6`.
   - **All items 1–6 must be `pass`** before installing the kill switch (`unknown` is not a pass). If item 4 shows multiple exit IPs or a disallowed region, stop and switch to Plan B.
6. **Kill Switch + Verify 7–8 + Drills 9–11**:
   - Run `cnet killswitch` (user runs as admin) → `cnet verify --items 7,8` → `cnet drills --plan <dir>` (user runs as admin) → `cnet verify` for the full 11-item summary.
7. **Gate check before Step 3**:
   - Record the verified **fixed overseas exit IP, country, city**, the **earliest backup directory**, and the **kill-switch disable command**.

---

## Step 3: Align Timezone, Language & Browser (`claude-browser-env`)

> Read [`claude-browser-env/SKILL.md`](claude-browser-env/SKILL.md) (and its `references/`) and follow Steps 1–6 strictly.

1. **Read-only audit**: Ask the user to close browsers, then run `claude-browser-env/scripts/claude_browser_env.py --audit --json`.
   - Verify the direct (TUN) exit is the overseas exit from Step 2. If it still shows Mainland China, return to Step 2 immediately.
2. **Prepare the Claude browser**:
   - **Local browser (Chrome / Edge / Firefox / Safari)**: If previously used for Claude or a banned account, **create a brand-new dedicated Profile** (or clear all site data/cache for `claude.ai`, `claude.com`, `anthropic.com`); install **WebRTC Protect** (or configure Firefox `about:config`); align browser preferred languages with `browser_languages`.
   - **Antidetect browser (AdsPower, BitBrowser, etc.)**: Follow `claude-browser-env/references/fingerprint-browser.md` (match real OS, set WebRTC to **Disabled** when routing through Clash TUN, IP-based timezone/geo/language, disable unnecessary Canvas/WebGL/Audio noise).
   - **Default browser note**: Claude Code opens the system default browser during OAuth login. If the dedicated Claude browser/profile is not the default, remind the user to copy the login URL from the terminal into the dedicated browser.
3. **Align system timezone, region, and language**:
   - If `auto_timezone` is enabled, ask the user to disable automatic timezone in OS settings first.
   - AI runs `--plan` → User runs `--apply` with matching flags in a real terminal and types `CONFIRM` (macOS requires `sudo` password).
   - Ask the user to fully quit and reopen the browser (and log out/in for OS region/language changes to take full effect).
4. **Gate check**:
   - Re-run `--audit --json`: `recommendation.ops` is empty, target profile shows `webrtc: protected` and `language_ok: true`.
   - Ask the user to verify <https://browserleaks.com/webrtc> and <https://www.browserscan.net> in the target browser.

---

## Step 4: Final End-to-End Acceptance Report (Required)

Once all three steps finish, output a consolidated **Final Acceptance Report** strictly grounded in actual script outputs (never fabricate or gloss over failures):

```markdown
## Claude ENV Setup — Final Acceptance Report

### 1. Local Cleanup (`claude-env-cleanup`)
- Mode: Basic / Deep / Skipped (fresh machine)
- Re-audit status: Credentials & old relay configs cleared / Remaining items listed
- Manual check: Old `ANTHROPIC_*` variables removed from shell profile / OS env vars
- Rollback path: `~/ClaudeCleanup/<timestamp>/`

### 2. Network & Exit (`claude-network`)
- Exit mode: Plan A (Fixed VPS/DC) / Plan B (Static Residential SOCKS5 Chain)
- Fixed exit IP & location: `<IP>` (`<Country / City>`)
- 11-item verification:
  - Items 1–6 (Rules / Split routing / Single exit IP / DNS / TUN): list pass / fail / unknown
  - Items 7–8 (Kill switch install & active): pass / fail / unknown
  - Items 9–11 (Dead node / TUN off / Client quit drills): pass / fail / unknown
- One-click restore: `<earliest backup restore command>`
- Disable kill switch: `<off.sh / off.cmd command>`
- Desktop emergency sheet: Generated & verified

### 3. Timezone, Language & Browser (`claude-browser-env`)
- Login browser: Local (`<Browser / Profile>`) / Antidetect (`<Name>`)
- System timezone & region: `<Timezone>` / `<Region>` (matches exit city; auto-timezone off)
- System & browser languages: `<Language list>` (`recommendation.ops` empty)
- WebRTC protection: `protected`
- Live browser checks (user-confirmed):
  - `browserleaks.com/webrtc`: Shows overseas exit IP only, no local ISP leak
  - `browserscan.net`: IP timezone = system timezone, matching language, no DNS/WebRTC leak
- Rollback path: `~/ClaudeBrowserEnv/<timestamp>/`

### 4. Daily Usage Reminders
- Always use only the dedicated browser/profile or antidetect window for `claude.ai` and its associated email.
- For Claude Code OAuth login, copy the terminal URL into the dedicated browser if it is not your system default.
- If connectivity ever drops, follow the desktop emergency sheet first, then return to this chat to inspect status before retrying.
```

Declare **Overall Acceptance PASSED** only when all automated checks pass (no `fail` or unresolved `unknown`) and the user confirms the manual browser checks. Otherwise, list exact blockers and remediation steps.
