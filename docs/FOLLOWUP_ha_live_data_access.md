# Follow-up: live Home Assistant entity-data access for analysis

**Status:** decided, not yet implemented — blocked on HA LAN IP + host allowlist.
**Goal:** let the assistant pull live HA entity **state history + long-term
statistics** so it can feed `docs/analysis/seed_thresholds.py` /
`rain_correlation.py` directly, replacing the manual *Developer Tools → History →
Download Data* CSV export.

## Decision

Use a **community MCP server — [`voska/hass-mcp`](https://github.com/voska/hass-mcp)** —
run as a local **stdio** process in the sandbox via `uv`, authenticated with a
**long-lived token minted from a dedicated, entity-scoped HA user**.

### Why not the official `mcp_server` integration

Evaluated and rejected for this use ([docs](https://www.home-assistant.io/integrations/mcp_server/)):
- Routes **everything through the Assist API** → only current state of exposed
  entities + control + a live-context snapshot. **No recorder history, no
  long-term statistics** — which is exactly what the analysis pipeline needs.
- Does **not** avoid a token for a headless CLI agent: `/api/mcp` needs an
  `Authorization: Bearer <long-lived token>`. (OAuth 2.0 exists but is for
  browser-based clients.)
- It's only the right tool if we later want to *control* devices via Assist.

### Why voska/hass-mcp over homeassistant-ai/ha-mcp

- Lean tool set that maps 1:1 to our needs: `get_history_range`,
  `get_statistics_range` (explicit ISO ranges), `get_history`, `get_statistics`.
- Runs as a local stdio server here — **no changes needed inside HA** (no HACS
  custom component). `ha-mcp` (87 tools, HACS component, remote HTTP) is the
  alternative if we ever want a token-free in-HA endpoint instead.

## Token risk mitigation

HA long-lived tokens aren't per-entity scopeable, so:
- Create a **dedicated HA user**, expose **only the pump/flood entities** to it,
  mint the long-lived token as that user → token's blast radius = pump entities.
- Register the MCP server at **`--scope user`** (`~/.claude.json`) so the token
  **never lands in the git repo**. Optionally keep it in
  `/etc/sandbox-persistent.sh` as `HA_TOKEN` so it stays out of chat transcripts.

## Blocker / open items before wiring up

1. **HA is not reachable by mDNS name.** `homeassistant.local` does **not**
   resolve from the sandbox (it's link-local mDNS; the sandbox isn't on the LAN).
   → Need HA's **LAN IP** (e.g. `http://192.168.1.x:8123`), not the `.local` name.
2. **Allowlist from the host** (both required):
   ```bash
   sbx policy allow network 192.168.1.x:8123          # HA instance
   sbx policy allow network pypi.org,files.pythonhosted.org   # to fetch hass-mcp
   ```
3. Verify the sandbox can actually TCP-connect to `:8123` before wiring anything;
   if not, the sandbox has no LAN route and we fall back (below).

## Setup steps (once IP + allowlist are done)

```bash
# token/url kept out of chat + repo:
echo 'export HA_URL=http://192.168.1.x:8123' >> /etc/sandbox-persistent.sh
echo 'export HA_TOKEN=<scoped-user-long-lived-token>' >> /etc/sandbox-persistent.sh

claude mcp add hass-mcp --scope user \
  -e HA_URL="$HA_URL" -e HA_TOKEN="$HA_TOKEN" \
  -- uv tool run hass-mcp

claude mcp get hass-mcp            # verify it connects
# smoke test: get_history_range on
#   binary_sensor.circuitsetup_energy_meter_f3a598_sump_pump_running
```

Then adapt `seed_thresholds.py` / `rain_correlation.py` to consume MCP history
output directly instead of a committed CSV.

## Fallback if the sandbox can't reach the LAN

- Run the MCP server (or a small REST/WS fetch script) **on the host** instead, or
- Stay on the current **offline model**: export CSV/JSON into `docs/analysis/`
  and analyze it here. (This is what's in place today and works fine.)

## Environment facts confirmed (2026-07-10)

- Sandbox has `uv`, `docker`, and `claude mcp add` available.
- `homeassistant.local` does not resolve here (verified via getent / getaddrinfo).
