# Security Policy

## Scope

This repository is a trading **bot framework**. It handles credentials for
broker accounts (MetaTrader 5, Bitget) and notification channels (Telegram,
Discord, SMTP). Reports are welcome for anything touching those surfaces.

## Reporting

Email: open a GitHub Security Advisory (Security tab → Report a vulnerability).
Do not open public issues for exploitable findings. You will get a response
within 7 days.

## Hard rules for users

1. **Never commit `.env`.** It is gitignored; keep it that way. Secrets live
   only in `.env` (or your secret manager).
2. **Rotate leaked tokens immediately.** A token pasted into any chat, issue
   or screenshot is burned — revoke it at
   github.com/settings/tokens and broker dashboards.
3. **Least privilege.** Exchange API keys: enable trading, disable
   withdrawals. MT5: prefer a dedicated sub-account.
4. **Run the bot under a dedicated OS user** (systemd user-service does this
   by design) with no shell access for other processes.

## Design notes

- Broker credentials are read from environment at process start only; they
  are never logged, persisted into state files, or included in the audit
  trail (audit records decisions, never secrets).
- The audit ledger (`data/audit.jsonl`) is append-only and contains market
  and decision data only.
- Outbound network calls: broker APIs, Yahoo/Dukascopy/ForexFactory feeds,
  notification webhooks you configure. Nothing else.
