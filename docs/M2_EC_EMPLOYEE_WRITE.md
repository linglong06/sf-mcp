# M2 EC employee and related write tools

Twenty-four transactional tools use the existing M0 mutation client. Existing
GET tools and M1 foundation-object/picklist tools are unchanged.

A login account is not the same as an OData `User` row or a successful
`upsert_user`. `EmpEmployment` creates the corresponding OData User entity.
Follow PerPerson → EmpEmployment → EmpJob → PerPersonal; do not default to
OData User first. See [Workforce SCIM Users](WORKFORCE_SCIM_USERS.md) for
`get_scim_user` / `create_scim_user`, the SCIM step after EmpEmployment, and the
required tenant IAS/IPS provisioning-model confirmation before a real create.

## Tools and required business keys

| Tool | Entity | Required arguments → payload properties |
| --- | --- | --- |
| `upsert_emp_job` | `EmpJob` | `user_id` → `userId`, `start_date` → `startDate`, `seq_number` → `seqNumber` |
| `upsert_emp_employment` | `EmpEmployment` | `person_id_external` → `personIdExternal`, `user_id` → `userId` |
| `upsert_emp_job_relationship` | `EmpJobRelationships` | `user_id` → `userId`, `start_date` → `startDate`, `relationship_type` → `relationshipType` |
| `upsert_per_person` | `PerPerson` | `person_id_external` → `personIdExternal` |
| `upsert_per_personal` | `PerPersonal` | `person_id_external` → `personIdExternal`, `start_date` → `startDate` |
| `upsert_per_email` | `PerEmail` | `person_id_external` → `personIdExternal`, `email_type` → `emailType` |
| `upsert_per_phone` | `PerPhone` | `person_id_external` → `personIdExternal`, `phone_type` → `phoneType` |
| `upsert_per_address` | `PerAddressDEFLT` | `person_id_external` → `personIdExternal`, `address_type` → `addressType`, `start_date` → `startDate` |
| `upsert_per_national_id` | `PerNationalId` | `person_id_external` → `personIdExternal`, `country` → `country`, `card_type` → `cardType` |

## M2.3/M2.4 and expanded write tools

| Tool | Entity | Required arguments → payload properties |
| --- | --- | --- |
| `upsert_emp_compensation` | `EmpCompensation` | `user_id` → `userId`, `start_date` → `startDate` |
| `upsert_emp_pay_comp_recurring` | `EmpPayCompRecurring` | `user_id` → `userId`, `start_date` → `startDate`, `seq_number` → `seqNumber`, `pay_component` → `payComponent` |
| `upsert_emp_pay_comp_non_recurring` | `EmpPayCompNonRecurring` | `user_id` → `userId`, `pay_date` → `payDate`, `pay_component_code` → `payComponentCode` |
| `upsert_position` | `Position` | `code` → `code`, `effective_start_date` → `effectiveStartDate` |
| `upsert_emp_work_permit` | `EmpWorkPermit` | `user_id` → `userId`, `country` → `country`, `document_type` → `documentType`, `document_number` → `documentNumber`, `issue_date` → `issueDate` |
| `upsert_emp_employment_termination` | `EmpEmploymentTermination` | `person_id_external` → `personIdExternal`, `user_id` → `userId`, `end_date` → `endDate` |
| `upsert_emp_employment_higher_duty` | `EmpEmploymentHigherDuty` | `user_id` → `userId` (only metadata key; other properties via `fields`) |
| `upsert_user` | `User` | `user_id` → `userId` |
| `upsert_employee_time` | `EmployeeTime` | `external_code` → `externalCode` |
| `upsert_employee_time_country` | `EmployeeTime{ISO3}` | `country_code` selects entity; `external_code` → `externalCode` |
| `upsert_rbp_role` | `RBPRole` | `role_id` → `roleId` |
| `upsert_rbp_rule` | `RBPRule` | `rule_id` → `ruleId` |
| `upsert_dynamic_group` | `DynamicGroup` | `group_id` → `groupID` (SF spelling) |
| `upsert_per_global_info` | `PerGlobalInfo{ISO3}` | `country_code` selects entity and sets `country`; `person_id_external` → `personIdExternal`, `start_date` → `startDate` |

Employee, compensation, User, and GlobalInfo tools live in `employee.py`;
Position, time off, and permission tools live in their existing modules.

### Country selectors

Country codes must exactly match an uppercase ISO3 code below. Unknown codes
return an error listing valid codes and a sorted `valid_types` list; empty codes
are rejected. `country_code` is a selector, not a payload property.

`upsert_per_global_info` supports these 53 codes from the supplied hisensehonT2
scope, through one generic tool:

```text
ARE ARG AUS AUT BEL BGD BRA CAN CHE CHL CHN COL CZE DEU DNK EGY ESP FIN
FRA GBR GTM HKG HUN IDN IND IRL ITA JPN KOR LKA MEX MYS NLD NOR NZL OMN
PER PHL POL PRT QAT ROU RUS SAU SGP SWE THA TUR TWN USA VEN VNM ZAF
```

For example, `country_code="USA"` selects `PerGlobalInfoUSA` and sets
`country="USA"`, overriding any `fields.country` value.

`upsert_employee_time_country` supports exactly these 12 codes:

```text
AUS CAN COL DEU ESP ITA MEX NLD NOR POL SWE USA
```

For example, `country_code="DEU"` selects `EmployeeTimeDEU`. It does not add a
`country` property. No additional Time entities are inferred or supported.

### High-impact writes

- **Termination is dangerous:** `upsert_emp_employment_termination` writes an
  employment termination record. Review the person/user IDs, end date, and all
  tenant-required fields with dry-run first. This is not termination orchestration.
- **User has a large surface (login/HRIS hybrid):** prefer Per*/Emp* tools for
  person/job data. The caller owns field choice; review the preview carefully.
- **Permission writes are high-impact:** RBPRole, RBPRule, and DynamicGroup changes
  can affect access and target populations. Use dry-run first and review fields.

Common required arguments: `instance`, `data_center`, `environment`,
`auth_user_id`, `auth_password`. Each tool also accepts optional `fields` and
`dry_run=True`. Request context is injected and hidden from the MCP schema.
Empty or whitespace-only required key strings return an error before mutation.
`seq_number` and `background_element_id` accept a string or integer; other business-key arguments are strings.

## Background employee profile portlets

One generic `upsert_background_element` tool in `employee.py` selects
`Background_{Type}` using `background_type`. An optional `Background_` prefix
is removed; type names otherwise match exactly. Supported hisensehonT2 types:

```text
Awards Benefitselection Community Compensation Courses Documents Education
Fsaelection FuncExperience Googledocs InsideWorkExperience Languages
LeadExperience Memberships Mobility OutsideWorkExperience PreferredNextMove
Promotability SpecialAssign SysScoreCardDevelopmentObjectivesPortlet
TalentPool TalentPoolcorp VarPayEmpHistData VarPayEmpHistDataECSource
```

`background_element_id` → `backgroundElementId` is always required. All types
also require `user_id` → `userId`, except `VarPayEmpHistData` and
`VarPayEmpHistDataECSource`, whose only key is `backgroundElementId`. For these
two types, the `user_id` argument is ignored; `userId` or `user_id` may still
be supplied in `fields` as a non-key property.

Unknown types return an error and sorted `valid_types`. Empty required IDs are
rejected before mutation. Optional `fields` and `dry_run=True` use the same
shared upsert path and controls described below.

Example arguments (add common connection arguments):

```json
{
  "background_type": "Education",
  "background_element_id": "123",
  "user_id": "USER_DEMO",
  "dry_run": true
}
```

## Payload and controls

Every tool calls **POST `/odata/v2/upsert?purgeType=incremental`** with a JSON
array containing exactly one entity. Each entity contains
`__metadata: {"uri": "<EntityName>", "type": "SFOData.<EntityName>"}`.

`fields` accepts SF property names, including tenant custom fields. Known aliases
are converted: `user_id`, `person_id_external`, `start_date`, `end_date`,
`seq_number`, `relationship_type`, `email_type`, `phone_type`, `address_type`,
`card_type`, `pay_component`, `pay_component_code`, `pay_date`,
`effective_start_date`, `document_type`, `document_number`, `issue_date`,
`role_id`, `rule_id`, `external_code`, `background_element_id` become their camelCase equivalents.
`group_id` becomes `groupID`. Other names pass through unchanged.
Avoid supplying both spellings of an optional property: the last supplied entry
wins. Explicit required keys and generated metadata always override `fields`.
The input dictionary is not modified. Values, including apostrophes and dates,
are passed literally as JSON values; no OData filter escaping is applied to writes.
Supply dates in the tenant's SF OData format (for example `/Date(0)/`); there is
no implicit date conversion, date selection, or event-reason selection.

- Default `dry_run=True` returns the M0 preview without HTTP or credential resolution.
- Real writes require both `dry_run=False` and server-side `SF_ALLOW_WRITE=true`.
  The write flag defaults to false.
- There is **no environment whitelist**; ordinary data-center/environment
  validation still applies, including production support.
- Results are returned unchanged from M0: preview, SF response, or structured error.
  M0 handles mutation auditing and instance-cache invalidation.

The supplied metadata verification baseline is **hisensehonT2 preview**:
property-level `sap:upsertable` metadata was verified for the scoped entities.
This is metadata verification, not a successful live-write test, and was not
repeated by the mocked tests in this change. Still confirm business keys,
required properties, writable properties, permissions, and date formats against
**each tenant's metadata** before writing. Property-level upsertability alone
does not establish a complete valid business transaction.

Example `upsert_per_email` arguments (add common connection arguments):

```json
{
  "person_id_external": "PERSON_DEMO",
  "email_type": "business",
  "fields": {
    "emailAddress": "person@example.com",
    "isPrimary": true
  },
  "dry_run": true
}
```

Use the tenant's actual email-type code and required fields.

## Verify with existing GET tools

1. Review the dry-run endpoint, keys, metadata, and fields. A preview does not
   validate tenant schema, access, event reasons, or effective-dating rules.
2. Enable the server write flag and repeat the reviewed call with `dry_run=false`.
3. Inspect the full SF response, including per-record errors even if HTTP is 200.
4. Use `query_odata` with the same connection and the entity from the table.
   Filter by every business key and select the keys plus changed properties.
   For the email example, use `entity="PerEmail"`,
   `filter="personIdExternal eq 'PERSON_DEMO' and emailType eq 'business'"`,
   and `select="personIdExternal,emailType,emailAddress,isPrimary"`.
   Escape apostrophes in filter string literals by doubling them. Use the tenant's
   key types when constructing numeric or datetime filter literals.
5. For job changes, `get_employee_history(user_id=...)` also provides a formatted
   view; use `query_odata` to inspect `seqNumber` and any fields omitted by that
   formatter. Effective-dated GETs may apply tenant current-date defaults, so a
   missing future or historical row in a default query is not proof of failure;
   inspect the intended effective slice using tenant-supported read parameters.
6. If a transport error leaves the outcome unknown, verify before retrying.

Tests in `tests/test_ec_employee_write.py` mock mutations or sessions. They
exercise all 24 registered tools without live SF access, including every country
selector, empty keys, blocked writes, aliases, and default dry-run schemas.

## Explicit non-goals

No payroll-replication (`EmployeeDataReplication*`) or
`PaymentInformationV3*` / PaymentInformation country-matrix writes. No workflow
approval actions, hire orchestration, full termination workflows, automatic
event reasons, effective-dating engines, parent-record creation, or full purge.
FO/picklist configuration writes remain M1 scope. No live SF writes are used
for implementation or tests.
