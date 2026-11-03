"""Entity-scoped OData metadata extraction and source-limited required analysis."""

from typing import Any

SAP_NAMESPACES = (
    "http://www.successfactors.com/edm/sap",
    "http://www.sap.com/Protocols/SAPData",
)
MAPPING_NOTE = (
    "SAP Mapping of Edit Attributes: scalar properties mapped from Picklist, Generic Object, "
    "or User can have sap:required=false even when the MDF field is required. Property and "
    "NavigationProperty annotations are independent. OData optional never establishes UI/rule optional."
)


class MetadataExtractionError(ValueError):
    """A metadata document cannot provide a complete field inventory."""

    def __init__(self, code: str, message: str, status: str = "incomplete"):
        super().__init__(message)
        self.code = code
        self.status = status

    def as_dict(self) -> dict[str, str]:
        return {"status": self.status, "error": self.code, "message": str(self)}


def _nodes(parent: Any, name: str) -> list:
    """Accept xml_to_dict local element names and prefixed/Clark equivalents."""
    if not isinstance(parent, dict):
        return []
    nodes = []
    for key, value in parent.items():
        if isinstance(key, str) and not key.startswith("@") and key.rsplit("}", 1)[-1].split(":")[-1] == name:
            nodes.extend(value if isinstance(value, list) else [value])
    return nodes


def _sap(node: dict, name: str) -> Any:
    # Do not accept arbitrary namespaces merely because the local name matches.
    for namespace in SAP_NAMESPACES:
        key = f"@{{{namespace}}}{name}"
        if key in node:
            return node[key]
    return node.get(f"@sap:{name}")


def _boolean(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return {"true": True, "false": False}.get(value.strip().lower())
    return None


def _requirements(node: dict) -> dict[str, Any]:
    sap_required = _boolean(_sap(node, "required"))
    nullable = _boolean(node.get("@Nullable"))
    # Preserve explicit edit-attribute mapping, even if Nullable is false.
    # Nullability is a fallback constraint, not proof of payload acceptance.
    required = sap_required
    basis = "sap:required" if sap_required is not None else None
    if basis is None and _sap(node, "required") is None and nullable is not None:
        required, basis = not nullable, "Nullable"
    status = "unknown" if required is None else ("required" if required else "optional")
    notes = [
        "API/edit-attribute mapping only; not server-side payload validation or UI/rule requirements.",
        MAPPING_NOTE,
    ]
    if sap_required is not None and nullable is not None and sap_required == nullable:
        notes.append("sap:required and Nullable describe different constraints; both are retained, "
                     "with sap:required taking precedence in this classification.")
    api = {
        "availability": "available" if basis else "unknown",
        "status": status,
        "required": required,
        "basis": basis,
        "sap_required": sap_required,
        "nullable": nullable,
        "notes": notes,
    }
    return {
        "api_odata": api,
        "required_sources": {
            "api_odata": api.copy(),
            "object_definition": {
                "availability": "unsupported",
                "status": "unknown",
                "required": None,
                "notes": ["This tooling has no supported Configure Object Definition read API/export source; "
                          "OData metadata does not establish the MDF definition's required setting."],
            },
            "ui_or_rule": {
                "availability": "unknown",
                "status": "unknown",
                "required": None,
                "notes": ["No business-rule or page export source is available."],
            },
        },
        "required_mapping_exception": {
            "applies_to_mdf_types": ["Picklist", "Generic Object", "User"],
            "picklist_identified": bool(_sap(node, "picklist")),
            "notes": [MAPPING_NOTE,
                      "Edm.String alone does not identify an MDF Generic Object or User field."],
        },
    }


def extract_entity_fields(metadata: Any, entity: str) -> dict[str, dict]:
    """Walk every Schema, match exact @Name, and keep scalar/nav evidence separate.

    Raises MetadataExtractionError instead of returning a misleading empty success.
    Missing annotations remain None; no implicit Nullable default is applied.
    """
    roots = _nodes(metadata, "Edmx")
    if not roots:
        raise MetadataExtractionError("invalid_metadata", "Expected a parsed Edmx metadata document.", "error")
    entity_types = [
        et
        for root in roots
        for services in _nodes(root, "DataServices")
        for schema in _nodes(services, "Schema")
        for et in _nodes(schema, "EntityType")
        if isinstance(et, dict) and isinstance(et.get("@Name"), str) and et["@Name"]
    ]
    if not entity_types:
        raise MetadataExtractionError("no_entity_types", "Edmx present, but no parseable EntityType definitions found.")
    matches = [et for et in entity_types if et["@Name"] == entity]
    if not matches:
        raise MetadataExtractionError("entity_not_found", f"Entity '{entity}' not found in metadata.")
    if len(matches) != 1:
        raise MetadataExtractionError("ambiguous_entity", f"Multiple EntityType definitions named '{entity}'.")

    fields = {}
    for kind in ("Property", "NavigationProperty"):
        for node in _nodes(matches[0], kind):
            if not isinstance(node, dict) or not isinstance(node.get("@Name"), str) or not node["@Name"]:
                raise MetadataExtractionError("invalid_field", f"Malformed {kind} in entity '{entity}'.")
            name = node["@Name"]
            if name in fields:
                raise MetadataExtractionError("duplicate_field", f"Duplicate field '{name}' in entity '{entity}'.")
            is_nav = kind == "NavigationProperty"
            field = {
                "name": name,
                "kind": kind,
                "type": "NavigationProperty" if is_nav else node.get("@Type", "unknown"),
                "nullable": node.get("@Nullable"),
                "maxLength": node.get("@MaxLength"),
                "sap": {key: _sap(node, key) for key in (
                    "required", "creatable", "updatable", "upsertable", "visible", "picklist", "label",
                )},
                **_requirements(node),
            }
            if is_nav:
                field.update(relationship=node.get("@Relationship"),
                             from_role=node.get("@FromRole"), to_role=node.get("@ToRole"))
            fields[name] = field
    if not fields:
        raise MetadataExtractionError("no_fields", f"Edmx contains entity '{entity}', but no fields were parsed.")
    return fields
