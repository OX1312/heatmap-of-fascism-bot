# Mastodon API Rate Limits

## Kritische Regeln

### Lösch-Operationen (DELETE)
- **~65 Sekunden** zwischen Löschungen warten
- Bei zu vielen Requests: HTTP 429 (Rate Limit) oder 412 Fehler
- **IMMER** `Retry-After` Header beachten bei 429 Responses
- Implementiert in: `hm/support/delete_runner.py` (Zeile 154: `time.sleep(65)`)

### Read-Operationen (GET)
- **Timeline/Tag Fetches:** ~500ms Pause zwischen Requests
- **Status Fetches:** ~500ms Pause
- **Context Fetches:** ~500ms Pause
- Bei 429: Mindestens 30-65 Sekunden warten

### Write-Operationen (POST)
- **Replies/Posts:** ~2-5 Sekunden zwischen Posts
- **Favorites:** Rate Limit kann sehr restriktiv sein

## Best Practices

### 1. Caching
```python
# Bot Account ID cachen (einmal pro Session)
my_id = cache.get("_bot_account_id")
if not my_id:
    # Nur einmal API-Call
    r_me = api_get(cfg, f"{inst}/api/v1/accounts/verify_credentials")
    cache["_bot_account_id"] = my_id
```

### 2. Batch Processing mit Limits
```python
# Nicht alle Posts auf einmal prüfen
for reply_info in analysis["replies"][:50]:  # Limit auf 50
    parent = fetch_parent_post(cfg, parent_id)
    time.sleep(0.5)  # Politeness delay
```

### 3. Retry-Logic
```python
if r.status_code == 429:
    ra = int(r.headers.get("Retry-After", "65"))
    log_line(f"RATE_LIMIT | retry_after={ra}")
    time.sleep(ra + 1)  # +1 für Sicherheit
    # Dann retry oder skip
```

## Aktuelle Implementierungen

### Delete Runner
- **File:** `hm/support/delete_runner.py`
- **Delay:** 65 Sekunden zwischen Löschungen
- **Retry:** Automatisch bei 429 mit Retry-After

### Find Self-Replies
- **File:** `hm/support/find_self_replies.py`
- **Delay:** 0.5 Sekunden zwischen Timeline-Fetches

### Spam Analysis
- **File:** `hm/support/analyze_spam.py`
- **Delay:** 0.5 Sekunden zwischen Parent-Post-Checks
- **Limit:** Max 50 Parent-Posts prüfen (nicht alle)

## Fehler-Codes

| Code | Bedeutung | Action |
|------|-----------|--------|
| 200 | OK | Weiter |
| 404 | Not Found | Skip (Post gelöscht) |
| 410 | Gone | Skip (Post permanent gelöscht) |
| 422 | Unprocessable | Skip (invalide Anfrage) |
| 429 | Too Many Requests | Warten (Retry-After Header) |

## Stack Overflow vermeiden

**Problem:** Zu viele API-Requests ⇒ 412 oder Stack Overflow

**Lösung:**
1. **Delays einhalten** (siehe oben)
2. **Batch-Größen limitieren** (max 50-100 items)
3. **Caching nutzen** (Account-IDs, Status-Info)
4. **Retry-After respektieren**
5. **Logging** für Monitoring

## Monitoring

Alle API-Anfragen sollten geloggt werden:
```python
log_line(f"API_CALL | endpoint={endpoint} | http={status_code}")
```

Bei Rate Limits:
```python
log_line(f"RATE_LIMIT | wait_s={wait_s} | endpoint={endpoint}")
```
