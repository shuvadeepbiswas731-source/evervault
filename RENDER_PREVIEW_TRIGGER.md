# Render Preview Trigger

This is a temporary, documentation-only file created to trigger a Render PR preview for testing the already-implemented Evervault reverse WebSocket tunnel.

## Purpose

The Evervault reverse WebSocket tunnel implementation is complete and tested locally:

- 13 tunnel protocol tests pass
- 7 safety patch tests pass
- Python syntax checks pass
- Git diff checks pass

This file exists solely to create a visible change in a PR so that Render's "Manual" PR preview mode can be triggered. It does not modify any application code, configuration, or behavior.

## What This Does Not Change

- No application logic modified
- No environment variables changed
- No database migrations added
- No secrets exposed
- No production configuration affected

## Tunnel Implementation Status

The tunnel is already implemented in the codebase at commits:
- `738948a` - Isolate PR preview configuration and allow separate preview tunnel token
- `7216a8c` - Preserve Google Drive fallback when tunnel is unavailable
- `296a6dc` - Add safe preview tunnel roundtrip test endpoint

These changes are already merged into `main` (commit `ef3be17`). This PR merely allows a dedicated preview instance to be created for end-to-end tunnel testing with an isolated Windows storage agent.

## Next Steps

1. Trigger the Render preview manually via the Render dashboard
2. Configure preview environment variables:
   - `IS_PULL_REQUEST=true`
   - `EVERVAULT_PREVIEW_TUNNEL_TOKEN=<test-token>`
   - `EVERVAULT_PREVIEW_TUNNEL_ENABLED=1`
   - `EVERVAULT_PREVIEW_SECRET=<test-secret>`
   - `EVERVAULT_PREVIEW_DATABASE_URL=` (leave empty for isolated SQLite)
3. Launch isolated Windows storage agent with dedicated token and media directory
4. Launch Windows tunnel client pointing to preview WSS URL
5. Verify end-to-end tunnel connectivity and file transfer with SHA-256 verification

---

**This file should be removed after the preview test completes.**