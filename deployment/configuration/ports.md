# D5 port allocation (verified free on 2026-11-12 before allocation)

| Port | Bind | Component |
|---|---|---|
| 7614 | * (HTTPS) | ACE IS_D5_EDGE admin REST |
| 7840 | * | ACE IS_D5_EDGE HTTP listener (/d5/v1/*) |
| 8500 | 127.0.0.1 | Central office API + dashboard |
| 8501 | 127.0.0.1 | Edge agent VESSEL-01 (onboard console API) |
| 8502 | 127.0.0.1 | Edge agent VESSEL-02 |
| 8510 | 127.0.0.1 | Satellite simulator control API |
| 8511 | 127.0.0.1 | Satellite relay VESSEL-01 -> shore |
| 8512 | 127.0.0.1 | Satellite relay VESSEL-02 -> shore |

Ports already used by other POCs and left untouched: 4414, 7610-7613, 7800, 7810, 7830, 8098, 8099, 9000, 9001.
firewalld was not changed. 7614/7840 listen on all interfaces (ACE default) but the firewall only admits ssh/cockpit/3389.
