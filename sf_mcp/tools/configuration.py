"""Configuration and metadata tools: get_configuration, compare_configurations, list_entities."""

import time
import uuid
from typing import Any

from sf_mcp.client import make_metadata_request, make_service_doc_request
from sf_mcp.decorators import sf_tool
from sf_mcp.dependencies import ApiHost, RequestId, StartTime
from sf_mcp.logging_config import audit_log
from sf_mcp.metadata_parse import MetadataExtractionError, extract_entity_fields
from sf_mcp.server import mcp
from sf_mcp.validation import validate_identifier


def _extract_entity_fields(metadata: dict, entity: str) -> dict[str, dict]:
    """Extract only the requested entity, including separately typed navigation fields."""
    return extract_entity_fields(metadata, entity)


def _categorize_entities(entities: list[str]) -> dict[str, list[str]]:
    """Group OData entity names by category."""
    categories = {
        "foundation": [],
        "employee": [],
        "talent": [],
        "platform": [],
        "other": [],
    }
    for entity in entities:
        entity_lower = entity.lower()
        if entity.startswith("FO") or entity.startswith("fo"):
            categories["foundation"].append(entity)
        elif any(entity_lower.startswith(p) for p in ["emp", "per", "user", "person"]):
            categories["employee"].append(entity)
        elif any(entity_lower.startswith(p) for p in ["goal", "performance", "learning", "competency", "talent"]):
            categories["talent"].append(entity)
        elif any(entity_lower.startswith(p) for p in ["rbp", "picklist", "background", "photo", "attachment"]):
            categories["platform"].append(entity)
        else:
            categories["other"].append(entity)
    return categories


@mcp.tool()
@sf_tool("get_configuration")
def get_configuration(
    instance: str,
    entity: str,
    data_center: str,
    environment: str,
    auth_user_id: str,
    auth_password: str,
    *,
    request_id: str = RequestId(),
    start_time: float = StartTime(),
    api_host: str = ApiHost(),
) -> dict[str, Any]:
    """
    Retrieve OData entity metadata/configuration from a SuccessFactors instance.

    This tool fetches the $metadata document for a specific entity, showing
    all available scalar/navigation fields, types, and OData required annotations.
    Required analysis separates api_odata, object_definition (unsupported by this
    tooling), and ui_or_rule (unknown). OData optional does not mean UI optional.

    Args:
        instance: The SuccessFactors instance/company ID
        entity: OData entity to inspect (e.g., "User", "EmpEmployment", "Position")
        data_center: SAP data center code (e.g., 'DC55', 'DC10', 'DC4')
        environment: Environment type ('preview', 'production', 'sales_demo')
        auth_user_id: SuccessFactors user ID for authentication (required)
        auth_password: SuccessFactors password for authentication (required)

    Returns:
        dict containing entity metadata with field definitions
    """
    metadata = make_metadata_request(
        instance,
        entity,
        data_center,
        environment,
        auth_user_id,
        auth_password,
        request_id,
    )

    if metadata is None:
        return {"status": "error", "error": "metadata_unavailable",
                "message": f"Failed to fetch metadata for entity '{entity}'"}
    if isinstance(metadata, dict) and "error" in metadata:
        return {**metadata, "status": "error"}

    try:
        fields = _extract_entity_fields(metadata, entity)
    except MetadataExtractionError as exc:
        return {"entity": entity, "instance": instance, **exc.as_dict()}

    return {
        "status": "success",
        "entity": entity,
        "instance": instance,
        "field_count": len(fields),
        "property_count": sum(f["kind"] == "Property" for f in fields.values()),
        "navigation_property_count": sum(f["kind"] == "NavigationProperty" for f in fields.values()),
        "fields": fields,
    }


@mcp.tool()
def compare_configurations(
    instance1: str,
    instance2: str,
    entity: str,
    data_center1: str,
    environment1: str,
    data_center2: str,
    environment2: str,
    auth_user_id: str,
    auth_password: str,
) -> dict[str, Any]:
    """
    Compare entity configuration/metadata between two SuccessFactors instances.

    This is useful for verifying that dev/test/production environments are aligned
    before deployments, or for auditing configuration drift.

    Args:
        instance1: First SF instance/company ID (e.g., dev instance)
        instance2: Second SF instance/company ID (e.g., prod instance)
        entity: OData entity to compare (e.g., "User", "EmpEmployment", "Position")
        data_center1: SAP data center for instance1 (e.g., 'DC55')
        environment1: Environment for instance1 ('preview', 'production')
        data_center2: SAP data center for instance2 (e.g., 'DC55')
        environment2: Environment for instance2 ('preview', 'production')
        auth_user_id: SuccessFactors user ID for authentication (required, used for both instances)
        auth_password: SuccessFactors password for authentication (required, used for both instances)

    Returns:
        dict containing comparison results with match percentage and field differences
    """
    request_id = str(uuid.uuid4())[:8]
    start_time_val = time.time()

    audit_log(
        event_type="tool_invocation",
        tool_name="compare_configurations",
        instance=instance1,
        status="started",
        details={"instance1": instance1, "instance2": instance2, "entity": entity},
        request_id=request_id,
    )

    try:
        validate_identifier(instance1, "instance1")
        validate_identifier(instance2, "instance2")
    except ValueError as e:
        audit_log(
            event_type="validation_error",
            tool_name="compare_configurations",
            instance=instance1,
            status="failure",
            details={"error": str(e)},
            request_id=request_id,
            duration_ms=(time.time() - start_time_val) * 1000,
        )
        return {"error": str(e)}

    # Fetch metadata from both instances
    metadata1 = make_metadata_request(
        instance1,
        entity,
        data_center1,
        environment1,
        auth_user_id,
        auth_password,
        request_id,
    )
    metadata2 = make_metadata_request(
        instance2,
        entity,
        data_center2,
        environment2,
        auth_user_id,
        auth_password,
        request_id,
    )

    if metadata1 is None or (isinstance(metadata1, dict) and "error" in metadata1):
        return {"error": f"Failed to fetch metadata from instance1 ({instance1})"}
    if metadata2 is None or (isinstance(metadata2, dict) and "error" in metadata2):
        return {"error": f"Failed to fetch metadata from instance2 ({instance2})"}

    try:
        fields1 = _extract_entity_fields(metadata1, entity)
        fields2 = _extract_entity_fields(metadata2, entity)
    except MetadataExtractionError as exc:
        return {"entity": entity, **exc.as_dict()}

    fields1_names = set(fields1.keys())
    fields2_names = set(fields2.keys())

    only_in_1 = sorted(fields1_names - fields2_names)
    only_in_2 = sorted(fields2_names - fields1_names)
    in_both = fields1_names & fields2_names

    type_differences = []
    for field in sorted(in_both):
        if fields1[field]["type"] != fields2[field]["type"]:
            type_differences.append(
                {
                    "field": field,
                    f"{instance1}_type": fields1[field]["type"],
                    f"{instance2}_type": fields2[field]["type"],
                }
            )

    total_unique_fields = len(fields1_names | fields2_names)
    matching_fields = len(in_both) - len(type_differences)
    match_percentage = round((matching_fields / total_unique_fields * 100), 1) if total_unique_fields > 0 else 100

    audit_log(
        event_type="tool_invocation",
        tool_name="compare_configurations",
        instance=instance1,
        status="success",
        request_id=request_id,
        duration_ms=(time.time() - start_time_val) * 1000,
    )

    return {
        "entity": entity,
        "instance1": {
            "name": instance1,
            "data_center": data_center1,
            "environment": environment1,
            "field_count": len(fields1),
        },
        "instance2": {
            "name": instance2,
            "data_center": data_center2,
            "environment": environment2,
            "field_count": len(fields2),
        },
        "comparison": {
            "fields_only_in_instance1": only_in_1,
            "fields_only_in_instance2": only_in_2,
            "fields_in_both": len(in_both),
            "type_differences": type_differences,
            "match_percentage": match_percentage,
        },
        "summary": {
            "is_identical": len(only_in_1) == 0 and len(only_in_2) == 0 and len(type_differences) == 0,
            "differences_found": len(only_in_1) + len(only_in_2) + len(type_differences),
        },
    }


@mcp.tool()
@sf_tool("list_entities")
def list_entities(
    instance: str,
    data_center: str,
    environment: str,
    auth_user_id: str,
    auth_password: str,
    category: str | None = None,
    *,
    request_id: str = RequestId(),
    start_time: float = StartTime(),
    api_host: str = ApiHost(),
) -> dict[str, Any]:
    """
    List all available OData entities in the SuccessFactors instance.

    This discovery tool helps users understand what data is available to query.
    It fetches the service document which lists all entity sets.

    Args:
        instance: The SuccessFactors instance/company ID
        data_center: SAP data center code (e.g., 'DC55', 'DC10', 'DC4')
        environment: Environment type ('preview', 'production', 'sales_demo')
        auth_user_id: SuccessFactors user ID for authentication (required)
        auth_password: SuccessFactors password for authentication (required)
        category: Optional filter - 'foundation', 'employee', 'talent', 'platform', 'all' (default: all)

    Returns:
        dict containing entity list, count, and optional category breakdown
    """
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

    # SuccessFactors deployments return either the OData V2 verbose envelope
    # or a top-level service document. Do not silently claim zero entities when
    # a server returns an unexpected representation.
    container = data.get("d", data)
    if not isinstance(container, dict) or not isinstance(container.get("EntitySets"), list):
        return {"error": "invalid_service_document", "message": "EntitySets list missing from service document"}
    raw_entities = container["EntitySets"]
    if not all(isinstance(item, str) for item in raw_entities):
        return {"error": "invalid_service_document", "message": "EntitySets must contain entity names"}
    entities = sorted(set(raw_entities))

    categories_dict = _categorize_entities(entities)

    if category and category.lower() != "all":
        filtered_entities = categories_dict.get(category.lower(), entities)
    else:
        filtered_entities = entities

    response_data = {
        "instance": instance,
        "data_center": data_center,
        "environment": environment,
        "entities": filtered_entities,
        "count": len(filtered_entities),
        "total_available": len(entities),
    }

    if not category or category.lower() == "all":
        response_data["by_category"] = {k: len(v) for k, v in categories_dict.items()}

    return response_data


@mcp.tool()
@sf_tool("get_entity_catalog", max_top=50)
def get_entity_catalog(instance: str, data_center: str, environment: str, auth_user_id: str,
                       auth_password: str, category: str = "all", skip: int = 0, top: int = 20, *,
                       request_id: str = RequestId(), start_time: float = StartTime(),
                       api_host: str = ApiHost()) -> dict[str, Any]:
    """Build a paged tenant-specific entity inventory from service document and metadata.

    Field upsert annotations describe metadata only, not actual permission or UI rules.
    Categories use name heuristics and do not prove an entity belongs to EC.
    """
    if skip < 0 or top < 1:
        return {"error": "skip must be nonnegative and top must be positive"}
    service = make_service_doc_request(instance, data_center, environment, auth_user_id, auth_password, request_id)
    if "error" in service:
        return service
    container = service.get("d", service)
    if not isinstance(container, dict) or not isinstance(container.get("EntitySets"), list):
        return {"error": "invalid_service_document", "message": "EntitySets list missing from service document"}
    entities = container["EntitySets"]
    if not all(isinstance(x, str) for x in entities):
        return {"error": "invalid_service_document", "message": "EntitySets must contain entity names"}
    entities = sorted(set(entities))
    groups = _categorize_entities(entities)
    if category != "all":
        if category not in groups:
            return {"error": "Unknown category", "valid_categories": ["all", *groups]}
        entities = groups[category]
    rows = []
    for entity in entities[skip:skip + top]:
        metadata = make_metadata_request(instance, entity, data_center, environment, auth_user_id, auth_password, request_id)
        if not isinstance(metadata, dict) or "error" in metadata:
            rows.append({"entity": entity, "metadata_status": "unavailable"})
            continue
        try:
            fields = extract_entity_fields(metadata, entity)
            properties = [x for x in fields.values() if x.get("kind") == "Property"]
            rows.append({"entity": entity, "metadata_status": "available", "property_count": len(properties),
                         "upsertable_property_count": sum(x.get("sap", {}).get("upsertable") == "true" for x in properties),
                         "note": "Field annotations do not establish entity write support or user permission."})
        except MetadataExtractionError as exc:
            rows.append({"entity": entity, "metadata_status": "incomplete", "error": exc.as_dict()})
    next_skip = skip + len(rows)
    return {"instance": instance, "data_center": data_center, "environment": environment, "category": category,
            "total": len(entities), "skip": skip, "results": rows,
            "next_skip": next_skip if next_skip < len(entities) else None}
