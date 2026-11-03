# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- M2 Background writes: one `upsert_background_element` tool for 24 profile portlet
  types, with metadata key rules, default dry-run, shared upsert controls, and tests.

- M2 expanded scope: 14 upsert tools for compensation, Position, work permits,
  termination, higher duty, User, time off, RBP/groups, and country-specific
  GlobalInfo. Reuses M0 gating and dry-run previews; adds explicit country lists,
  shared aliases, high-impact write guidance, and mocked regression tests.

- M2.1/M2.2: nine EC employee/person upsert tools with shared key/alias handling,
  default dry-run previews, M0 write gating, mocked tests, and
  `docs/M2_EC_EMPLOYEE_WRITE.md`.

- M0 write infrastructure: disabled-by-default `SF_ALLOW_WRITE`, reusable write guard,
  generic OData mutations with dry-run previews, mutation auditing, and cache invalidation.
  No environment whitelist is imposed.
- M1 EC configuration tools: `upsert_foundation_object` and `upsert_picklist_value`,
  with default dry-run previews, M0 write gating, shared foundation mappings,
  localized picklist fields, mocked tests, and endpoint behavior documented in
  `docs/M1_EC_CONFIG_WRITE.md`.

### Changed

- M1 foundation-object and picklist-value writes now POST to SAP
  `/odata/v2/upsert?purgeType=incremental` with a one-entity JSON array.

## [0.3.2] - 2026-03-02

### Fixed

- **compare_configurations**: Removed broken `@sf_tool` decorator; added inline validation for dual-instance parameters
- **search_employees / compliance**: Wrapped OR clauses in parentheses so AND joins evaluate correctly in OData filters
- **get_role_assignment_history**: Replaced invalid `RBPBasicUserPermission` entity with `getUserPermissions` + `RBPRole` two-step approach
- **get_anniversary_employees**: Iterate all years in date range instead of only `from_date.year`, fixing cross-year-boundary queries
- **Decorator validation**: Skip validation for empty string defaults to avoid false positives on optional parameters

### Added

- **Connection pooling**: `requests.Session` singleton for HTTP connection reuse across API calls
- **Cache mutation safety**: Deep-copy on both cache `put()` and `get()` to prevent callers from corrupting cached data
- **MDF/foundation input validation**: `query_mdf_object` validates object_name, filter, select, orderby, effective_date; `get_foundation_objects` validates filter

### Changed

- **dependencies.py**: Import `Dependency` from `fastmcp.server.dependencies` instead of `docket.dependencies`
- **_display_name**: Extracted duplicated helper to shared `sf_mcp/tools/utils.py`, imported by 4 tool modules

## [0.3.1] - 2026-03-02

### Fixed

- **Timing-safe auth**: API key comparison uses `hmac.compare_digest` to prevent timing-based side-channel attacks
- **OData filter hardening**: Blocklist now checks raw, URL-decoded, and double-decoded input; rejects control characters
- **UTC-consistent dates**: `parse_hire_date` uses explicit UTC timezone for all SAP timestamp parsing
- **GCP Secret Manager**: Separated `ImportError` from runtime `Exception`; runtime errors now logged properly

### Changed

- Updated tool count in documentation (32 → 43)
- Version bump to 0.3.1

## [0.3.0] - 2026-02-28

### Added

- **Rate limiting**: Sliding-window algorithm with per-instance tracking, configurable limits, automatic 429 retry
- **Response caching**: Category-based TTLs (metadata 1h, picklists 30m, permissions 1h), SHA-256 cache keys
- **Automatic pagination**: `paginate=True` on `query_odata` fetches all pages automatically
- **3 admin tools**: `get_api_quota_status`, `get_cache_status`, `clear_cache`
- ruff linter and mypy type checker configuration

### Changed

- Major refactoring with ruff code hygiene improvements
- Expanded `.gitignore` for tool caches

## [0.2.0] - 2026-02-25

### Added

- **3 monitoring tools**: `get_alert_notifications`, `get_scheduled_job_status`, `get_integration_center_jobs`
- **3 MDF tools**: `get_mdf_object_definitions`, `query_mdf_object`, `get_foundation_objects`
- **2 workflow tools**: `get_pending_approvals`, `get_workflow_history`
- **3 position tools**: `get_position_details`, `get_vacant_positions`, `get_org_chart`
- **get_user_roles**: Fixed to use `getUserPermissions` + `RBPRole` instead of invalid `RBPBasicUserPermission`

## [0.1.0] - 2026-02-20

### Added

- Initial release with 32 tools across 10 categories
- `sf_tool` decorator for cross-cutting concerns (logging, validation, error handling)
- FastMCP Dependency injection for schema-clean tool interfaces
- 21 SAP data center mappings with alias support
- Per-request authentication (no stored credentials)
- Input validation with 10 regex-based validators
- XXE-safe XML parsing via defusedxml
- Structured JSON audit logging with credential masking
- Dockerfile for Cloud Run deployment
- 110 pytest test cases
