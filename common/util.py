import datetime, json, logging, os, sys, uuid

PRIORITY_RANK = {"CRITICAL": 0, "NORMAL": 1, "LOW": 2}
MQ_PRIORITY = {"CRITICAL": 9, "NORMAL": 5, "LOW": 1}
RECORD_TYPES = ("PR", "GRN", "MAINTENANCE", "EMERGENCY_MAINTENANCE")
D5_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_iso(s):
    if not s:
        return None
    return datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))


def seconds_since(s):
    t = parse_iso(s)
    return None if t is None else (datetime.datetime.now(datetime.timezone.utc) - t).total_seconds()


def new_id():
    return str(uuid.uuid4())


def dumps(o):
    return json.dumps(o, separators=(",", ":"), sort_keys=True, default=str)


def loads(s, default=None):
    if s is None or s == "":
        return default
    return json.loads(s)


class JsonFormatter(logging.Formatter):
    def format(self, r):
        d = {"ts": now_iso(), "level": r.levelname, "logger": r.name, "msg": r.getMessage()}
        extra = getattr(r, "ctx", None)
        if extra:
            d.update(extra)
        if r.exc_info:
            d["exc"] = self.formatException(r.exc_info)
        return json.dumps(d, default=str)


def setup_logging(service, log_dir=None):
    log_dir = log_dir or os.path.join(D5_ROOT, "logs")
    os.makedirs(log_dir, exist_ok=True)
    root = logging.getLogger()
    root.handlers[:] = []
    root.setLevel(logging.INFO)
    fh = logging.FileHandler(os.path.join(log_dir, service + ".log"))
    fh.setFormatter(JsonFormatter())
    root.addHandler(fh)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(JsonFormatter())
    root.addHandler(sh)
    return logging.getLogger(service)


def log(logger, msg, level=logging.INFO, **ctx):
    logger.log(level, msg, extra={"ctx": ctx})
