# M1 EC configuration write tools

M1 adds `upsert_foundation_object` and `upsert_picklist_value`. Both use the
M0 `make_odata_mutation` helper, which calls `check_write_allowed`, audits the
mutation, and invalidates the instance cache after a successful HTTP write.
Existing GET tools are unchanged.

## Controls and endpoint strategy

- `dry_run` defaults to **true**: returns the method, URL, endpoint, payload,
  parameters, and write flag without credentials resolution or any HTTP request.
- `SF_ALLOW_WRITE` defaults to **false**. Real writes require both
  `SF_ALLOW_WRITE=true` on the server and `dry_run=false` in the tool call.
- There is **no environment whitelist**. The ordinary data-center/environment
  validation still applies, including production support.
- Common required arguments are `instance`, `data_center`, `environment`,
  `auth_user_id`, and `auth_password`. Request context is injected automatically.

Both tools call `POST /odata/v2/upsert?purgeType=incremental`. The payload is a
**JSON array containing one entity**, following the conventional SF upsert
function-import pattern for an `UpsertResult` collection. Each entity retains
`__metadata.uri` naming the FO* entity or `PickListValueV2` and
`__metadata.type` set to `SFOData.{Entity}`. Business keys are JSON properties.
Explicit IDs and generated metadata override conflicting entries in `fields`.
Inputs are copied, not modified.

Endpoint availability was verified against preview metadata:
FunctionImport `upsert` is present with `POST` and
`sap:support-payload=true`; FO* and `PickListValueV2` have
`sap:upsertable=true` properties. This is metadata verification, not a live-write
test. Still confirm business keys, required dates, and writable properties per
tenant before executing. There is no automatic date selection, parent creation,
full purge, or effective-dating workflow.

Reference: [SAP upsert semantics](https://help.sap.com/docs/successfactors-platform/sap-successfactors-api-reference-guide-odata-v2/upsert).

## Foundation objects

`upsert_foundation_object` requires `object_type` and a nonempty `external_code`.
It shares `_FOUNDATION_ENTITIES` with `get_foundation_objects`, including its
case-insensitive lookup and unknown-type error with `valid_types`:

`company`, `department`, `division`, `location`, `cost_center`, `job_code`,
`job_function`, `pay_grade`, `pay_group`, `business_unit`, `event_reason`,
`legal_entity`.

`fields` is an optional object of exact SF property names. Only these snake-case
aliases are converted:

| Input | SF property |
| --- | --- |
| `start_date` | `startDate` |
| `end_date` | `endDate` |
| `name_default_value` | `name_defaultValue` |
| `description_default_value` | `description_defaultValue` |

`name`, `name_defaultValue`, `description`, and `description_defaultValue` match
the GET selections and pass through unchanged. Use tenant metadata to choose
writable properties. Avoid supplying both an alias and its canonical name.
Supply `start_date` when required by the entity; date values pass through in
SF OData format (for example `/Date(0)/`), with no implicit ISO conversion.

Example tool arguments (add the common connection arguments):

```json
{
  "object_type": "department",
  "external_code": "DEPT_DEMO",
  "fields": {
    "name": "Demo department",
    "status": "A",
    "start_date": "/Date(0)/"
  },
  "dry_run": true
}
```

## Picklist values

`upsert_picklist_value` requires nonempty `picklist_id` and `external_code`,
which become `PickListV2_id` and `externalCode`. Optional arguments:

- `label` and `locale` (default `en-US`) produce `label_en_US`, for example.
- `status` and `sort_order` become `status` and `sortOrder`; omitted values are
  not defaulted, and zero sort order is retained.
- `fields` passes exact SF property names through, including additional locale
  labels and `PickListV2_effectiveStartDate` for the parent picklist's date.

Supply the parent date and at least one label when inserting, according to tenant
metadata. The tool leaves required-field validation to SF. There is no write
fallback to legacy `PicklistOption` or automatic parent picklist modification.

```json
{
  "picklist_id": "demoColors",
  "external_code": "RED",
  "label": "Red",
  "locale": "en-US",
  "status": "A",
  "sort_order": 0,
  "fields": {
    "PickListV2_effectiveStartDate": "/Date(0)/",
    "label_fr_FR": "Rouge"
  },
  "dry_run": true
}
```

IDs and labels are JSON values, never interpolated into filters or keyed URLs;
therefore quotes are preserved, not OData-escaped. The existing GET tools use
`sanitize_odata_string` for interpolated string literals. When constructing a
foundation verification filter yourself, escape apostrophes by doubling them.

## Verify a real write

1. Review the dry-run endpoint and payload, including the intended business keys
   and effective dates. Preview success does not validate tenant schema or access.
2. Enable `SF_ALLOW_WRITE=true`, then repeat with `dry_run=false`.
3. Inspect the returned SF response, including any per-record errors. The tools
   return the M0 preview, SF response, or structured error without rewriting it.
4. Call `get_foundation_objects` with the same connection, `object_type`,
   `filter="externalCode eq 'DEPT_DEMO'"`, and `include_inactive=true`; or call
   `get_picklist_values` with the same `picklist_id`, `locale`, and
   `include_inactive=true`. Check the saved code, label/name, status, and dates.
   The picklist GET formatter may not expose every V2 localized field: use
   `query_odata` on `PickListValueV2` to inspect `label_en_US` and parent date
   directly. Use `query_odata` for additional foundation fields as well.
5. If a transport error leaves the outcome unknown, verify before retrying.

No live SF access is required for `tests/test_ec_config_write.py`. Tests mock
mutations or the HTTP session and exercise both tools through their decorators.
No Emp*, User, compensation, workflow, or RBP write tools are added.
