import subprocess
import pathlib
from typing import Dict, Any, Tuple
from ..utils.log import log_line

# We assume LOG_ROOT is available or we pass cwd explicitely. 
# For now, we'll accept cwd or assume a global ROOT if we were in bot.py, 
# but better to pass it in.

def auto_git_push_reports(cfg: Dict[str, Any], root_dir: pathlib.Path, relpath: str = "reports.geojson", reason: str = "dirty") -> bool:
    """
    Commit and push a single file. (No pull, no rebase).
    Returns True if pushed (or clean), False on error.
    """
    if not cfg.get("auto_push_reports", True):
        return True

    remote = str(cfg.get("git_remote", "origin") or "origin")
    branch = str(cfg.get("git_branch", "main") or "main")
    
    # We execute git commands in the root_dir
    def run_git(args) -> Tuple[int, str]:
        try:
            r = subprocess.run(
                ["git"] + args,
                cwd=str(root_dir),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=30,
            )
            return r.returncode, r.stdout.strip()
        except Exception as e:
            return -1, str(e)

    # 1. Check status
    rc, out = run_git(["status", "--porcelain", "--", relpath])
    if rc != 0:
        log_line(f"ERROR | auto_push | git_status rc {rc} | out {out!r}")
        return False
    
    if not out.strip():
        # Clean
        return True

    # 2. Add
    rc, out = run_git(["add", "--", relpath])
    if rc != 0:
        log_line(f"ERROR | auto_push | git_add rc {rc} | out {out!r}")
        return False

    # 3. Commit
    msg = f"Update data ({reason})"
    rc, out = run_git(["commit", "-m", msg])
    if rc != 0:
        log_line(f"ERROR | auto_push | git_commit rc {rc} | out {out!r}")
        return False

    # 4. Push
    # CAUTION: This might hang if auth is needed and not configured in keychain/ssh-agent
    rc, out = run_git(["push", remote, f"HEAD:{branch}"])
    if rc != 0:
         log_line(f"ERROR | auto_push | git_push rc {rc} | out {out!r}")
         return False

    log_line(f"GIT PUSH OK | reason={reason}")
    return True
