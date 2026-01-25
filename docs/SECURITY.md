# Security

## Strict rule
Security-sensitive details must never be published (GitHub, README, docs, public posts).

## What is considered sensitive
- access tokens / secrets
- any content inside `secrets/`
- internal admin workflows
- internal moderation/manager tooling details
- rate-limit / anti-abuse internals that can be weaponized

## Design approach (high level)
- secrets are stored locally and excluded from version control
- public repository contains only code + public dataset + public documentation
