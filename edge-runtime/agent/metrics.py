"""Resource reporting. Distinguishes: configured limits (read from the process's cgroup v2, i.e. what systemd
actually enforces), measured usage (psutil / cgroup counters), and the business targets from edge.json."""
import os, time

try:
    import psutil
except ImportError:  # pragma: no cover
    psutil = None


def _cgroup_dir():
    try:
        with open("/proc/self/cgroup") as f:
            for line in f:
                if line.startswith("0::"):
                    return "/sys/fs/cgroup" + line.strip()[3:]
    except OSError:
        pass
    return None


def _read(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def dir_size(path):
    total = 0
    for root, _, files in os.walk(path):
        for n in files:
            try:
                total += os.path.getsize(os.path.join(root, n))
            except OSError:
                pass
    return total


class Metrics:
    def __init__(self, data_dir, targets):
        self.data_dir, self.targets = data_dir, targets
        self.proc = psutil.Process() if psutil else None
        if self.proc:
            self.proc.cpu_percent(None)
        self.started = time.time()

    def limits(self):
        cg = _cgroup_dir()
        out = {"cgroup": cg, "cpuVcpu": None, "memoryMB": None, "source": "none (not running under a limited cgroup)"}
        if cg:
            cpu = _read(os.path.join(cg, "cpu.max"))
            mem = _read(os.path.join(cg, "memory.max"))
            if cpu and not cpu.startswith("max"):
                q, p = cpu.split()
                out["cpuVcpu"] = round(int(q) / int(p), 2)
            if mem and mem != "max":
                out["memoryMB"] = int(mem) // (1024 * 1024)
            if out["cpuVcpu"] or out["memoryMB"]:
                out["source"] = "cgroup v2 (systemd CPUQuota / MemoryMax)"
        return out

    def snapshot(self):
        m = {"measuredAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "uptimeSec": int(time.time() - self.started)}
        if self.proc:
            with self.proc.oneshot():
                m["cpuPercent"] = round(self.proc.cpu_percent(None), 1)  # % of one vCPU
                m["rssMB"] = round(self.proc.memory_info().rss / 1048576, 1)
                m["threads"] = self.proc.num_threads()
        cg = _cgroup_dir()
        cur = _read(os.path.join(cg, "memory.current")) if cg else None
        if cur:
            m["cgroupMemoryMB"] = round(int(cur) / 1048576, 1)
        m["diskUsedMB"] = round(dir_size(self.data_dir) / 1048576, 2)
        return {"measured": m, "configuredLimits": self.limits(), "targets": self.targets,
                "estimatedProduction": {"cpuVcpu": 1, "memoryMB": 512, "diskMB": 2048,
                                        "note": "estimate only - not measured on vessel hardware"}}
