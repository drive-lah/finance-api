"""Default-approver resolution — ONE mechanism for every approval rail (Gaurav 2026-09-15).

Until the approval matrix (finance_coa_config) names an approver for a COA, every approval
task is assigned to a single named human from the DEFAULT CHAIN, skipping the raiser:

    DEFAULT_APPROVER_CHAIN = "zilla@drivelah.sg,dirkjan@drivelah.sg,gauravs@drivelah.sg"

So: anyone raises → Zilla approves. ZILLA raises → Dirk-Jan approves. This answers the
"who approves the approver's own request" question deterministically instead of leaving her
own raise stuck behind maker-checker in her own queue. The matrix, once populated, always
outranks this (rails consult it first); this module is only the fallback rung.

Used by: incident payouts (host/guest), invoice approvals, employee claims. Maker-checker
stays absolute everywhere regardless of what this returns.
"""
import os
import logging

logger = logging.getLogger(__name__)

_DEFAULT_CHAIN = "zilla@drivelah.sg,dirkjan@drivelah.sg,gauravs@drivelah.sg"


def _chain() -> list[str]:
    raw = os.environ.get("DEFAULT_APPROVER_CHAIN", _DEFAULT_CHAIN)
    return [e.strip().lower() for e in raw.split(",") if e.strip()]


def default_assignee(db, raiser=None):
    """First user in the default chain who is NOT the raiser. `raiser` may be a user id
    (str/int) or an email; None skips the check. Returns the user's id, or None when no
    chain member resolves (task then rides the role queue)."""
    from src.models.user import User
    raiser_s = str(raiser).strip().lower() if raiser is not None else None
    for email in _chain():
        u = db.query(User).filter(User.email.ilike(email)).first()
        if not u:
            continue
        if raiser_s and raiser_s in (str(u.id).lower(), (u.email or "").lower()):
            continue   # never assign someone their own request
        return u.id
    logger.warning("default approver chain resolved nobody (chain=%s)", _chain())
    return None
