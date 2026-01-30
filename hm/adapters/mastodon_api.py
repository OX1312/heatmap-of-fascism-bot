import requests
import time
from pathlib import Path
from typing import Dict, Any, Optional, List, Set, Union
from ..utils.log import log_line

MASTODON_TIMEOUT_S = 25

# Separate mute flags for different message types
_ROOT = Path(__file__).resolve().parent.parent.parent
_MUTE_REPORTS = _ROOT / ".mute_reports"   # For #*_reports hashtag replies
_MUTE_OTHER = _ROOT / ".mute_other"       # For DMs and other posts

def is_muted(msg_type: str = "other") -> bool:
    """Check if message type is muted.
    
    Args:
        msg_type: "reports" for #*_reports replies, "other" for everything else
    """
    if msg_type == "reports":
        return _MUTE_REPORTS.exists()
    return _MUTE_OTHER.exists()

def _api_headers(cfg: Dict[str, Any]) -> Dict[str, str]:
    token = str(cfg.get("access_token", "") or "")
    ua = str(cfg.get("user_agent", "HeatmapBot/1.0") or "HeatmapBot/1.0")
    return {
        "Authorization": f"Bearer {token}",
        "User-Agent": ua,
    }

def api_get(cfg: Dict[str, Any], url: str, params: Optional[Dict[str, Any]] = None) -> requests.Response:
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

def fetch_timeline(cfg: Dict[str, Any], tag: str, since_id: Optional[str] = None, limit: int = 40) -> List[Dict[str, Any]]:
    inst = str(cfg.get("instance_url", "") or "").rstrip("/")
    if not inst: return []
    tag = tag.lstrip("#")
    
    params = {"limit": limit, "only_media": "false"}
    if since_id:
        params["since_id"] = since_id
        
    try:
        from urllib.parse import quote
        url = f"{inst}/api/v1/timelines/tag/{quote(tag)}"
        r = api_get(cfg, url, params=params)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        log_line(f"WARN | fetch_timeline {tag} failed: {e}")
    return []

def get_favourited_by(cfg: Dict[str, Any], status_id: str) -> List[str]:
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

def is_approved_by_fav(cfg: Dict[str, Any], status_id: str, trusted_set: Set) -> bool:
    favs = get_favourited_by(cfg, status_id)
    for f in favs:
        base = f.split("@")[0]
        if base in trusted_set or f in trusted_set:
            return True
    return False

def reply_once(cfg: Dict[str, Any], cache: Dict[str, Any], cache_key: str, status_id: str, text: str) -> bool:
    if cache.get(cache_key):
        return True
    inst = str(cfg.get("instance_url", "") or "").rstrip("/")
    
    try:
        my_id = cache.get("_bot_account_id")
        if not my_id:
            r_me = api_get(cfg, f"{inst}/api/v1/accounts/verify_credentials")
            if r_me.status_code == 200:
                my_id = str(r_me.json().get("id", ""))
                if my_id:
                    cache["_bot_account_id"] = my_id
        
        if status_id:
            parent_status = fetch_status(cfg, status_id)
            if not parent_status:
                log_line(f"REPLY_BLOCKED | id={status_id} | reason=parent_not_found")
                cache[cache_key] = int(time.time())
                return True
            
            parent_account_id = str((parent_status.get("account") or {}).get("id") or "")
            if my_id and parent_account_id == my_id:
                log_line(f"REPLY_BLOCKED | id={status_id} | reason=self_reply")
                cache[cache_key] = int(time.time())
                return True
            
            content = (parent_status.get("content") or "").lower()
            has_mention = "@heatmapoffascism" in content
            if not has_mention:
                 log_line(f"REPLY_BLOCKED | id={status_id} | reason=missing_mention")
                 cache[cache_key] = int(time.time())
                 return True

            has_hashtag = "#sticker_report" in content or "#graffiti_report" in content
            if not has_hashtag:
                log_line(f"REPLY_BLOCKED | id={status_id} | reason=missing_hashtag")
                cache[cache_key] = int(time.time())
                return True
            
            duplicate_check_key = f"replied_to_parent_{status_id}"
            if cache.get(duplicate_check_key):
                log_line(f"REPLY_BLOCKED | id={status_id} | reason=already_replied_to_parent")
                cache[cache_key] = int(time.time())
                return True
                
    except Exception as e:
        log_line(f"WARN | reply_validation failed | id={status_id} | err={e}")
        cache[cache_key] = int(time.time())
        return True
    
    if is_muted("reports"):
        log_line(f"MUTED_REPORTS | reply skipped | id={status_id}")
        cache[cache_key] = int(time.time())
        return True
    try:
        data = {"status": text, "in_reply_to_id": status_id, "visibility": "public"}
        r = api_post(cfg, f"{inst}/api/v1/statuses", data)
        if r.status_code in (200, 202, 404, 422):
            cache[cache_key] = int(time.time())
            duplicate_check_key = f"replied_to_parent_{status_id}"
            cache[duplicate_check_key] = int(time.time())
            return True
    except Exception as e:
        log_line(f"ERROR | reply_once failed | id={status_id} | err={e}")
    return False

def send_dm(cfg: Dict[str, Any], acct: str, text: str) -> bool:
    if is_muted("other"):
        log_line(f"MUTED_OTHER | DM skipped | to={acct}")
        return True
    inst = str(cfg.get("instance_url", "") or "").rstrip("/")
    if not inst: return False
    target_mention = acct if acct.startswith("@") else f"@{acct}"
    full_text = f"{target_mention} {text}"
    try:
        data = {"status": full_text, "visibility": "direct"}
        r = api_post(cfg, f"{inst}/api/v1/statuses", data)
        return r.status_code in (200, 202)
    except Exception as e:
        log_line(f"ERROR | send_dm failed | to={acct} | err={e}")
        return False

def post_status(cfg: Dict[str, Any], text: str, visibility: str = "public") -> bool:
    if is_muted("other"):
        log_line(f"MUTED_OTHER | post skipped | text={text[:30]}...")
        return True
    inst = str(cfg.get("instance_url", "") or "").rstrip("/")
    if not inst: return False
    try:
        data = {"status": text, "visibility": visibility}
        r = api_post(cfg, f"{inst}/api/v1/statuses", data)
        return r.status_code in (200, 202)
    except Exception as e:
        log_line(f"ERROR | post_status failed | val={text[:20]}... | err={e}")
        return False
