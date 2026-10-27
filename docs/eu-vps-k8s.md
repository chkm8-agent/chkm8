# EU VPS + k8s: handoff brief

Start a new Hermes chat named **"EU VPS + k8s"** and say: *"Read ~/chkm8/docs/eu-vps-k8s.md and continue."*
This file is the full context; the new session doesn't need the old chat.

## Why this project exists
- chkm8 trades perps on **Lighter.xyz** (account <redacted:lighter-account>, L1 wallet <redacted:wallet-address>).
- **Orders are only placed from a dedicated EU server** (k3s); the Mac runs Hermes (research, decisions, digests) and
  never signs orders.
- Guard in place: `~/chkm8/lighter/trade.py` `client()` refuses to sign unless `~/.chkm8/trading_host` exists.
  Never create that file on the Mac.

## Agreed architecture
| EU server (k3s) | Mac (Hermes) |
|---|---|
| Position monitor: Deployment (`lighter/monitor.py`) | Hermes: chat, trade decisions, research, wiki |
| Trades: a Job per order, triggered from the Mac via `kubectl` | 7am market digest + Five Chains digest (Hermes cron), unchanged |
| 7am PnL email: CronJob (`lighter/report.py`, needs monitor state) | Keep-awake launchd service stays |
| Scalper later: Deployment (paper-test first) | `<redacted:wallet-prefix>` wallet key (Keychain) only, for rare deposits/withdrawals/key registration |
| Secrets: Lighter API key, X (xurl) creds, Gmail app password | `kubectl` via SSH tunnel |

Decisions made:
- **Hetzner Cloud + k3s** chosen 2026-10-05. Plan **CX23** (fallback CAX11), Ubuntu 24.04, Falkenstein/Nuremberg,
  ~€5.99 net incl. IPv4. Bootstrap: `~/chkm8/infra/cloud-init.yaml` (user chkm8, key-only SSH, ufw SSH-only,
  fail2ban, k3s without traefik/servicelb, secrets encryption). Plus a Hetzner Cloud Firewall: inbound TCP 22 only.
- **LIVE 2026-10-06:** server `chkm8-nue-1`, **<redacted:server-ip>**, Hetzner **Nuremberg** (nbg1-dc3), **x86_64 (amd64)**
  2 vCPU / 3.7 GiB / 38 GB, Ubuntu 24.04.5, k3s v1.36.5+k3s1. Bootstrap done; ufw/sshd/fail2ban/secrets-encryption
  verified; 6443 unreachable from the internet. Egress IP geolocates to Nuremberg, DE; Lighter API reachable (CloudFront FRA).
  So images must include **linux/amd64** (built under Rosetta); arm64 build is for local Mac testing.
- kubectl from Mac: `KUBECONFIG=~/.kube/chkm8-eu.yaml` (exported in ~/.zshrc; context chkm8-eu, server
  https://127.0.0.1:16443). Tunnel kept up by launchd agent **`com.chkm8.k3s-tunnel`**
  (`~/Library/LaunchAgents/com.chkm8.k3s-tunnel.plist`, KeepAlive, ServerAlive 15s x3, log `~/chkm8/logs/k3s-tunnel.log`);
  kill-and-respawn tested. Kubeconfig client cert = `system:masters` admin, expires 2027-10-06 (k3s renews server certs;
  re-copy kubeconfig after renewal). Kubeconfig = cluster admin; treat like the SSH key.
- Server arch switched to **arm64: Hetzner CAX11** (matches the M2, so locally tested images are what runs). Ubuntu
  **24.04** (not 26.04). k3s pinned to **v1.36.5+k3s1** in cloud-init; bootstrap runs `/usr/local/sbin/chkm8-bootstrap.sh`
  (log `/var/log/chkm8-bootstrap.log`; marker `/var/lib/cloud/chkm8-bootstrap-done` or `-failed`).
- Never create NodePort/hostPort services: k3s iptables rules bypass ufw; Hetzner Cloud Firewall is the real perimeter.
- k3s chosen over kubeadm: same API/manifests, ~1 GB less RAM, one-command install/upgrade.
- **k3s**, single node to start; API not public (SSH tunnel only; firewall allows SSH only).
- Mac tooling installed 2026-10-06: kubectl 1.37, colima 0.10.3 (vz+Rosetta, 4 CPU/6 GB/60 GB, brew service
  autostart), docker 29.8 (containerd image store, so multi-arch build+push works on the default builder), buildx 0.37
  (plugin dir in ~/.docker/config.json). Verified amd64 (Rosetta) and arm64 containers run.
- **Build images on the Mac** with **Colima (vz + Rosetta)** + `docker buildx`, multi-arch `linux/arm64,linux/amd64`
  (arm64 for local testing, amd64 for the server). Changed 2026-10-05 (was: build on server, no Colima).
- **Push to a private registry: GHCR, owner `chkm8-agent`** (GitHub user account). Images `ghcr.io/chkm8-agent/chkm8-app`
  and `ghcr.io/chkm8-agent/chkm8-report`. Tokens (classic PATs): push = `write:packages` in Keychain `chkm8-ghcr-push`;
  pull = `read:packages` only in Keychain `chkm8-ghcr-pull` -> cluster imagePullSecret. Stored via
  `~/chkm8/infra/store_secret.py` (hidden macOS dialog -> Keychain, validates scopes). docker login creds go to the
  macOS Keychain (`credsStore: osxkeychain`, docker-credential-helper), not plaintext config.json. No rsync.
  **Done 2026-10-06:** push token (scopes `repo, write:packages`, expires 2027-10-06) stored + `docker login ghcr.io` OK;
  pull token (`read:packages` only, no expiry) stored; namespace `chkm8` + Secret `ghcr-pull` attached to the `default`
  ServiceAccount. Smoke test: multi-arch push, anonymous pull = 401 (private), cluster pulled and ran amd64 image.
  TODO: rotate push token to drop `repo` scope; push token expires 2027-10-06.
- Workflow: buildx build+push -> `kubectl rollout restart` / new Job. Wrap in `deploy.sh`.
- Local testing covers the monitor, the report and the PNG only. Trade Jobs on the Mac stay dry-run because of the
  `trading_host` guard; real orders go out only from the cluster.
- Keep Chromium in a separate report image so the monitor and trade images stay small.
- **Keep it simple: no Git integration / GitOps** for now.
- Wallet key **never** goes into the cluster. New Lighter API key: sign the ChangePubKey on the Mac (pure crypto, nothing
  sent), submit it from the cluster, then revoke the Mac's API key (index 4).
- After cluster versions are verified: disable Mac launchd `com.chkm8.monitor` and `com.chkm8.pnlreport` (keep
  `com.chkm8.caffeinate`). Monitor state lives in `~/chkm8/lighter/monitor_state.json`; copy it into a PVC.
- PnL email: SMTP via Gmail app password (currently Keychain `chkm8-gmail-smtp`); drop the Mail.app fallback in-cluster.
  PNG rendering needs Chromium in the image (currently uses Brave on the Mac).

## SSH key for the server (public half)
```
ssh-ed25519 <redacted:ssh-pubkey> chkm8-mac-to-eu-server
```
Private key: `~/.ssh/chkm8_eu` on the Mac, passphrase-protected since 2026-10-06. Passphrase (random 33 bytes, never
displayed) is stored twice in Keychain: macOS's own SSH entry (added with `ssh-add --apple-use-keychain`, used
automatically) and the named item `chkm8-ssh-eu-passphrase` (readable copy for recovery/re-adding). `~/.ssh/config` host
**`chkm8-eu`** (user chkm8, `UseKeychain yes`, `AddKeysToAgent yes`; HostName to fill in). Login via the alias was
tested against a throwaway local sshd container.

## Containerization (done 2026-10-07)
- Code now reads config from env with Mac defaults: `~/chkm8/chkm8env.py` (paths, account, secrets env-then-Keychain,
  stdout logging via `CHKM8_LOGS=-`, `CHKM8_DRY_RUN=1` = no X posts / no cancels). `~/chkm8/xpost.py`: X posts via
  OAuth1 env tokens (server) else xurl (Mac); signer verified against RFC 5849 test vector. mailer.py reads
  `GMAIL_APP_PASSWORD` env first; no Mail.app fallback in containers. report.py: Chromium in containers (needs writable
  $HOME + /tmp; `--no-sandbox`), raises on screenshot failure. trade.py: guard marker path `CHKM8_TRADING_HOST_FILE`.
  Mac behaviour unchanged (monitor restarted on new code; --once, trade status/guard, report --dry all verified).
  Pre-change copies: `~/chkm8/backups/pre-container-20261007-1302/`.
- `~/chkm8/Dockerfile`: targets `app` (python:3.12-slim, lighter-sdk 1.1.6, uid 10001, only this arch's signer lib,
  ~345 MB arm64) and `report` (+ Chromium, emoji fonts, ~1.37 GB). `.dockerignore` whitelists only the copied files.
- `deploy/build.sh` (multi-arch push, tag = UTC timestamp + latest, writes deploy/LAST_TAG; `--local` = arm64 load).
  First push: tag **20261007-201216**; full build ~5.5 min (Chromium amd64 under Rosetta is the slow part).
- Tested locally: monitor --once/loop dry-run on a copy of the real state (state unchanged, heartbeat written, read-only
  root fs); trade status works, `--send` refused by the guard; report PNG renders correctly in Chromium (arm64 and
  amd64); real report email sent from the container to the chkm8 inbox only.
- Runtime needs for k8s: `/data` PVC (uid 10001), report pod needs emptyDir at /tmp and /home/chkm8.
- **PnL report stays on the server with Chromium + PNG** (operator likes the PNG; decided 2026-10-07). Revisit if memory
  gets tight: alternative is an HTML email via mailer.py's SMTP html support (table layout, no Chromium, ~345 MB image).
  Give the report CronJob a memory limit so Chromium can't starve the monitor; watch `kubectl top` after first runs.

## Cluster deploy (2026-10-07)
- Manifests in `~/chkm8/deploy/k8s/` (kustomize): namespace (PSA `restricted` enforced), NetworkPolicy deny-all-ingress,
  PVC `chkm8-data` (local-path 1Gi), Deployment `monitor` (1 replica, Recreate, liveness = heartbeat age < 180s,
  256Mi limit, no SA token), CronJob `pnl-report` (07:00 America/Los_Angeles, **suspended** until cutover, 1Gi limit),
  ConfigMap generated from `kustomization.yaml` literals (**CHKM8_DRY_RUN=1** until cutover). Secret `chkm8-secrets`
  is optional and not yet created (Lighter key, X OAuth1, Gmail pw go there at cutover).
- `deploy/deploy.sh` [--build] [--tag T] [--dry]: tunnel check -> image exists in GHCR -> pin tag -> `kubectl diff` ->
  `apply -k` -> rollout status. Live tag **20261007-203222**.
- Monitor running dry-run on a seeded copy of the Mac state (md5 matched), 55Mi RAM, heartbeat fresh, no token mounted,
  PSA rejects non-hardened pods. The Mac monitor is still the live one.
- Report CronJob not yet run in-cluster (needs GMAIL_APP_PASSWORD Secret); measure Chromium memory on first run.

## LIVE (cutover 2026-10-08 ~01:48 UTC)
- Cluster monitor live (`CHKM8_DRY_RUN=0`, Lighter key **index 5**), PnL CronJob enabled (07:00 PT, SMTP **587**: Hetzner
  blocks 25/465), image **20261008-014334**. Mac `com.chkm8.monitor` + `com.chkm8.pnlreport` booted out; plists in
  `~/Library/LaunchAgents/disabled/`. Mac keeps caffeinate, k3s-tunnel, Colima, Hermes digests.
- Lighter key 5: generated + wallet-signed on Mac (`lighter/rotate_api_key.py sign-new`), submitted from a cluster Job
  (egress DE). Key in Keychain `chkm8-lighter-api-cluster/<redacted:lighter-account>:5` -> Secret `lighter-api`. Key 4 revoked the same way
  (throwaway key, never stored); old key deleted from Keychain `chkm8-lighter-api`. **Mac can't sign trades.**
- Trades: `deploy/trade.py <trade.py args>` runs a Job with the `trading-host` ConfigMap mounted + `lighter-api`.
  Funding rule in trade.py: shorts need Lighter funding > 0; longs refused > 0.03%/8h.
- First cluster trade: HYPE long 0.68 @ 88.1648, SL 84.4, TP 97 (Job trade-live-long-hype-1007-191314); X entry
  2108017929979560195 (monitor, OAuth1 from server) + reply 2108018184263733736 (Mac xurl).
- Deploy config in private repo **github.com/chkm8-agent/chkm8-trading** (`~/chkm8/deploy`, git push via
  `~/chkm8/infra/git-credential-chkm8`, which reads the push token through Python keyring; `security` CLI blocks on an ACL prompt).
- `~/.kube/config` symlinks to `chkm8-eu.yaml`. Hermes `approvals.timeout` 900; operator later set approvals off.

## Autonomous trading (operator, 2026-10-08)
- Operator: "make us money", fully autonomous, **no kill switch** (explicit choice). Limits in `lighter/trade.py`: risk
  <= 1.5% of equity per trade (stop distance x size), max 3 open, no stacking, lev <= 3, SL mandatory, funding rule.
  Autonomous trades are **private**: `CHKM8_X_TRADES=0` (no X posts); results in the 07:00 PnL email.
- Backtest: `~/chkm8/research/bt/README.md`. Only 4h Donchian-20 LONG breakout in uptrend was positive (+0.19R OOS),
  all from Aug-Sep; shorts lost. No robust edge yet.
- Paper scanner: CronJob `paper-scanner` (every 15 min, no secrets) runs `lighter/paper_scanner.py`, state
  `/data/paper_scanner.json`, shown in the PnL email. Review after 1-2 weeks before any live automation.
- Hermes cron `75243ab3fb98` "chkm8 trade call" 06:00 + 18:00 PT: context script
  `~/.hermes/scripts/chkm8_trade_call_context.sh`, may trade via `deploy/trade.py --send` under the limits, logs every
  decision to `~/chkm8/logs/trade_calls.log`. Needs `approvals.mode: off` (cron_mode deny would block kubectl).
- Hermes cron `1be97f016d72` "chkm8 signal responder" every 5m, monitor-gated by `~/.hermes/scripts/chkm8_signal_watch.sh`
  (lists scanner signals from the last 7 days; agent wakes only when the list changes). On a new signal it enters live
  (stop 2 ATR, target 4 ATR, ~1.4% risk) unless price ran > 1 ATR or a red flag. Baseline run OK; NEAR dry run passed.
- **AUTO ENTRIES ON THE CLUSTER (operator, 2026-10-27), DRY RUN until the operator says go live:** CronJob
  `auto-entry` (`2-59/15`, 2 min after each scanner run) -> `lighter/auto_entry.py`: fresh (bar closed < 8h) crypto
  breakouts >= $5M/day, no-chase filters (> 1 ATR above / > 0.5 ATR below the signal close), stop 2 ATR, target 4 ATR,
  1.4% of equity at the stop (min-size up to $25, shrink once for the $20 total), then `trade.long()` so every
  trade.py assert applies. Mode `CHKM8_AUTO_ENTRY` (dry|live) sits in its own manifest; pause flag
  `/data/auto_entry.pause` (trade call may set/clear it); events in `/data/auto_entry.json`. While dry, the Mac
  responder `1be97f016d72` still trades live and logs a `COMPARE` line per signal; review job `f44f2b71cbb7`
  emails the operator on 11-03. Cutover: `~/.hermes/scripts/chkm8_auto_entry_cutover.py --apply` (manifest -> live +
  deploy, responder becomes the post-entry "entry review" job on `chkm8_auto_entry_watch.sh`, trade call gains the
  AUTO ENTRIES pause/resume block). r3lik.com's board reads the mode from the CronJob (TESTING -> LIVE).
- **LIVE ENTRIES RESUMED (operator, 2026-10-27):** responder `1be97f016d72` resumed (now ignores signals whose 4h bar
  closed > 8h ago, so pause-era signals are skipped as stale); the PAUSED block was removed from the trade-call prompt.
  Operator: backtests are evidence, not orders; trade on live signals and sentiment inside the code limits. A weekly
  backtest runs Mondays 05:30 PT (report only, shown in the 07:00 PnL email): CronJob `weekly-backtest` ->
  `lighter/weekly_backtest.py` -> `/data/weekly_backtest.json` (breakout on crypto >= $5M/day, 120 days; index dips
  replayed through `paper_mr.step()`, 60 days; last 30/7 days, R by month, paper-bot comparison, breadth/BTC regime).
  report.py shows it when < 30h old. First run 10-27 matched paper bot 2 trade for trade. Previous state, for history:
- **LIVE BREAKOUT ENTRIES PAUSED (2026-10-22)** after 2W/16L: signal responder `1be97f016d72` paused; trade call
  `75243ab3fb98` only reviews open positions (no new entries). Breakout scanner now crypto-only, $5M/day floor
  (image 20261023-045607). Resume proposal (operator decides): >60% of crypto perps above EMA200 (4h) AND paper
  scanner 3 of last 5 wins. Regime backtest in `research/bt/mr/README.md`.
- Paper bot 2 (2026-10-16): CronJob `paper-mr` (`1-59/15 * * * *`, no secrets) runs `lighter/paper_mr.py`, 15m
  index dip-buying on US500/US100/SPY/QQQ/AAPL (rules: `research/bt/mr/README.md`; crypto dip-buying failed the
  backtest). State `/data/paper_mr.json`, second paper section in the PnL email. `research/bt/mr_paper_check.py`
  proves the bot reproduces the backtest trade for trade. Review after 2-3 weeks (~15 trades) vs +0.23R OOS.

## X posting policy (operator, 2026-10-08)
- Each new position on the main account: own top-level post; its close replies under it.
- Cap **3 top-level trade posts per Pacific day** (`CHKM8_X_TOPLEVEL_PER_DAY`). Trade 4+ goes into that day's
  "📒 trade log" thread (root posted on first overflow; entries chain under it). State: `x_day` in monitor_state.json.
- **Scalper / HF: no per-trade posts, one daily summary post** (trades, win rate, PnL, best/worst). Requirements when
  built: run on its **own Lighter sub-account** (so the main monitor never sees its fills), own Deployment with posting
  disabled, daily summary CronJob. Paper-test first.

## Waiting on the operator
1. ~~Server IP~~ done: <redacted:server-ip> (`ssh chkm8-eu`).
2. ~~Registry~~ done: GHCR `chkm8-agent`. Optional: rotate push token without `repo` scope.
3. X OAuth 1.0a tokens (Read and write) for the server monitor.
4. Confirm the Hetzner Cloud Firewall is attached to chkm8-nue-1.

## First trades queued (re-check prices first)
- PUMP long, $60 notional, 2x isolated, SL 0.00565, TP 0.0072 (planned at ~0.00628).
- HYPE long, $60 notional, 2x isolated, SL 84.4, TP 97 (planned at ~90.0).
- Account flat at $193.88 as of 2026-10-04. Monitor posts opens/closes to X (@Chkm8_agent) as replies in one thread.

## Learning path (operator wants to learn k8s)
1. k3s + monitor Deployment + Secrets + `kubectl logs`
2. Trade Jobs (first real trades from cluster)
3. ConfigMaps, probes, resource limits
4. (later) scalper with risk limits; GitOps only if operator asks
