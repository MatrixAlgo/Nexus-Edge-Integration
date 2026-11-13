# D5 – Environment Discovery (Phase 1)

Captured 2026-11-12, read-only inspection. No system changes were made.

## Host
| Item | Found |
|---|---|
| OS | RHEL 9.8 (Plow), kernel 5.14.0-687.30.1.el9_8 |
| CPU / RAM | 8 vCPU / 30 GiB (≈15 GiB available), no swap |
| Disk | `/` 99 GB, 59 GB free (single filesystem for /var, /root, /home) |
| SELinux / firewalld | Enforcing / active (open: ssh, cockpit, 3389/tcp) |
| Python | 3.9.25, SQLite 3.34.1, psutil 5.8.0. **No pip, no FastAPI, no pymqi, no gcc** |
| Containers | Podman 5.8.2 (no images); no Docker |
| Network tools | `tc`, `ip` present (not required) |
| Internet | pypi.org reachable (HTTP 200); RHEL RHUI repos configured |

## IBM MQ
- 10.0.1.0 CD, Developer licence, `/opt/mqm`, data `/var/mqm`, Installation1 (primary)
- Queue managers:

| QM | Status | Used by |
|---|---|---|
| TESTMQ | **Ended unexpectedly** | (not touched) |
| TESTMQ2 | Running | IS_AMOS, IS_INTEG (D2) |
| ZMIQM1 | Running | IS_MFT (D4) — not in the brief |
| MFTQM | **Ended unexpectedly** | (not touched) |

- No MQ TCP listeners are running. Existing ACE servers use local bindings.
- The mqweb console is configured but not listening.
- Client libraries and `com.ibm.mq.allclient.jar` are present.

## IBM ACE
- 13.0.9.0 (evaluation tarball) at `/root/IBM/ACE/ace-13.0.9.0`. The `ibmint`, `mqsicreateworkdir` and `IntegrationServer` commands are present.
- Toolkit is present (`tools/eclipse`, `mqsicreatebar`).
- Runtime user is `aceadmin` (groups mqbrkrs and mqm).
- Integration nodes:
  - **TEST** is running (admin 4414, HTTP listener 7800). It has one server, `D6`.
  - **TEST2** is not running.
- Standalone integration servers (the pattern the earlier POCs use):

| Server | Admin | HTTP | QM | Workdir |
|---|---|---|---|---|
| IS_RD365 | 7610 | 9000 | – | ~aceadmin/ZMI_ADNOC_D2/runtime |
| IS_AMOS | 7611 | 7810 | TESTMQ2 | ~aceadmin/ZMI_ADNOC_D2/runtime |
| IS_INTEG | 7612 | 7820* | TESTMQ2 | ~aceadmin/ZMI_ADNOC_D2/runtime |
| IS_MFT | 7613 | 7830 | ZMIQM1 | ~aceadmin/ZMI_ADNOC_D4/runtime |

  \*7820 is not currently listening.
- **IS_ZMI_POC is not running anywhere.** It exists only as a config file at `~aceadmin/ZMI-ADNOC-POC/D2-PO-Integration/ace-app/server-config/`.

## Ports in use (relevant)
22, 631, 2222-2223, 3002-3005, 3306/33060 (MySQL), 3389, 4414, 7610-7613, 7800, 7810, 7830, 7891, 8098-8099, 9000-9001, 11883.
Ports that look free for D5: 7614 (ACE admin), 7840 (ACE HTTP), 8500-8510 (Python services and dashboard).

## Workspace
- `~/ZMI-ADNOC-POC` does **not** exist for root.
- It exists as **`/home/aceadmin/ZMI-ADNOC-POC`** (D2-PO-Integration, documentation).
- Other POCs: `/home/aceadmin/ZMI_ADNOC_D2`, `/home/aceadmin/ZMI_ADNOC_D4`.
- The current session directory `/root/ZMID5POC` is empty.

## Deviations from expected environment
1. The workspace is `/home/aceadmin/ZMI-ADNOC-POC`, not `/root/ZMI-ADNOC-POC`.
2. IS_ZMI_POC is not running. The earlier POCs use standalone servers rather than node-managed ones.
3. Two additional queue managers exist (ZMIQM1, MFTQM). TESTMQ and MFTQM are in the *Ended unexpectedly* state. This was left as is.
4. There is no pip and no FastAPI.

## Blockers / decisions needed before changing IBM resources
1. **Create queue manager D5QM.** This needs approval because it is a new IBM resource created as mqm or root. It does not touch any existing queue manager.
2. **Create standalone integration server IS_D5_EDGE.** It would run as aceadmin with its own workdir, admin port 7614, HTTP port 7840 and QM D5QM. It does not touch node TEST/TEST2 or the existing servers.
3. **Workspace location.** Options are `/home/aceadmin/ZMI-ADNOC-POC/D5-Edge-Demo` (next to D2, readable by aceadmin) or `/root/ZMID5POC`.
4. **Python framework.** The default is the standard library only (`http.server`, `sqlite3`, `psutil`), so there is no package install. FastAPI would need pip (a system or user package install).
