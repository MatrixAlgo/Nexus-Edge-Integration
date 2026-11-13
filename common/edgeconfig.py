"""Edge runtime configuration contract - shared by the central publisher and the edge validator."""
import copy

DEFAULT_CONFIG = {
    "syncEnabled": True,
    "dispatchIntervalSec": 1.0,
    "ackPollIntervalSec": 1.0,
    "ackTimeoutSec": 20,
    "retryBaseSec": 2,
    "retryMaxSec": 30,
    "heartbeatSec": 4,
    "requestTimeoutSec": 8,
    "priorityRules": {
        "EMERGENCY_MAINTENANCE": "CRITICAL",
        "PR": "NORMAL",
        "GRN": "NORMAL",
        "MAINTENANCE": "LOW",
    },
}

LIMITS = {  # key: (type, min, max)
    "dispatchIntervalSec": (float, 0.1, 60), "ackPollIntervalSec": (float, 0.2, 60), "ackTimeoutSec": (float, 5, 3600),
    "retryBaseSec": (float, 0.5, 300), "retryMaxSec": (float, 1, 3600), "heartbeatSec": (float, 1, 300),
    "requestTimeoutSec": (float, 1, 120),
}


def merged_with_defaults(cfg):
    out = copy.deepcopy(DEFAULT_CONFIG)
    for k, v in (cfg or {}).items():
        if k == "priorityRules" and isinstance(v, dict):
            out["priorityRules"].update(v)
        else:
            out[k] = v
    return out


def validate_config(cfg):
    errs = []
    if not isinstance(cfg, dict):
        return ["configuration must be an object"]
    unknown = set(cfg) - set(DEFAULT_CONFIG)
    if unknown:
        errs.append("unknown keys: %s" % ", ".join(sorted(unknown)))
    if "syncEnabled" in cfg and not isinstance(cfg["syncEnabled"], bool):
        errs.append("syncEnabled must be true/false")
    for k, (_t, lo, hi) in LIMITS.items():
        if k in cfg:
            v = cfg[k]
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not lo <= v <= hi:
                errs.append("%s must be a number between %s and %s" % (k, lo, hi))
    pr = cfg.get("priorityRules", {})
    if not isinstance(pr, dict):
        errs.append("priorityRules must be an object")
    else:
        for rt, p in pr.items():
            if rt not in DEFAULT_CONFIG["priorityRules"]:
                errs.append("priorityRules: unknown record type %s" % rt)
            if p not in ("CRITICAL", "NORMAL", "LOW"):
                errs.append("priorityRules.%s must be CRITICAL, NORMAL or LOW" % rt)
    m = merged_with_defaults(cfg) if not errs else None
    if m and m["retryMaxSec"] < m["retryBaseSec"]:
        errs.append("retryMaxSec must be >= retryBaseSec")
    return errs
