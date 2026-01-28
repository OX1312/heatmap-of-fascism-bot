from typing import List, Dict, Any, Optional
import time

from .models import PipelineResult
from .constants import ACC_FALLBACK, ACC_GPS
from ..adapters.mastodon_api import (
    fetch_timeline, reply_once, is_approved_by_fav
)
from ..domain.parse_post import (
    strip_html, parse_location, has_image
)
from ..domain.location import geocode_query_worldwide, snap_to_public_way
from ..domain.dedup import attempt_dedup
from ..utils.log import log_line
from ..support.support_replies import build_reply_missing, build_reply_pending, build_needs_info_reply

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

    def run_cycle(self):
        """Run one full cycle."""
        # 1. Ingest
        self._ingest_timeline()
        # 2. Process
        self._process_pending()

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

        # 2. Mention (Placeholder: minimal check)
        required = self.cfg.get("required_mentions") or ["HeatmapofFascism"]
        has_mention = any(m.strip().lstrip("@") in content for m in required)
        # also check mentions list
        if not has_mention:
             mentions = st.get("mentions") or []
             for m in mentions:
                 acct = m.get("acct") or m.get("username")
                 if acct and any(req.lower().strip().lstrip("@") in acct.lower() for req in required):
                     has_mention = True
                     break
        
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

    def _create_pending_item(self, st, item_id, tag, status, error):
        event = "removed" if "removed" in tag else "present"
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
            "media": [a.get("url") for a in st.get("media_attachments", []) if a.get("type") == "image"]
        }

    def _process_pending(self):
        active_pending = []
        trusted = set() # TODO load trusted
        
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
        # Create Feature
        feat = {
            "type": "Feature",
            "properties": {
                "item_id": item["id"],
                "status": "present" if item["event"] == "present" else "removed",
                "sticker_type": "unknown", # TODO parse type
                "created_date": item.get("created_date"),
                "media": item.get("media"),
                "radius_m": item.get("accuracy_m", 50)
            },
            "geometry": {
                "type": "Point",
                "coordinates": [item["lon"], item["lat"]]
            }
        }
        
        merged, dirty = attempt_dedup(feat, self.reports)
        
        if not merged:
            self.reports["features"].append(feat)
            self.reports_ids.add(item["id"])
        
        # We don't save here, main loop saves
