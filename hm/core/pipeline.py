from typing import List, Dict, Any, Optional
import time
from pathlib import Path

from .models import PipelineResult
from .constants import ACC_FALLBACK, ACC_GPS
from ..adapters.mastodon_api import (
    fetch_timeline, reply_once, is_approved_by_fav, send_dm
)
from ..domain.parse_post import (
    strip_html, parse_location, has_image, parse_type_and_medium, parse_note
)
from ..domain.location import geocode_query_worldwide, snap_to_public_way
from ..domain.dedup import attempt_dedup
from ..domain.entities import EntityRegistry
from ..domain.geojson_normalize import normalize_reports_geojson
from ..utils.log import log_line
from ..support.support_replies import (
    build_reply_missing, build_reply_pending, build_needs_info_reply,
    build_reply_removed_confirmation, build_reply_confirmed_confirmation
)
from ..support.state import load_trusted_accounts

class Pipeline:
    def __init__(self, cfg: Dict[str, Any], cache: Dict[str, Any], pending: List[Dict[str, Any]], reports: Dict[str, Any]):
        self.cfg = cfg
        self.cache = cache
        self.pending = pending
        self.reports = reports
        self.reports_ids = set()
        
        for f in self.reports.get("features", []):
            if f.get("properties", {}).get("item_id"):
                self.reports_ids.add(f["properties"]["item_id"])

        self.pipeline_result = PipelineResult()
        
        # Load entity registry from root entities.json
        entities_path = Path("entities.json")
        self.entity_registry = EntityRegistry.from_file(entities_path)

    def run_cycle(self):
        """Run one full cycle."""
        # 1. Ingest
        self._ingest_timeline()
        # 2. Process
        self._process_pending()

    def _has_required_mention(self, st: Dict[str, Any]) -> bool:
        """Check if the status explicitly mentions the bot."""
        required = self.cfg.get("required_mentions") or ["HeatmapofFascism"]
        content = strip_html(st.get("content") or "")
        
        # Check text mention
        if any(m.strip().lstrip("@") in content for m in required):
            return True
            
        # Check mentions array
        mentions = st.get("mentions") or []
        for m in mentions:
            acct = m.get("acct") or m.get("username")
            if acct and any(req.lower().strip().lstrip("@") in acct.lower() for req in required):
                return True
                
        return False

    def _ingest_timeline(self):
        tags = self.cfg.get("hashtags") or ["sticker_report", "sticker_removed"]
        for tag in tags:
            timeline = fetch_timeline(self.cfg, tag)
            for st in timeline:
                self._handle_status(st, tag)

    def _handle_status(self, st: Dict[str, Any], tag: str):
        status_id = st.get("id")
        url = st.get("url")
        if not status_id or not url: return

        # CRITICAL: Strict Mention Check (Rule #1)
        # The bot must NEVER respond or act unless explicitly mentioned.
        if not self._has_required_mention(st):
             return

        # 0. Check for update replies or threaded conversations
        if st.get("in_reply_to_id"):
             # We passed 'tag' but we should check if the tag implies an update action
             # The tag comes from the loop over configured hashtags.
             # If tag is report_again or sticker_removed, we try update logic.
             if "report_again" in tag or "sticker_removed" in tag:
                 if self._handle_update_reply(st, tag):
                     # Successfully handled (or ignored silently). Return to avoid processing as new report.
                     return
             
             # CRITICAL: If it is a reply but not a valid update command (or failed update),
             # we MUST ignore it. We do not want to parse threads as new reports.
             # This prevents "Missing photo" spam in discussion threads.
             return

        item_id = f"masto-{status_id}"
        if item_id in self.reports_ids: return
        for p in self.pending:
            if str(p.get("source")) == url: return

        content = strip_html(st.get("content") or "")
        attachments = st.get("media_attachments") or []

        def _reply(key_suffix, text):
            return reply_once(self.cfg, self.cache, f"{key_suffix}:{status_id}", str(status_id), text)

        # 1. Media
        if not has_image(attachments):
             _reply("no_image", "🤖 ⚠️ Missing photo\n\nPlease repost with ONE photo image.\n\nFCK RACISM. ✊ ALERTA ALERTA.")
             return

        # 2. Mention check is now done at the very top.

        
        # 3. Location
        coords, q = parse_location(content)
        
        lat, lon = 0.0, 0.0
        method = "none"
        acc = ACC_FALLBACK
        loc_text = ""
        
        if coords:
            lat, lon = coords
            method = "gps"
            acc = ACC_GPS
            loc_text = f"{lat}, {lon}"
        elif q:
            # Geocode
            # Check cache
            if q in self.cache:
                c = self.cache[q]
                lat, lon = c["lat"], c["lon"]
                method = c.get("method", "cache")
            else:
                user_agent = self.cfg.get("user_agent", "HeatmapBot")
                c_res, c_meth = geocode_query_worldwide(q, user_agent)
                if c_res:
                    lat, lon = c_res
                    method = c_meth
                    # Update cache
                    self.cache[q] = {"lat": lat, "lon": lon, "method": method, "ts": int(time.time())}
                else:
                    # Fail
                    pass

        if not lat and not lon:
            # NEEDS INFO
            item = self._create_pending_item(st, item_id, tag, "NEEDS_INFO", "missing_location")
            self.pending.append(item)
            _reply("needs", build_needs_info_reply(q or ""))
            return

        # Snap
        lat, lon, note = snap_to_public_way(lat, lon, self.cfg.get("user_agent", "Bot"))
        if note:
            method += f"+{note}"

        # Create Pending
        item = self._create_pending_item(st, item_id, tag, "PENDING", None)
        item["lat"] = lat
        item["lon"] = lon
        item["geocode_method"] = method
        item["location_text"] = loc_text or q or ""
        item["accuracy_m"] = int(acc)
        
        self.pending.append(item)
        _reply("pending", build_reply_pending())

    def _handle_update_reply(self, st: Dict[str, Any], tag: str) -> bool:
        """
        Handle a reply that signals an update (report_again or sticker_removed).
        Returns True if we should stop processing handling this status (it was consumed).
        """
        parent_id = st.get("in_reply_to_id")
        if not parent_id: return False
        
        # Determine action
        is_removed = "removed" in tag

        # SECURITY CHECK: Only allow trusted accounts
        trusted = load_trusted_accounts()
        account = st.get("account", {})
        acct = str(account.get("acct") or account.get("username") or "").strip().lower()
        
        # Logic matches is_approved_by_fav: check full handle or base handle
        base = acct.split("@")[0]
        is_trusted = (base in trusted) or (acct in trusted)
        
        if not is_trusted:
             log_line(f"SECURITY | Unauthorized update attempt by {acct} on {parent_id}", "WARN")
             return True # Consume it (don't treat as new report) but do nothing.
        
        # Try to find target feature in reports
        target_item_id = f"masto-{parent_id}"
        found_feat = None
        
        for f in self.reports.get("features", []):
            if f.get("properties", {}).get("item_id") == target_item_id:
                found_feat = f
                break
                
        if not found_feat:
            # Parent not found in reports.
            # It might be an old report or not yet processed.
            # We ignore it for now (silent).
            log_line(f"UPDATE IGNORED | {tag} reply to {parent_id} - parent not in reports", "WARN")
            return True # Consume it, don't treat as new report
            
        # Update logic
        p = found_feat["properties"]
        created_at = st.get("created_at") or ""
        iso_date = created_at[:10] if len(created_at) >= 10 else "2026-01-01"
        
        reply_text = ""

        if is_removed:
            # #sticker_removed = Mark as removed
            if p.get("status") != "removed":
                p["status"] = "removed"
                p["removed_at"] = iso_date
                p["last_seen"] = iso_date # Update last verified date too?
                log_line(f"UPDATE | {target_item_id} marked REMOVED by {st['id']}")
                reply_text = build_reply_removed_confirmation()
            else:
                 log_line(f"UPDATE | {target_item_id} already REMOVED")
                 reply_text = build_reply_removed_confirmation()
        else:
            # #report_again = Affirm presence
            p["status"] = "present"
            p["removed_at"] = None
            p["last_seen"] = iso_date
            
            # Increment seen count? Not currently tracked in schema explicitly but dedup uses it
            p["seen_count"] = int(p.get("seen_count", 1)) + 1
            
            log_line(f"UPDATE | {target_item_id} CONFIRMED present by {st['id']}")
            reply_text = build_reply_confirmed_confirmation()
            
        # We modified 'reports' in place. Typically the main loop saves it.
        
        # Send Confirmation Reply
        if reply_text:
             # cache key: "update_confirm:{this_status_id}" to avoid double replies
             reply_key = f"update_confirm:{st['id']}"
             reply_once(self.cfg, self.cache, reply_key, str(st['id']), reply_text)

        return True

    def _create_pending_item(self, st, item_id, tag, status, error):
        event = "removed" if "removed" in tag else "present"
        content = strip_html(st.get("content") or "")  # Store stripped content for later parsing
        return {
            "id": item_id,
            "status_id": str(st.get("id")),
            "status": status, # PENDING or NEEDS_INFO
            "event": event,
            "tag": tag,
            "source": st.get("url"),
            "created_at": st.get("created_at"),
            "created_date": st.get("created_at")[:10] if st.get("created_at") else None,
            "error": error,
            "content": content,  # Added for type parsing during publication
            "media": [a.get("url") for a in st.get("media_attachments", []) if a.get("type") == "image"]
        }

    def _process_pending(self):
        active_pending = []
        
        # Load trusted accounts from secrets
        trusted = load_trusted_accounts()
        
        for item in self.pending:
            if item["status"] != "PENDING":
                active_pending.append(item)
                continue
                
            sid = item["status_id"]
            if is_approved_by_fav(self.cfg, sid, trusted):
                self._publish_item(item)
            else:
                active_pending.append(item)
        
        self.pending = active_pending

    def _publish_item(self, item):
        """Publish an item to reports.geojson with full type parsing and entity enrichment."""
        # Parse sticker type and medium from the original post content
        content = item.get("content", "")
        medium, sticker_type, parse_err = parse_type_and_medium(content)
        
        # Extract note if present
        note = parse_note(content)
        
        # Match entity from sticker_type
        entity_key, entity_display = self.entity_registry.match_entity_from_type(sticker_type)
        
        # Create Feature with enriched metadata
        feat = {
            "type": "Feature",
            "properties": {
                "item_id": item["id"],
                "status": "present" if item["event"] == "present" else "removed",
                "sticker_type": sticker_type,  # Actual parsed type, not "unknown"
                "medium": medium.value if medium else "sticker",  # "sticker" or "graffiti"
                "created_date": item.get("created_date"),
                "media": item.get("media"),
                "url": item.get("source"),
                "notes": item.get("notes"),
                "radius_m": item.get("accuracy_m", 50)
            },
            "geometry": {
                "type": "Point",
                "coordinates": [item["lon"], item["lat"]]
            }
        }
        
        # Add entity enrichment if matched
        if entity_key:
            feat["properties"]["entity_key"] = entity_key
            feat["properties"]["entity_display"] = entity_display
        
        # Add note if present
        # Add note if present - Already added in properties above but keeping for backward compat if needed or remove
        if note:
             # Ensure the note from parse_note overwrites if existing was empty
             if not feat["properties"].get("notes"):
                 feat["properties"]["notes"] = note
        
        # Add parse error if detected
        if parse_err:
            feat["properties"]["parse_error"] = parse_err
        
        # Attempt deduplication
        merged, dirty = attempt_dedup(feat, self.reports)
        
        if not merged:
            self.reports["features"].append(feat)
            self.reports_ids.add(item["id"])
        
        # We don't save here, main loop saves
