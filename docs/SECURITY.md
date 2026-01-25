# Security model

## Secrets
- Tokens live only in `secrets/secrets.json` (gitignored).
- Never commit tokens, DM texts, trusted/blacklist lists, or runtime state.

## Manager DMs
- Update text is private: `secrets/manager_update_message.txt`
- State: `secrets/manager_update_state.json` (prevents resends)
