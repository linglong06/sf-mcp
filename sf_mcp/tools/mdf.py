"""MDF (Metadata Framework) tools: object definitions, generic queries, foundation objects."""

from typing import Any

from sf_mcp.client import make_metadata_request, make_odata_mutation, make_odata_request, make_service_doc_request
from sf_mcp.decorators import sf_tool
from sf_mcp.dependencies import ApiHost, RequestId, StartTime
from sf_mcp.metadata_parse import MetadataExtractionError, extract_entity_fields
from sf_mcp.server import mcp
from sf_mcp.validation import sanitize_odata_string


@mcp.tool()
@sf_tool("get_mdf_object_definitions")
def get_mdf_object_definitions(
    instance: str,
    data_center: str,
    environment: str,
    auth_user_id: str,
    auth_password: str,
    object_name: str = "",
    *,
    request_id: str = RequestId(),
    start_time: float = StartTime(),
    api_host: str = ApiHost(),
) -> dict[str, Any]:
    """
    List custom MDF objects and their fields from the instance.

    Discovers MDF/generic objects (prefixed with 'cust_') in the system.
    When an object_name is provided, returns detailed field definitions
    including types, nullability, max lengths, and source-limited required analysis.
    This reads OData metadata, not Configure Object Definition. Object-definition
    required settings are unsupported by this tooling; UI/rule requirements are
    unknown. Scalar and navigation annotations are reported independently.

    Args:
        instance: The SuccessFactors instance/company ID
        data_center: SAP data center code (e.g., 'DC55', 'DC10', 'DC4')
        environment: Environment type ('preview', 'production', 'sales_demo')
        auth_user_id: SuccessFactors user ID for authentication (required)
        auth_password: SuccessFactors password for authentication (required)
        object_name: Specific MDF object to inspect (e.g., 'Position', 'cust_myObject'). If empty, lists cust_ objects.
    """
    if object_name:
        # Fetch metadata for a specific MDF object
        safe_name = sanitize_odata_string(object_name)
        metadata = make_metadata_request(
            instance,
            safe_name,
            data_center,
            environment,
            auth_user_id,
            auth_password,
            request_id,
        )

        if metadata is None:
            return {
                "status": "error",
                "error": "metadata_unavailable",
                "message": f"Failed to fetch metadata for MDF object '{object_name}'.",
            }
        if isinstance(metadata, dict) and "error" in metadata:
            return {**metadata, "status": "error"}

        try:
            fields = _extract_mdf_fields(metadata, object_name)
        except MetadataExtractionError as exc:
            return {"object_name": object_name, **exc.as_dict()}
        return {
            "status": "success",
            "object_name": object_name,
            "field_count": len(fields),
            "property_count": sum(f["kind"] == "Property" for f in fields),
            "navigation_property_count": sum(f["kind"] == "NavigationProperty" for f in fields),
            "fields": fields,
        }

    # List all MDF objects by filtering the service document for cust_ entities
    data = make_service_doc_request(
        instance,
        data_center,
        environment,
        auth_user_id,
        auth_password,
        request_id,
    )

    if "error" in data:
        return data

    all_entities = data.get("d", {}).get("EntitySets", [])
    mdf_objects = sorted(e for e in all_entities if e.startswith("cust_"))

    return {
        "mdf_objects": mdf_objects,
        "count": len(mdf_objects),
        "total_entities": len(all_entities),
        "message": "Use object_name parameter to get field details for a specific object.",
    }


def _extract_mdf_fields(metadata: dict, object_name: str) -> list[dict]:
    """Use the shared entity-scoped parser, retaining MDF's list and max_length alias."""
    fields = list(extract_entity_fields(metadata, object_name).values())
    for field in fields:
        if field["maxLength"] is not None:
            field["max_length"] = field["maxLength"]
    return fields


@mcp.tool()
@sf_tool(
    "query_mdf_object",
    max_top=500,
    validate={
        "object_name": "entity_path",
        "filter": "odata_filter",
        "select": "select",
        "orderby": "orderby",
        "effective_date": "date",
    },
)
def query_mdf_object(
    instance: str,
    object_name: str,
    data_center: str,
    environment: str,
    auth_user_id: str,
    auth_password: str,
    select: str = "",
    filter: str = "",
    top: int = 100,
    skip: int = 0,
    orderby: str = "",
    effective_date: str = "",
    from_date: str = "",
    to_date: str = "",
    *,
    request_id: str = RequestId(),
    start_time: float = StartTime(),
    api_host: str = ApiHost(),
) -> dict[str, Any]:
    """
    Query data from any MDF/generic object (e.g., cust_myObject).

    Supports filtering, field selection, and pagination for custom MDF objects
    and standard generic objects. Use get_mdf_object_definitions first to
    discover available objects and their fields.

    Args:
        instance: The SuccessFactors instance/company ID
        object_name: MDF object name (e.g., 'cust_myObject', 'cust_trainingRecord')
        data_center: SAP data center code (e.g., 'DC55', 'DC10', 'DC4')
        environment: Environment type ('preview', 'production', 'sales_demo')
        auth_user_id: SuccessFactors user ID for authentication (required)
        auth_password: SuccessFactors password for authentication (required)
        select: Comma-separated fields (e.g., 'externalCode,cust_field1')
        filter: OData filter (e.g., "cust_status eq 'active'")
        top: Maximum results (default: 100, max: 500)
        skip: Records to skip for pagination (default: 0)
        orderby: Sort order (e.g., 'lastModifiedDateTime desc')
        effective_date: SAP asOfDate (YYYY-MM-DD), mutually exclusive with range.
        from_date: Beginning of SAP effective date range (YYYY-MM-DD).
        to_date: End of SAP effective date range (YYYY-MM-DD).
    """
    safe_name = sanitize_odata_string(object_name)
    endpoint = f"/odata/v2/{safe_name}"

    params: dict[str, str] = {
        "$format": "json",
        "$top": str(top),
    }

    if select:
        params["$select"] = select
    if filter:
        params["$filter"] = filter
    if skip > 0:
        params["$skip"] = str(skip)
    if orderby:
        params["$orderby"] = orderby
    from sf_mcp.validation import validate_date
    if effective_date and (from_date or to_date):
        return {"error": "effective_date cannot be combined with from_date or to_date"}
    try:
        for name, value in (("effective_date", effective_date), ("from_date", from_date), ("to_date", to_date)):
            if value:
                validate_date(value, name)
                if len(value) != 10:
                    raise ValueError(f"Invalid {name}: expected YYYY-MM-DD")
        if from_date and to_date and from_date > to_date:
            raise ValueError("from_date must not be after to_date")
    except ValueError as exc:
        return {"error": str(exc)}
    for name, value in (("asOfDate", effective_date), ("fromDate", from_date), ("toDate", to_date)):
        if value:
            params[name] = value

    result = make_odata_request(
        instance,
        endpoint,
        data_center,
        environment,
        auth_user_id,
        auth_password,
        params,
        request_id,
    )

    if "error" in result:
        return result

    # Extract results
    if "d" in result:
        d = result["d"]
        if "results" in d:
            records = d["results"]
            response: dict[str, Any] = {
                "object_name": object_name,
                "results": records,
                "count": len(records),
            }
            if len(records) >= top:
                response["next_skip"] = skip + top
                response["has_more"] = True
            else:
                response["has_more"] = False
            return response
        else:
            return {"object_name": object_name, "result": d, "count": 1}
    return result


# Well-known foundation object entity mappings
_FOUNDATION_ENTITIES: dict[str, str] = {
    "company": "FOCompany",
    "department": "FODepartment",
    "division": "FODivision",
    "location": "FOLocation",
    "cost_center": "FOCostCenter",
    "job_code": "FOJobCode",
    "job_function": "FOJobFunction",
    "pay_grade": "FOPayGrade",
    "pay_group": "FOPayGroup",
    "business_unit": "FOBusinessUnit",
    "event_reason": "FOEventReason",
    "legal_entity": "FOLegalEntity",
}


@mcp.tool()
@sf_tool("get_foundation_objects", max_top=500, validate={"filter": "odata_filter"})
def get_foundation_objects(
    instance: str,
    object_type: str,
    data_center: str,
    environment: str,
    auth_user_id: str,
    auth_password: str,
    filter: str = "",
    top: int = 100,
    include_inactive: bool = False,
    *,
    request_id: str = RequestId(),
    start_time: float = StartTime(),
    api_host: str = ApiHost(),
) -> dict[str, Any]:
    """
    Query foundation objects (cost centers, departments, divisions, etc.).

    Foundation objects are the building blocks of the org structure in
    SuccessFactors. This tool provides a convenient way to query them
    without needing to know the FO entity names.

    Args:
        instance: The SuccessFactors instance/company ID
        object_type: Type of foundation object. Options: 'company', 'department',
            'division', 'location', 'cost_center', 'job_code', 'job_function',
            'pay_grade', 'pay_group', 'business_unit', 'event_reason', 'legal_entity'
        data_center: SAP data center code (e.g., 'DC55', 'DC10', 'DC4')
        environment: Environment type ('preview', 'production', 'sales_demo')
        auth_user_id: SuccessFactors user ID for authentication (required)
        auth_password: SuccessFactors password for authentication (required)
        filter: Additional OData filter (e.g., "country eq 'US'")
        top: Maximum results (default: 100, max: 500)
        include_inactive: Include inactive/end-dated records (default: false)
    """
    entity_name = _FOUNDATION_ENTITIES.get(object_type.lower())
    if not entity_name:
        return {
            "error": f"Unknown foundation object type: '{object_type}'",
            "valid_types": sorted(_FOUNDATION_ENTITIES.keys()),
        }

    endpoint = f"/odata/v2/{entity_name}"

    # Common FO fields across most foundation objects
    select_fields = (
        "externalCode,name,name_defaultValue,description,"
        "description_defaultValue,status,startDate,endDate,"
        "lastModifiedDateTime"
    )

    filters: list[str] = []
    if not include_inactive:
        filters.append("status eq 'A'")
    if filter:
        filters.append(filter)

    params: dict[str, str] = {
        "$select": select_fields,
        "$format": "json",
        "$top": str(top),
        "$orderby": "name_defaultValue asc",
    }
    if filters:
        params["$filter"] = " and ".join(filters)

    result = make_odata_request(
        instance,
        endpoint,
        data_center,
        environment,
        auth_user_id,
        auth_password,
        params,
        request_id,
        cache_category="metadata",
    )

    if "error" in result:
        return result

    records = result.get("d", {}).get("results", [])

    objects = [
        {
            "external_code": r.get("externalCode"),
            "name": r.get("name_defaultValue") or r.get("name"),
            "description": r.get("description_defaultValue") or r.get("description"),
            "status": r.get("status"),
            "start_date": r.get("startDate"),
            "end_date": r.get("endDate"),
            "last_modified": r.get("lastModifiedDateTime"),
        }
        for r in records
    ]

    # Count by status
    status_counts: dict[str, int] = {}
    for obj in objects:
        s = obj.get("status") or "Unknown"
        status_counts[s] = status_counts.get(s, 0) + 1

    return {
        "object_type": object_type,
        "entity_name": entity_name,
        "objects": objects,
        "count": len(objects),
        "by_status": status_counts,
        "filters_applied": {
            "include_inactive": include_inactive,
            "custom_filter": filter or None,
        },
    }


@mcp.tool()
@sf_tool("upsert_foundation_object")
def upsert_foundation_object(
    instance: str,
    object_type: str,
    external_code: str,
    data_center: str,
    environment: str,
    auth_user_id: str,
    auth_password: str,
    fields: dict[str, Any] | None = None,
    dry_run: bool = True,
    *,
    request_id: str = RequestId(),
    start_time: float = StartTime(),
    api_host: str = ApiHost(),
) -> dict[str, Any]:
    """Preview or POST one foundation object using the get_foundation_objects map.

    Uses POST /odata/v2/upsert?purgeType=incremental with SFOData metadata.
    Sends a JSON array containing one entity, following the conventional SF
    upsert function-import pattern for an UpsertResult collection. No automatic
    retry, lookup, or effective-dating workflow is performed.

    fields contains SF property names, passed through except for these aliases:
    start_date -> startDate, end_date -> endDate, name_default_value ->
    name_defaultValue, description_default_value -> description_defaultValue.
    name and description remain unchanged, as in GET. Supply start_date in the
    tenant's OData date format and other required properties when needed.
    external_code and generated metadata override fields. No dates are inferred.

    dry_run defaults to True (no HTTP). Real writes require SF_ALLOW_WRITE=true;
    there is no environment whitelist. Returns the M0 preview or SF response.
    """
    entity_name = _FOUNDATION_ENTITIES.get(object_type.lower())
    if not entity_name:
        return {
            "error": f"Unknown foundation object type: '{object_type}'",
            "valid_types": sorted(_FOUNDATION_ENTITIES.keys()),
        }
    if not external_code.strip():
        return {"error": "external_code must not be empty"}

    aliases = {
        "start_date": "startDate",
        "end_date": "endDate",
        "name_default_value": "name_defaultValue",
        "description_default_value": "description_defaultValue",
    }
    payload = {aliases.get(key, key): value for key, value in (fields or {}).items()}
    payload["externalCode"] = external_code
    payload["__metadata"] = {"uri": entity_name, "type": f"SFOData.{entity_name}"}
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
