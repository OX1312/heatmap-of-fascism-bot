import requests
import time
from typing import Dict, Any, Optional
from ..utils.log import log_line

MASTODON_TIMEOUT_S = 25

def _api_headers(cfg: Dict[str, Any]) -> Dict[str, str]:
    token = str(cfg.get("access_token", "") or "")
    # User-Agent strictly required by some instances
    ua = str(cfg.get("user_agent", "HeatmapBot/1.0") or "HeatmapBot/1.0")
    return {
        "Authorization": f"Bearer {token}",
        "User-Agent": ua,
    }

def api_get(cfg: Dict[str, Any], url: str, params: Dict[str, Any] | None = None) -> requests.Response:
    return requests.get(url, headers=_api_headers(cfg), params=params, timeout=MASTODON_TIMEOUT_S)

def api_post(cfg: Dict[str, Any], url: str, data: Dict[str, Any]) -> requests.Response:
    return requests.post(url, headers=_api_headers(cfg), data=data, timeout=MASTODON_TIMEOUT_S)

def api_delete(cfg: Dict[str, Any], url: str) -> requests.Response:
    return requests.delete(url, headers=_api_headers(cfg), timeout=MASTODON_TIMEOUT_S)

def verify_credentials(cfg: Dict[str, Any]) -> bool:
    inst = str(cfg.get("instance_url", "") or "").rstrip("/")
    if not inst:
        return False
    try:
        r = api_get(cfg, f"{inst}/api/v1/accounts/verify_credentials")
        return r.status_code == 200
    except Exception:
        return False

def fetch_status(cfg: Dict[str, Any], status_id: str) -> Optional[Dict[str, Any]]:
    inst = str(cfg.get("instance_url", "") or "").rstrip("/")
    if not inst: return None
    try:
        r = api_get(cfg, f"{inst}/api/v1/statuses/{status_id}")
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return None

def fetch_timeline(cfg: Dict[str, Any], tag: str, since_id: Optional[str] = None, limit: int = 40) -> list:
    inst = str(cfg.get("instance_url", "") or "").rstrip("/")
    if not inst: return []
    # clean tag
    tag = tag.lstrip("#")
    
    params = {"limit": limit, "only_media": "false"} # we filter media ourselves to allow helpful text replies
    if since_id:
        params["since_id"] = since_id
        
    try:
        from urllib.parse import quote
        # Use simple timeline API
        url = f"{inst}/api/v1/timelines/tag/{quote(tag)}"
        r = api_get(cfg, url, params=params)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        log_line(f"WARN | fetch_timeline {tag} failed: {e}")
    return []

def get_favourited_by(cfg: Dict[str, Any], status_id: str) -> list[str]:
    """Return list of account handles who favourited the status."""
    inst = str(cfg.get("instance_url", "") or "").rstrip("/")
    out = []
    try:
        url = f"{inst}/api/v1/statuses/{status_id}/favourited_by"
        r = api_get(cfg, url, params={"limit": 60})
        if r.status_code != 200:
            return []
        data = r.json()
        if isinstance(data, list):
            for acc in data:
                acct = str(acc.get("acct") or acc.get("username") or "").strip().lower()
                if acct:
                    out.append(acct)
    except Exception:
        pass
    return out

def is_approved_by_fav(cfg: Dict[str, Any], status_id: str, trusted_set: set) -> bool:
    favs = get_favourited_by(cfg, status_id)
    for f in favs:
        # handle check: handle or handle@instance
        base = f.split("@")[0]
        if base in trusted_set or f in trusted_set:
            return True
    return False

def reply_once(cfg: Dict[str, Any], cache: Dict[str, Any], cache_key: str, status_id: str, text: str) -> bool:
    """Send reply if not already cached as sent."""
    if cache.get(cache_key):
        return True
    
    inst = str(cfg.get("instance_url", "") or "").rstrip("/")
    
    # CRITICAL: Comprehensive Reply Validation
    # NEVER reply unless ALL conditions are met:
    # 1. Not a self-reply
    # 2. Parent has @HeatmapofFascism mention
    # 3. Parent has #sticker_report or #graffiti_report
    # 4. Not a duplicate (haven't replied to this post already)
    
    try:
        # Get our account ID (cached)
        my_id = cache.get("_bot_account_id")
        if not my_id:
            r_me = api_get(cfg, f"{inst}/api/v1/accounts/verify_credentials")
            if r_me.status_code == 200:
                my_id = str(r_me.json().get("id", ""))
                if my_id:
                    cache["_bot_account_id"] = my_id
        
        # Fetch parent post for validation
        if status_id:
            parent_status = fetch_status(cfg, status_id)
            if not parent_status:
                # Parent doesn't exist - skip
                log_line(f"REPLY_BLOCKED | id={status_id} | reason=parent_not_found")
                cache[cache_key] = int(time.time())
                return True
            
            # 1. Check: Self-Reply Guard
            parent_account_id = str((parent_status.get("account") or {}).get("id") or "")
            if my_id and parent_account_id == my_id:
                log_line(f"REPLY_BLOCKED | id={status_id} | reason=self_reply")
                cache[cache_key] = int(time.time())
                return True
            
            # 2. Check: Required Mention
            content = (parent_status.get("content") or "").lower()
            has_mention = "@heatmapoffascism" in content
            if not has_mention:
                log_line(f"REPLY_BLOCKED | id={status_id} | reason=missing_mention")
                cache[cache_key] = int(time.time())
                return True
            
            # 3. Check: Required Hashtag
            has_hashtag = "#sticker_report" in content or "#graffiti_report" in content
            if not has_hashtag:
                log_line(f"REPLY_BLOCKED | id={status_id} | reason=missing_hashtag")
                cache[cache_key] = int(time.time())
                return True
            
            # 4. Check: Duplicate Guard
            # Check if we already replied to this parent (different cache key!)
            duplicate_check_key = f"replied_to_parent_{status_id}"
            if cache.get(duplicate_check_key):
                log_line(f"REPLY_BLOCKED | id={status_id} | reason=already_replied_to_parent")
                cache[cache_key] = int(time.time())
                return True
                
    except Exception as e:
        log_line(f"WARN | reply_validation failed | id={status_id} | err={e}")
        # CRITICAL: On validation error, BLOCK the reply (fail-safe)
        cache[cache_key] = int(time.time())
        return True
    
    try:
        data = {
            "status": text,
            "in_reply_to_id": status_id,
            "visibility": "public" 
        }
        r = api_post(cfg, f"{inst}/api/v1/statuses", data)
        if r.status_code in (200, 202, 404, 422):
            # 2xx = success, 404 = parent deleted (count as done), 422 = unprocessable (count as done)
            cache[cache_key] = int(time.time())
            # Also mark this parent as "replied to" to prevent future duplicates
            duplicate_check_key = f"replied_to_parent_{status_id}"
            cache[duplicate_check_key] = int(time.time())
            return True
    except Exception as e:
        log_line(f"ERROR | reply_once failed | id={status_id} | err={e}")
    return False
