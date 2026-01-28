from typing import List, Optional

def validate_post_content(
    text: str, 
    attachments: List[dict],
    required_mentions: List[str]
) -> Optional[str]:
    """
    Returns None if valid, or reason string if invalid (ignored).
    """
    # Check mentions
    import re
    has_mention = False
    for m in (required_mentions or []):
         base = str(m).strip().lstrip("@").split("@")[0]
         if not base:
             continue
         if re.search(rf"(?i)(?:^|\s)@{re.escape(base)}(?:@[-\w\.]+)?\b", text):
             has_mention = True
             break
    
    if required_mentions and not has_mention:
        return "missing_mention"
        
    return None
