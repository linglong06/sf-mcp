# Workforce SCIM Users: query and explicit account creation

`get_scim_user` reads and `create_scim_user` optionally creates login accounts at
`/rest/iam/scim/v2/Users`. These tools do not orchestrate hires or modify SCIM
Groups, memberships, RBP, or existing accounts.

## Hire sequence and the provisioning decision

SAP's **Adding a New Employee**, OData V2 API Reference Guide §9.4.18.1,
describes the minimum sequence as PerPerson → EmpEmployment → EmpJob →
PerPersonal **plus** SCIM Users account creation. The SCIM step can start after
EmpEmployment. EmpEmployment creates the corresponding OData `User` entity;
a successful `upsert_user` or an existing OData row does **not** establish that
a login account exists. Do not default to upserting OData User first.

SAP's **Managing User Information**, chapter 8 Workforce SCIM overview, also
says that creating user accounts through SCIM is not recommended: SuccessFactors
mainly acts as a source of workforce identities. IAS/IPS synchronization is the
common provisioning path. These statements address different integration
contexts and must not be reduced to a rule that every tenant needs a SCIM POST.

Use explicit SCIM creation only for an API-driven EC hire where EmpEmployment
already created the employment/User row, the tenant needs an explicit login
account, and Workforce SCIM is enabled with the required permissions—typically
when IAS/IPS is not automatically creating the account for that hire path.
Do not use it if IAS/IPS, HRIS sync, or real-time sync already provisions login
accounts. Reverse IPS → SF user creation is unsupported as a usable hire flow.
Do not automatically create accounts after every employment upsert.

Before `dry_run=false`, the caller must confirm the tenant account-provisioning
model, the existing employment, and prerequisites below, then explicitly pass
`provisioning_model_confirmed=true`. This flag records the caller's confirmation;
it does not discover or validate tenant configuration. `SF_ALLOW_WRITE=true` is
also required. A flag alone is not a reason to create an account.

## Prerequisites

- Workforce SCIM must be enabled for the instance and API identity.
- Enable **Case-Insensitive Usernames** as required by SCIM. Check existing
  duplicates and tenant SSO prerequisites before any administrator changes this
  setting. These tools do not change it or apply their own lowercase/case-folding
  rules; exact `eq` lookup semantics and returned spelling belong to SAP.
- RBP **Manage Identity Account and Group → Read Access to SCIM User API** for
  queries, and **Edit Access to SCIM User API** for creation. Creation needs both
  because it performs read-only duplicate checks first.
- **Manage User → Employee Export** and the appropriate employee/onboardee target
  population. Optional work email/phone updates may need additional EC field
  permissions. OData User permissions do not establish SCIM access.
- Confirm IAS/IPS and HRIS/real-time sync behavior for this particular hire path.
  Coordinate with the responsible administrator; these tools neither inspect nor
  change provisioning configuration.

## Query contract

Supply `instance`, `data_center`, `environment`, and credentials using the existing
`auth_user_id` / `auth_password` resolver. Supply exactly one nonempty key:

| Tool argument | SCIM filter |
| --- | --- |
| `user_name` | `userName eq "<value>"` |
| `person_id_external` | `urn:ietf:params:scim:schemas:extension:successfactors:2.0:User:personIdExternal eq "<value>"` |

Both keys, neither key, and blank values are errors. Values use JSON string
escaping (including quotes and backslashes); `requests` URL-encodes query params.
No OData `$filter` is used. The GET requests `Accept: application/scim+json,
application/json`, `startIndex=1`, and `count=2`.

The hard limit is **one requested page of two records per lookup**. This is an
existence/uniqueness check, not an export. It does not follow server pagination
links. `totalResults > 1` or two returned resources means `multiple`, even if only
one resource was returned on the page. Summaries are capped at two. A single
resource requires `totalResults=1` and a resource ID to produce `found`.

| `outcome` | Meaning |
| --- | --- |
| `found` | Exactly one resource, with sanitized `user` summary |
| `not_found` | Empty Resources with total 0 or omitted; total 0 may omit Resources |
| `multiple` | Multiple matches indicated; never silently selects one |
| `incomplete` | Missing/inconsistent metadata, partial singleton page, or invalid JSON; uniqueness/absence unconfirmed |
| `unauthorized` | Missing credentials, HTTP 401, or equivalent SCIM error |
| `forbidden` | HTTP 403 or equivalent SCIM permission error; check RBP/population |
| `rate_limited` / `error` | Request failed; do not infer account absence |
| `invalid_input` | Invalid lookup keys |

A summary permits only `id`, `userName`, `userType`, `active`, `personIdExternal`,
`perPersonUuid`, and emails (`value`, `type`, `primary`); absent fields stay absent.
Passwords, other extension fields, and raw server errors are excluded. A found
SCIM resource is not a live sign-in test. `not_found` reflects the API identity's
visibility at that moment; restricted target population or sync delays can affect
what it sees.

## Create contract and previews

Required arguments map directly to `userName`, `userType`, and explicit boolean
`active`. At least one nonempty `person_id_external` or `per_person_uuid` is also
required. If both are supplied, the payload **omits personIdExternal and uses only
perPersonUuid**, encoding SAP's rule that UUID determines the EC information.
Duplicate checks still inspect both supplied person identifiers.

Every create payload includes these schema URNs:

```text
urn:ietf:params:scim:schemas:core:2.0:User
urn:ietf:params:scim:schemas:extension:successfactors:2.0:User
urn:ietf:params:scim:schemas:extension:sap:2.0:User
urn:ietf:params:scim:schemas:extension:enterprise:2.0:User
```

Optional arguments are `locale`, `timezone`, `external_id`, `emails`,
`phone_numbers`, and `login_method`. They are omitted unless explicitly supplied.
`login_method` and person identifiers go in the SuccessFactors extension;
`external_id` maps to `externalId` and `phone_numbers` to `phoneNumbers` at the
root. Emails/phone numbers accept entries with required string `value`, optional
string `type`, and optional boolean `primary`; other keys are rejected. No
password input or arbitrary identity-field dictionary is accepted. No locale,
timezone, email, phone, login method, external ID, or SAP user UUID is invented.
Tenant-specific field constraints remain SAP's responsibility.

Example payload preview (no credentials needed):

```json
{
  "instance": "example-company",
  "data_center": "DC55",
  "environment": "preview",
  "user_name": "example.employee",
  "user_type": "EMPLOYEE",
  "active": true,
  "person_id_external": "example-person"
}
```

`dry_run=true` is the default. It returns method, full URL, endpoint, payload,
params, write-flag status, and request ID. It performs **no HTTP**, credential
resolution, duplicate checks, or SAP validation. A preview is **payload preview
only**, never “SAP validated.” It also does not validate the employment or the
provisioning model.

For a real create, explicitly supply `dry_run=false`,
`provisioning_model_confirmed=true`, credentials, and enable `SF_ALLOW_WRITE=true`
in the server environment. The existing `check_write_allowed` guard blocks
writes before any preflight HTTP when the flag is off.

## Duplicate checks, failures, and verification

A real create checks `userName` and each supplied person key independently using
exact filters. UUID uses the same SuccessFactors extension path ending in
`:perPersonUuid`. Every lookup must return `not_found`. Any match returns
`conflict` without a POST. A failed/incomplete lookup stops creation and returns
its outcome with `phase=preflight`. Unsupported UUID filtering therefore blocks
the write; it never silently skips the check. At most three GETs and one POST
are made per call, with early exit on a duplicate or inconclusive check.

POST uses `Content-Type: application/scim+json`. `created` contains a sanitized
summary when a success response confirms a resource ID. HTTP 409 returns
`conflict`; the check and POST are not atomic, so this also handles races.
HTTP 401/403 remains an auth/permission error. No POST is automatically retried,
including HTTP 409, HTTP 429, timeout, connection failure, or server errors.
GETs likewise have no automatic transport retry in this implementation.

Timeout/connection failure and HTTP 5xx return `unknown`: **the write may have
succeeded**. An asynchronous/empty/unusable success response is also `unknown`.
Query with `get_scim_user(user_name=...)` (and the external person ID if supplied)
before considering any manual retry. A failed, ambiguous, or temporarily empty
query is not enough to resolve every unknown outcome; reconcile with the tenant
administrator and provisioning system. The tool does not re-POST automatically.

The shared session, API-host mapping, credential resolver, per-instance rate
limiter, and request ID/audit mechanisms are reused. SCIM reads bypass the OData
cache so duplicate checks are fresh. Redirects are disabled. SCIM audit records
omit input values/payloads, raw response/error bodies, exception text, passwords,
and Authorization headers. Only explicitly allowed summary fields reach callers.

## Source references and verification limits

- [Adding a New Employee, OData V2 API Reference Guide §9.4.18.1](https://help.sap.com/docs/successfactors-platform/sap-successfactors-api-reference-guide-odata-v2/adding-new-employee):
  four-entity hire sequence and SCIM step after employment.
- [EmpEmployment Upsert](https://help.sap.com/docs/successfactors-platform/sap-successfactors-api-reference-guide-odata-v2/b5b4de5f425d4f6c92ca5b9c8e8b06a1.html):
  corresponding OData User creation and SCIM account step.
- [Managing User Information, Workforce SCIM overview (chapter 8)](https://help.sap.com/docs/successfactors-platform/managing-user-information/overview-of-sap-successfactors-workforce-system-for-cross-domain-identity-management-api):
  creation caution, SF as identity source, username prerequisite, and SCIM RBP.
- [Create a User](https://help.sap.com/docs/successfactors-platform/managing-user-information/8d2436694d984f719e677dcc85a0c1ba.html):
  POST endpoint, required properties, schema URNs, and UUID precedence.

`tests/test_workforce_scim_users.py` uses mocked transport exclusively. Public
SAP documentation was consulted; **no SuccessFactors tenant was contacted**.
SCIM enablement, effective RBP and target population, case-insensitive username
configuration, UUID filter support, tenant-specific validation and error shapes,
sync timing, and the customer's actual IAS/IPS/HRIS account-provisioning behavior
remain unverified. No employee/account, RBP, or tenant setting was changed.
