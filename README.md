# chkm8

Built by Mike Czarny ([r3lik](https://r3lik.com)) on a stock [Hermes Agent](https://hermes-agent.nousresearch.com) install.

Core code for chkm8, an autonomous crypto agent: the Lighter.xyz perps stack (position monitor,
trade runner, PnL report, paper bots), shared runtime helpers, email and X posting, wallet tooling,
and infra scripts. The container images (`ghcr.io/chkm8-agent/chkm8-{app,report}`) are built from
this directory.

| Path | What |
|---|---|
| `Dockerfile`, `.dockerignore` | `app` and `report` images (built by `chkm8-trading/build.sh`) |
| `chkm8env.py` | shared config: Mac defaults, env-var overrides in k8s, secrets from env or Keychain |
| `mailer.py`, `imap_*.py` | Gmail SMTP sender, IMAP delivery checks |
| `xpost.py` | post to X (OAuth1 in the cluster, xurl on the Mac) |
| `lighter/` | monitor, trade, report, paper bots, API key setup/rotation |
| `wallet/` | EVM signer (key stays in the macOS Keychain), vanity address search |
| `infra/` | server bootstrap (cloud-init), Keychain helpers, git privacy tooling (`infra/git/`) |
| `docs/` | EU server / k3s design notes |
| `assets/` | avatar art |

Related repos (separate checkouts under `~/chkm8/`):
[chkm8-research](https://github.com/chkm8-agent/chkm8-research) (`research/`),
[chkm8-wiki](https://github.com/chkm8-agent/chkm8-wiki) (`wiki/`),
[fantasy-basketball](https://github.com/chkm8-agent/fantasy-basketball) (`fantasy/`),
[chkm8-x](https://github.com/chkm8-agent/chkm8-x) (`x/`); private: `chkm8-trading` (k8s deploy
config, `deploy/`) and one personal repo.

## Local config

`lighter/account.json` is gitignored; copy `lighter/account.example.json` and fill it in.
Secrets (API keys, app passwords, wallet key) live only in the macOS Keychain or k8s Secrets.

## Privacy guard (`infra/git/`)

Every chkm8 repo is set up with `infra/git/setup-repo.sh <dir>`, which turns on:

- **redact filter**: private values (emails, wallet address, server IP, account ids, SSH key) are
  replaced with `<redacted:name>` placeholders when committed and restored on checkout, so files on
  disk work unchanged while GitHub only ever sees placeholders. The value list lives outside every
  repo in `~/.config/chkm8-git/redactions.json`.
- **pre-commit hook**: gitleaks + `pii_check.py` on the staged content; blocks emails, wallet
  addresses, public IPs, keys, big files and secret-looking filenames, and requires the GitHub
  noreply commit email.
- **pre-push hook**: the same scans over every commit being pushed.

To add a new private value, append it to `redactions.json`; to allow a known-public value (e.g. a
token contract), add a regex to `infra/git/pii-allow.txt` or the repo's `.pii-allow`.

## License

MIT, see [LICENSE](LICENSE).
