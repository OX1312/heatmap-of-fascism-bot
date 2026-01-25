# Ops (Run / Deploy / Verify)

## One-shot verify (safe)
- git clean (status/diff)
- secrets are NOT tracked (git check-ignore / git ls-files)
- py_compile
- bot.py --once
- check logs (launchd + normal/event)

## Commands
- status: `./ox bot_status`
- restart: `./ox bot_restart`
- errors: `./ox show_errors`
