"""Sensitive EC entities: narrow reads and individually previewable writes.

Payment detail writes do not orchestrate parent/child or validate local banking
rules; an SAP success response and read-back are required. Attachments are
created separately from linking them to the owning business record.
"""

import base64
import binascii
import re
from typing import Any

from sf_mcp.client import make_odata_request
from sf_mcp.decorators import sf_tool
from sf_mcp.dependencies import ApiHost, RequestId, StartTime
from sf_mcp.server import mcp
from sf_mcp.tools.ec_upsert import upsert_ec_entity
from sf_mcp.validation import sanitize_odata_string


def _read(entity, field, value, instance, data_center, environment, auth_user_id, auth_password, request_id, top):
    if not value or not value.strip():
        return {"error": f"{field} must not be empty"}
    params = {"$format": "json", "$top": str(top), "$filter": f"{field} eq '{sanitize_odata_string(value)}'"}
    result = make_odata_request(instance, f"/odata/v2/{entity}", data_center, environment,
                                auth_user_id, auth_password, params, request_id)
    if "error" in result:
        return result
    rows = result.get("d", {}).get("results")
    if not isinstance(rows, list):
        return {"error": "Unexpected OData response; no results list"}
    return {"entity": entity, "results": rows, "count": len(rows), "may_have_more": len(rows) >= top}


@mcp.tool()
@sf_tool("get_emergency_contacts", max_top=100, log_parameters=False)
def get_emergency_contacts(instance: str, data_center: str, environment: str, auth_user_id: str,
                           auth_password: str, person_id_external: str, top: int = 100, *,
                           request_id: str = RequestId(), start_time: float = StartTime(),
                           api_host: str = ApiHost()) -> dict[str, Any]:
    """Read emergency contacts for one person. Results may contain sensitive personal data."""
    return _read("PerEmergencyContacts", "personIdExternal", person_id_external, instance, data_center,
                 environment, auth_user_id, auth_password, request_id, top)


@mcp.tool()
@sf_tool("upsert_emergency_contact", log_parameters=False)
def upsert_emergency_contact(instance: str, data_center: str, environment: str, auth_user_id: str,
                             auth_password: str, person_id_external: str, name: str, relationship: str,
                             fields: dict[str, Any] | None = None, dry_run: bool = True, *,
                             request_id: str = RequestId(), start_time: float = StartTime(),
                             api_host: str = ApiHost()) -> dict[str, Any]:
    """Preview or upsert a PerEmergencyContacts record; fields may require tenant-specific data."""
    return upsert_ec_entity("PerEmergencyContacts", {"personIdExternal": person_id_external,
                            "name": name, "relationship": relationship}, instance, data_center,
                            environment, auth_user_id, auth_password, fields, dry_run, request_id)


@mcp.tool()
@sf_tool("get_person_relationships", max_top=100, log_parameters=False)
def get_person_relationships(instance: str, data_center: str, environment: str, auth_user_id: str,
                             auth_password: str, person_id_external: str, top: int = 100, *,
                             request_id: str = RequestId(), start_time: float = StartTime(),
                             api_host: str = ApiHost()) -> dict[str, Any]:
    """Read current effective family/person relationships; use query_odata for dated history."""
    return _read("PerPersonRelationship", "personIdExternal", person_id_external, instance, data_center,
                 environment, auth_user_id, auth_password, request_id, top)


@mcp.tool()
@sf_tool("upsert_person_relationship", log_parameters=False)
def upsert_person_relationship(instance: str, data_center: str, environment: str, auth_user_id: str,
                                auth_password: str, person_id_external: str, related_person_id_external: str,
                                start_date: str, fields: dict[str, Any] | None = None, dry_run: bool = True, *,
                                request_id: str = RequestId(), start_time: float = StartTime(),
                                api_host: str = ApiHost()) -> dict[str, Any]:
    """Preview or upsert a relationship. This does not create the related person's records."""
    return upsert_ec_entity("PerPersonRelationship", {"personIdExternal": person_id_external,
                            "relatedPersonIdExternal": related_person_id_external, "startDate": start_date},
                            instance, data_center, environment, auth_user_id, auth_password, fields, dry_run, request_id)


@mcp.tool()
@sf_tool("get_payment_information", max_top=100, log_parameters=False)
def get_payment_information(instance: str, data_center: str, environment: str, auth_user_id: str,
                            auth_password: str, user_id: str, top: int = 100, *,
                            request_id: str = RequestId(), start_time: float = StartTime(),
                            api_host: str = ApiHost()) -> dict[str, Any]:
    """Read payment details for a worker. Contains sensitive bank data; current effective slice only."""
    return _read("PaymentInformationDetailV3", "PaymentInformationV3_worker", user_id, instance, data_center,
                 environment, auth_user_id, auth_password, request_id, top)


@mcp.tool()
@sf_tool("upsert_payment_information", log_parameters=False)
def upsert_payment_information(instance: str, data_center: str, environment: str, auth_user_id: str,
                               auth_password: str, worker: str, effective_start_date: str,
                               fields: dict[str, Any] | None = None, dry_run: bool = True, *,
                               request_id: str = RequestId(), start_time: float = StartTime(),
                               api_host: str = ApiHost()) -> dict[str, Any]:
    """Preview or upsert the payment parent. Does not create payment details."""
    return upsert_ec_entity("PaymentInformationV3", {"worker": worker, "effectiveStartDate": effective_start_date},
                            instance, data_center, environment, auth_user_id, auth_password, fields, dry_run, request_id)


@mcp.tool()
@sf_tool("upsert_payment_detail", log_parameters=False)
def upsert_payment_detail(instance: str, data_center: str, environment: str, auth_user_id: str,
                          auth_password: str, worker: str, effective_start_date: str,
                          fields: dict[str, Any], external_code: str | None = None, dry_run: bool = True, *,
                          request_id: str = RequestId(), start_time: float = StartTime(),
                          api_host: str = ApiHost()) -> dict[str, Any]:
    """Preview or upsert payment detail. Provide external_code for updates; local requirements vary."""
    keys = {"PaymentInformationV3_worker": worker, "PaymentInformationV3_effectiveStartDate": effective_start_date}
    if external_code is not None:
        if not external_code.isdecimal():
            return {"error": "external_code must be a nonnegative numeric key"}
        keys["externalCode"] = external_code
    return upsert_ec_entity("PaymentInformationDetailV3", keys, instance, data_center, environment,
                            auth_user_id, auth_password, fields, dry_run, request_id)


@mcp.tool()
@sf_tool("upsert_payment_detail_country", log_parameters=False)
def upsert_payment_detail_country(instance: str, data_center: str, environment: str, auth_user_id: str,
                                  auth_password: str, country_code: str, worker: str,
                                  effective_start_date: str, payment_detail_external_code: str,
                                  external_code: str, fields: dict[str, Any], dry_run: bool = True, *,
                                  request_id: str = RequestId(), start_time: float = StartTime(),
                                  api_host: str = ApiHost()) -> dict[str, Any]:
    """Preview or upsert a country-specific payment child using both parent and child numeric keys.

    Check that the country entity is exposed and supported by the tenant. This does not create parents.
    """
    if not re.fullmatch(r"[A-Z]{3}", country_code):
        return {"error": "country_code must be an uppercase ISO3 selector"}
    if not payment_detail_external_code.isdecimal() or not external_code.isdecimal():
        return {"error": "payment detail keys must be nonnegative numeric values"}
    keys = {"PaymentInformationV3_worker": worker,
            "PaymentInformationV3_effectiveStartDate": effective_start_date,
            "PaymentInformationDetailV3_externalCode": payment_detail_external_code,
            "externalCode": external_code}
    return upsert_ec_entity(f"PaymentInformationDetailV3{country_code}", keys, instance, data_center,
                            environment, auth_user_id, auth_password, fields, dry_run, request_id)


@mcp.tool()
@sf_tool("get_attachment", log_parameters=False)
def get_attachment(instance: str, data_center: str, environment: str, auth_user_id: str,
                   auth_password: str, attachment_id: str, include_content: bool = False, *,
                   request_id: str = RequestId(), start_time: float = StartTime(),
                   api_host: str = ApiHost()) -> dict[str, Any]:
    """Read one Attachment's metadata, optionally including base64 content. Content is sensitive."""
    if not attachment_id.isdecimal():
        return {"error": "attachment_id must be numeric"}
    select = "attachmentId,fileName,fileSize,mimeType,module" + (",fileContent" if include_content else "")
    return _read_attachment(attachment_id, select, instance, data_center, environment, auth_user_id, auth_password, request_id)


def _read_attachment(attachment_id, select, instance, data_center, environment, auth_user_id, auth_password, request_id):
    result = make_odata_request(instance, f"/odata/v2/Attachment({attachment_id}L)", data_center, environment,
                                auth_user_id, auth_password, {"$format": "json", "$select": select}, request_id)
    if "error" in result:
        return result
    if not isinstance(result.get("d"), dict):
        return {"error": "Unexpected OData attachment response"}
    return {"entity": "Attachment", "result": result["d"]}


@mcp.tool()
@sf_tool("create_attachment", log_parameters=False)
def create_attachment(instance: str, data_center: str, environment: str, auth_user_id: str,
                      auth_password: str, file_name: str, file_content_base64: str, module: str,
                      fields: dict[str, Any] | None = None, dry_run: bool = True, *,
                      request_id: str = RequestId(), start_time: float = StartTime(),
                      api_host: str = ApiHost()) -> dict[str, Any]:
    """Preview or create an attachment from base64; links to employee records are a separate step.

    Payload previews contain file content. Maximum decoded size is 5 MiB; tenant limits may be lower.
    """
    if len(file_content_base64) > 7_000_000:
        return {"error": "Attachment exceeds 5 MiB limit"}
    try:
        content = base64.b64decode(file_content_base64, validate=True)
    except (binascii.Error, ValueError):
        return {"error": "file_content_base64 must be valid base64"}
    if not content or len(content) > 5 * 1024 * 1024:
        return {"error": "Attachment must be nonempty and no larger than 5 MiB"}
    if fields and any(key in fields for key in ("attachmentId", "attachment_id", "operation")):
        return {"error": "create_attachment cannot update or delete an existing attachment"}
    return upsert_ec_entity("Attachment", {"fileName": file_name, "fileContent": file_content_base64,
                            "module": module}, instance, data_center, environment, auth_user_id,
                            auth_password, fields, dry_run, request_id)
