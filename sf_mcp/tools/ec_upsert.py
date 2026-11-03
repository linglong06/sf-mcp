"""Shared payload construction for EC personnel upserts."""

from typing import Any

from sf_mcp.client import make_odata_mutation

_FIELD_ALIASES = {
    "background_element_id": "backgroundElementId",
    "user_id": "userId",
    "person_id_external": "personIdExternal",
    "start_date": "startDate",
    "end_date": "endDate",
    "seq_number": "seqNumber",
    "relationship_type": "relationshipType",
    "email_type": "emailType",
    "phone_type": "phoneType",
    "address_type": "addressType",
    "card_type": "cardType",
    "pay_component": "payComponent",
    "pay_component_code": "payComponentCode",
    "pay_date": "payDate",
    "effective_start_date": "effectiveStartDate",
    "document_type": "documentType",
    "document_number": "documentNumber",
    "issue_date": "issueDate",
    "role_id": "roleId",
    "rule_id": "ruleId",
    "group_id": "groupID",
    "external_code": "externalCode",
}


def upsert_ec_entity(
    entity: str,
    keys: dict[str, Any],
    instance: str,
    data_center: str,
    environment: str,
    auth_user_id: str,
    auth_password: str,
    fields: dict[str, Any] | None,
    dry_run: bool,
    request_id: str,
) -> dict[str, Any]:
    """Copy fields, normalize aliases, and apply authoritative keys and metadata."""
    for key, value in keys.items():
        if isinstance(value, str) and not value.strip():
            return {"error": f"{key} must not be empty"}

    payload = {_FIELD_ALIASES.get(key, key): value for key, value in (fields or {}).items()}
    payload.update({_FIELD_ALIASES.get(key, key): value for key, value in keys.items()})
    payload["__metadata"] = {"uri": entity, "type": f"SFOData.{entity}"}
    return make_odata_mutation(
        instance,
        "/odata/v2/upsert",
        data_center,
        environment,
        auth_user_id,
        auth_password,
        method="POST",
        params={"purgeType": "incremental"},
        payload=[payload],
        dry_run=dry_run,
        request_id=request_id,
    )
