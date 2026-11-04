"""Explicit Workforce SCIM account query/create, separate from OData User upserts."""

from typing import Any

from sf_mcp.client import check_write_allowed
from sf_mcp.decorators import sf_tool
from sf_mcp.dependencies import ApiHost, RequestId, StartTime
from sf_mcp.scim import SCHEMAS, SF_SCHEMA, lookup_scim_user, scim_request
from sf_mcp.server import mcp


def _valid_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


@mcp.tool()
@sf_tool("get_scim_user", log_parameters=False)
def get_scim_user(
    instance: str,
    data_center: str,
    environment: str,
    auth_user_id: str = "",
    auth_password: str = "",
    user_name: str | None = None,
    person_id_external: str | None = None,
    *,
    request_id: str = RequestId(),
    start_time: float = StartTime(),
    api_host: str = ApiHost(),
) -> dict[str, Any]:
    """Query Workforce SCIM Users by exactly one user_name OR person_id_external.

    Returns found (sanitized account), not_found, multiple, incomplete, unauthorized,
    or forbidden; other transport/rate errors never imply absence. One page with
    count=2/startIndex=1; totalResults must prove uniqueness. Requires SCIM Read RBP
    and case-insensitive usernames enabled; no client case folding. OData User
    existence/upsert_user does not prove a login account exists. Before calling
    create_scim_user with dry_run=False, confirm the tenant provisioning model:
    IAS/IPS or HRIS/real-time sync may already provision the login account.
    """
    keys = {"userName": user_name, "personIdExternal": person_id_external}
    supplied = {key: value for key, value in keys.items() if value is not None}
    if len(supplied) != 1 or not all(_valid_text(value) for value in supplied.values()):
        return {
            "outcome": "invalid_input",
            "error": "Supply exactly one nonempty user_name or person_id_external (mutually exclusive).",
        }
    attribute, value = next(iter(supplied.items()))
    return lookup_scim_user(
        instance=instance,
        data_center=data_center,
        environment=environment,
        auth_user_id=auth_user_id,
        auth_password=auth_password,
        request_id=request_id,
        attribute=attribute,
        value=value,
    )


@mcp.tool()
@sf_tool("create_scim_user", log_parameters=False)
def create_scim_user(
    instance: str,
    data_center: str,
    environment: str,
    user_name: str,
    user_type: str,
    active: bool,
    auth_user_id: str = "",
    auth_password: str = "",
    person_id_external: str | None = None,
    per_person_uuid: str | None = None,
    locale: str | None = None,
    timezone: str | None = None,
    external_id: str | None = None,
    emails: list[dict[str, Any]] | None = None,
    phone_numbers: list[dict[str, Any]] | None = None,
    login_method: str | None = None,
    dry_run: bool = True,
    provisioning_model_confirmed: bool = False,
    *,
    request_id: str = RequestId(),
    start_time: float = StartTime(),
    api_host: str = ApiHost(),
) -> dict[str, Any]:
    """Preview or explicitly POST a Workforce SCIM login account after EmpEmployment.

    SAP Adding a New Employee requires PerPerson -> EmpEmployment -> EmpJob ->
    PerPersonal plus SCIM account creation (may start after EmpEmployment creates
    the employment/OData User row). upsert_user is not proof of a login account;
    do not default to OData User first. SAP Managing User Information ch.8 also
    discourages SCIM creation: SF mainly supplies workforce identities to IAS/IPS.
    Do not create unconditionally or where IAS/IPS or HRIS/real-time sync already
    provisions login accounts; reverse IPS-to-SF creation is not a usable hire path.

    Before dry_run=False, confirm this tenant needs explicit Workforce SCIM login
    creation for this API-driven EC hire, EmpEmployment exists, IAS/IPS is not
    auto-creating that account, SCIM is enabled, case-insensitive usernames are
    enabled, and required SCIM Read/Edit RBP plus Employee Export/target population
    are available. Only then set provisioning_model_confirmed=True. OData User
    permissions do not imply SCIM permissions. Not every tenant needs this tool.

    Requires user_name, user_type, explicit active, and at least one person key.
    If both person keys are given, only per_person_uuid is sent: SAP uses UUID to
    get EC info. All supplied keys are still checked for duplicates. Optional
    identity fields are sent only if supplied; emails/phone_numbers support
    value/type/primary entries. No password field is accepted.

    dry_run=True is payload preview only, NOT SAP validation; no credentials or
    HTTP required. Real POST also requires SF_ALLOW_WRITE=true. Preflight queries
    must all return not_found. Existing accounts/HTTP 409 return conflict; errors
    or incomplete reads block POST. POST is never automatically retried. Timeout
    or connection failure means unknown: verify with get_scim_user before retrying.
    """
    if not _valid_text(user_name) or not _valid_text(user_type) or type(active) is not bool:
        return {"outcome": "invalid_input", "error": "user_name, user_type and explicit boolean active are required."}
    person_keys = {"personIdExternal": person_id_external, "perPersonUuid": per_person_uuid}
    supplied_keys = {key: value for key, value in person_keys.items() if value is not None}
    if not supplied_keys or not all(_valid_text(value) for value in supplied_keys.values()):
        return {
            "outcome": "invalid_input",
            "error": "At least one nonempty person_id_external or per_person_uuid is required.",
        }
    if type(dry_run) is not bool or type(provisioning_model_confirmed) is not bool:
        return {"outcome": "invalid_input", "error": "dry_run and provisioning_model_confirmed must be booleans."}

    payload: dict[str, Any] = {
        "schemas": list(SCHEMAS),
        "userName": user_name,
        "userType": user_type,
        "active": active,
        SF_SCHEMA: {"perPersonUuid": per_person_uuid}
        if per_person_uuid is not None
        else {"personIdExternal": person_id_external},
    }
    for field, value in (
        ("locale", locale),
        ("timezone", timezone),
        ("externalId", external_id),
        ("loginMethod", login_method),
    ):
        if value is not None:
            if not _valid_text(value):
                return {"outcome": "invalid_input", "error": f"{field} must be a nonempty string if supplied."}
            target = payload[SF_SCHEMA] if field == "loginMethod" else payload
            target[field] = value
    for field, entries in (("emails", emails), ("phoneNumbers", phone_numbers)):
        if entries is None:
            continue
        if not isinstance(entries, list) or any(
            not isinstance(entry, dict)
            or not _valid_text(entry.get("value"))
            or set(entry) - {"value", "type", "primary"}
            or ("type" in entry and not _valid_text(entry["type"]))
            or ("primary" in entry and type(entry["primary"]) is not bool)
            for entry in entries
        ):
            return {
                "outcome": "invalid_input",
                "error": f"{field} entries require value; only type/primary are optional.",
            }
        payload[field] = [dict(entry) for entry in entries]

    connection = dict(
        instance=instance,
        data_center=data_center,
        environment=environment,
        auth_user_id=auth_user_id,
        auth_password=auth_password,
        request_id=request_id,
    )
    if not dry_run:
        try:
            check_write_allowed(False)
        except PermissionError as exc:
            return {"outcome": "blocked", "error": str(exc)}
        if not provisioning_model_confirmed:
            return {
                "outcome": "blocked",
                "error": "Confirm the tenant account-provisioning model and prerequisites "
                "described by this tool, then set provisioning_model_confirmed=True.",
            }
        for attribute, value in {"userName": user_name, **supplied_keys}.items():
            result = lookup_scim_user(**connection, attribute=attribute, value=value)
            if result["outcome"] in {"found", "multiple"} or result.get("users"):
                return {
                    "outcome": "conflict",
                    "error": "An account matched a supplied identity; no POST was sent.",
                    "matched_by": attribute,
                    "lookup": result,
                    "request_id": request_id,
                }
            if result["outcome"] != "not_found":
                return {
                    **result,
                    "phase": "preflight",
                    "message": "Duplicate check was inconclusive; no POST was sent.",
                }
    return scim_request(**connection, method="POST", payload=payload, dry_run=dry_run)
