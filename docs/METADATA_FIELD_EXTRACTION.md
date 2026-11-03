# Metadata fields and required levels

`get_configuration(entity="Position", ...)` and
`get_mdf_object_definitions(object_name="Position", ...)` now use the shared
`sf_mcp/metadata_parse.py` walker. The same behavior applies to other named MDF
objects, including `cust_` objects.

SuccessFactors metadata can contain an `SFODataSet` Schema holding only the
EntityContainer, followed by an `SFOData` Schema containing EntityTypes. The old
extractors selected only Schema[0], returning zero fields. The walker searches
every Schema and matches EntityType `@Name` exactly and case-sensitively. It never
merges unrelated entity definitions. The seeded single- and dual-schema Position
fixtures both yield **88 properties and 47 navigation properties**.

The existing MCP tools expose this analysis without adding another tool:

- Configuration `fields` remains a dictionary keyed by name; MDF `fields` remains
  a list. Each field has `name`, `kind`, `type`, `nullable`, `maxLength`, raw `sap`
  annotations, `api_odata`, `required_sources`, and `required_mapping_exception`.
  MDF also retains the `max_length` alias when present.
- Both inventories now include navigation fields, distinguished by
  `kind: "NavigationProperty"` and `type: "NavigationProperty"`, with their own
  `relationship`, roles, and required annotations. Scalar fields use
  `kind: "Property"` and their EDM type. Navigation requirements are never copied
  to a scalar property.
- `field_count` counts both kinds (135 for these fixtures); `property_count` and
  `navigation_property_count` give the breakdown. Configuration previously
  counted only scalar properties. Consumers that need scalar fields should use
  `kind` or `property_count`.
- XML element local names and prefixed/Clark forms are handled. SAP attributes
  are read from recognized Clark namespace keys (including the SuccessFactors
  SAP URI), with support for `@sap:` dictionaries. Raw boolean strings are
  preserved in `sap`; normalized classifications use JSON booleans or null.

## Required-level taxonomy

| Level | Evidence and boundaries |
| --- | --- |
| `api_odata` | API/edit-attribute mapping only. Explicit `sap:required` takes precedence. If absent, explicit `Nullable` is used as a nullability-only fallback (`false` → required; `true` → optional). `basis`, `sap_required`, and `nullable` retain the distinction. This does not predict whether an omitted value will be accepted by a particular operation. |
| `object_definition` | `availability: "unsupported"`, `status: "unknown"`, `required: null`. This repository has no supported Configure Object Definition read API/export integration. It does not assume a general ObjectDefinition OData entity exists. |
| `ui_or_rule` | `availability: "unknown"`, `status: "unknown"`, `required: null`. No page or business-rule export is available. |

Each field exposes `api_odata` directly and all three levels in
`required_sources`, with source limitations in `notes`. Classifications are
`required`, `optional`, or `unknown`. Missing/unrecognized annotations remain
unknown unless an explicit supported fallback exists; an invalid `sap:required`
value remains unknown. There is no invented default Nullable value. Explicit
`sap:required=false` with `Nullable=false` retains both signals and a note about
their distinct constraints. Unknown is never converted to optional.

SAP's **Mapping of Edit Attributes** describes exceptions for scalar properties
mapped from Picklist, Generic Object, and User: their `sap:required` can be false
even when the MDF field is required. Scalar and navigation values can differ.
See the MDF OData API chapter of the
[SAP SuccessFactors OData API guide, SF_HCM_OData_API_DEV.pdf](https://help.sap.com/doc/a7c08a422cc14e1eaaffee83610a981d/2511/en-US/SF_HCM_OData_API_DEV.pdf).
`required_mapping_exception` records the affected MDF types and whether a
picklist annotation is present. The caveat accompanies all fields because an
`Edm.String` alone cannot establish a Generic Object or User mapping.

In the real-structure fixtures, `cust_Type` (Position Class) has
`sap:picklist=employeeClass`, `Nullable=true`, and `sap:required=false`.
`cust_TypeNav` also has `sap:required=false`. Both report
`api_odata.required=false`, while `ui_or_rule.status="unknown"` and
`object_definition.status="unknown"`. This is compatible with the reported
required UI field and API acceptance of an omitted value; OData optional must
never be presented as proof that the UI or rules make the field optional.

## Failure and safety boundaries

Successful extraction returns `status: "success"`; this means the field
inventory was extracted, not that all required sources were available.
Wrong-format documents return `status: "error"` / `invalid_metadata`.
`status: "incomplete"` distinguishes `no_entity_types`, `entity_not_found`, and
`no_fields`; malformed fields and ambiguous/duplicate definitions are also
explicit errors. HTTP/authentication and XML parsing errors remain errors.
Missing fetch results do not claim that the entity is absent. Configuration
comparison uses the same entity filter and rejects incomplete inventories;
its existing comparison still covers field names/types, not required-level drift.

`dry_run` is **payload preview only**, not SAP server-side validation. A preview
must never be described as “validation passed.” This change makes no tenant
writes and adds no business-rule/page required coverage.

Verify offline (all HTTP in the new tests is blocked or mocked):

```sh
.venv/bin/pytest -q tests/test_metadata_fields.py tests/test_client.py tests/test_decorators.py tests/test_ec_config_write.py
```
