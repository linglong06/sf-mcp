# M0 write infrastructure

M0 adds a generic Python client helper, `sf_mcp.client.make_odata_mutation`, for
POST, PUT, PATCH, MERGE, and DELETE. No EC write tools ship in M0: foundation-object
upsert and Emp* writes remain M1+ work. Existing GET tools are unchanged.

- `SF_ALLOW_WRITE` defaults to `false`. Set `SF_ALLOW_WRITE=true` in the server
  environment to opt in to real writes (only `true`, case-insensitive, enables it).
- `dry_run=True` is the default. It returns a dictionary with the method, URL,
  endpoint, payload, query parameters, and flag state without contacting SF or
  requiring credentials, even when writes are disabled.
- A real write requires both `SF_ALLOW_WRITE=true` and `dry_run=False`, plus the
  same connection and credential arguments as the read client. Disabled writes
  return an error before HTTP. M1 callers can reuse `check_write_allowed(dry_run)`;
  it returns the flag state or raises `PermissionError` for a disabled real write.
- There is **no environment whitelist**: production, preview, and sales-demo
  environments follow the same write flag and dry-run rules.

Real requests reuse the session and rate limiter. Mutation audits record method,
endpoint, dry-run state, write flag, and outcome without credentials or payloads.
Successful HTTP 2xx writes clear the instance cache on a best-effort basis. JSON
responses are returned directly; empty successes return a success/status dict.
Entity-specific verb and payload semantics remain the caller's responsibility.
