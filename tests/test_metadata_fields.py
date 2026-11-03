"""Offline metadata regression tests; HTTP is mocked and live requests prohibited."""

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest

from sf_mcp.metadata_parse import MetadataExtractionError, extract_entity_fields
from sf_mcp.server import mcp
from sf_mcp.tools import configuration, mdf
from sf_mcp.xml_utils import xml_to_dict

FIXTURES = Path(__file__).parent / "fixtures" / "metadata"
SAP = "@{http://www.successfactors.com/edm/sap}"
COMMON = {
    "instance": "test-instance",
    "data_center": "DC55",
    "environment": "production",
    "auth_user_id": "admin",
    "auth_password": "test-password",
}
TOOLS = [
    (configuration, "get_configuration", "entity"),
    (mdf, "get_mdf_object_definitions", "object_name"),
]


@pytest.fixture(autouse=True)
def no_live_http(monkeypatch):
    def blocked(*args, **kwargs):
        pytest.fail("Live HTTP is prohibited in metadata tests")
    monkeypatch.setattr("requests.sessions.Session.request", blocked)


def document(*entities):
    return {"Edmx": {"DataServices": {"Schema": {"EntityType": list(entities)}}}}


def invoke(module, name, parameter, entity="Position"):
    tool = getattr(module, name)
    return getattr(tool, "fn", tool)(**COMMON, **{parameter: entity})


def field_map(result):
    fields = result["fields"]
    return {field["name"]: field for field in fields} if isinstance(fields, list) else fields


@pytest.mark.parametrize("fixture", ["position_dual_schema.xml", "position_single_schema.xml"])
@pytest.mark.parametrize("module,name,parameter", TOOLS)
def test_real_structure_fields_and_required_levels(fixture, module, name, parameter):
    metadata = xml_to_dict((FIXTURES / fixture).read_bytes())
    with patch.object(module, "make_metadata_request", return_value=metadata) as request:
        result = invoke(module, name, parameter)
    request.assert_called_once()
    assert request.call_args.args[1] == "Position"
    assert result["status"] == "success"
    fields = field_map(result)
    assert result["field_count"] == len(fields) > 0
    assert result["property_count"] > 0
    assert result["navigation_property_count"] > 0
    assert result["field_count"] == result["property_count"] + result["navigation_property_count"]
    scalar, nav = fields["cust_Type"], fields["cust_TypeNav"]
    assert scalar["kind"] == "Property"
    assert scalar["type"] == "Edm.String"
    assert scalar["nullable"] == "true"
    assert scalar["maxLength"] == "128"
    assert scalar["sap"] == {
        "required": "false", "creatable": "true", "updatable": "true",
        "upsertable": "true", "visible": "true", "picklist": "employeeClass", "label": "Position Class",
    }
    assert scalar["api_odata"]["required"] is False
    assert scalar["api_odata"]["status"] == "optional"
    assert scalar["required_mapping_exception"]["picklist_identified"] is True
    assert "Generic Object" in scalar["required_mapping_exception"]["applies_to_mdf_types"]
    assert "User" in scalar["required_mapping_exception"]["applies_to_mdf_types"]
    assert nav["kind"] == nav["type"] == "NavigationProperty"
    assert nav["relationship"] == "SFOData.cust_TypeNav_of_Position"
    assert nav["api_odata"]["required"] is False
    for field in fields.values():
        sources = field["required_sources"]
        assert sources["api_odata"] == field["api_odata"]
        assert sources["object_definition"]["availability"] == "unsupported"
        for level in ("object_definition", "ui_or_rule"):
            assert sources[level]["status"] == "unknown"
            assert sources[level]["required"] is None
    if module is mdf:
        assert scalar["max_length"] == scalar["maxLength"]


@pytest.mark.parametrize("module,name,parameter", TOOLS)
def test_multiple_entities_are_not_merged(module, name, parameter):
    metadata = document(
        {"@Name": "Other", "Property": {"@Name": "otherOnly", "@Type": "Edm.Int32"}},
        {"@Name": "Position", "Property": {"@Name": "code", "@Type": "Edm.String"}},
    )
    with patch.object(module, "make_metadata_request", return_value=metadata):
        result = invoke(module, name, parameter)
        wrong_case = invoke(module, name, parameter, "position")
    assert set(field_map(result)) == {"code"}
    assert wrong_case["error"] == "entity_not_found"


def test_scalar_and_navigation_required_are_independent():
    # Synthetic contrast case, separate from the real cust_Type fixture semantics.
    metadata = document({
        "@Name": "cust_Example",
        "Property": {"@Name": "choice", "@Type": "Edm.String", "@Nullable": "true",
                     SAP + "picklist": "choices", SAP + "required": "false"},
        "NavigationProperty": {"@Name": "choiceNav", "@Relationship": "SFOData.choiceAssociation",
                               SAP + "required": "true"},
    })
    fields = extract_entity_fields(metadata, "cust_Example")
    assert fields["choice"]["api_odata"]["required"] is False
    assert fields["choiceNav"]["api_odata"]["required"] is True
    assert fields["choiceNav"]["required_sources"]["ui_or_rule"]["required"] is None
    assert configuration._extract_entity_fields(metadata, "cust_Example") == fields
    assert {f["name"]: f for f in mdf._extract_mdf_fields(metadata, "cust_Example")} == fields


@pytest.mark.parametrize("attrs,required,basis", [
    ({SAP + "required": "true"}, True, "sap:required"),
    ({SAP + "required": "false"}, False, "sap:required"),
    ({"@Nullable": "false"}, True, "Nullable"),
    ({"@Nullable": "true"}, False, "Nullable"),
    ({SAP + "required": "false", "@Nullable": "false"}, False, "sap:required"),
    ({SAP + "required": "true", "@Nullable": "true"}, True, "sap:required"),
    ({}, None, None),
    ({SAP + "required": "invalid", "@Nullable": "true"}, None, None),
    ({"@Nullable": "invalid"}, None, None),
    ({"@{urn:unrelated}required": "false"}, None, None),
])
def test_required_optional_unknown(attrs, required, basis):
    fields = extract_entity_fields(document({
        "@Name": "Position", "Property": {"@Name": "example", "@Type": "Edm.String", **attrs},
    }), "Position")
    api = fields["example"]["api_odata"]
    assert api["required"] is required
    assert api["basis"] == basis
    assert api["status"] == ("unknown" if required is None else "required" if required else "optional")


@pytest.mark.parametrize("prefix", ["edmx:", "{urn:edmx}"])
def test_prefixed_and_clark_element_keys(prefix):
    metadata = {prefix + "Edmx": {prefix + "DataServices": {"edm:Schema": {
        "edm:EntityType": {"@Name": "Position", "edm:Property": {
            "@Name": "code", "@Type": "Edm.String", "@sap:required": "true",
        }},
    }}}}
    assert extract_entity_fields(metadata, "Position")["code"]["api_odata"]["required"] is True


BAD_METADATA = [
    ({}, "invalid_metadata"),
    ([], "invalid_metadata"),
    ("not a parsed document", "invalid_metadata"),
    ({"html": "login page"}, "invalid_metadata"),
    ({"Edmx": None}, "no_entity_types"),
    ({"Edmx": {"DataServices": {"Schema": {"EntityContainer": {}}}}}, "no_entity_types"),
    (document({"Property": {"@Name": "orphan"}}), "no_entity_types"),
    (document({"@Name": "User", "Property": {"@Name": "userId"}}), "entity_not_found"),
    (document({"@Name": "Position"}), "no_fields"),
    (document({"@Name": "Position", "Property": "broken"}), "invalid_field"),
    (document({"@Name": "Position"}, {"@Name": "Position"}), "ambiguous_entity"),
    (document({"@Name": "Position", "Property": [{"@Name": "code"}, {"@Name": "code"}]}), "duplicate_field"),
]


@pytest.mark.parametrize("metadata,code", BAD_METADATA)
@pytest.mark.parametrize("module,name,parameter", TOOLS)
def test_bad_metadata_is_not_empty_success(metadata, code, module, name, parameter):
    with pytest.raises(MetadataExtractionError) as error:
        extract_entity_fields(metadata, "Position")
    assert error.value.code == code
    with patch.object(module, "make_metadata_request", return_value=metadata):
        result = invoke(module, name, parameter)
    assert result["status"] in ("error", "incomplete")
    assert result["error"] == code
    assert result["message"]
    assert "field_count" not in result


@pytest.mark.parametrize("module,name,parameter", TOOLS)
def test_unavailable_response_does_not_claim_entity_missing(module, name, parameter):
    with patch.object(module, "make_metadata_request", return_value=None):
        result = invoke(module, name, parameter)
    assert result["error"] == "metadata_unavailable"
    assert result["status"] == "error"


@pytest.mark.parametrize("status,body,error", [
    (401, "Unauthorized", "HTTP 401"),
    (403, "Forbidden", "HTTP 403"),
    (500, "Internal server error", "HTTP 500"),
    (200, "", "Empty response from API"),
    (200, "<Edmx", "XML parse error"),
    (200, "<html>login</html>", "invalid_metadata"),
])
@pytest.mark.parametrize("module,name,parameter", TOOLS)
def test_client_errors_remain_errors(status, body, error, module, name, parameter):
    with patch("sf_mcp.client.get_session") as session:
        response = session.return_value.get.return_value
        response.status_code, response.text = status, body
        result = invoke(module, name, parameter)
    assert result["status"] == "error"
    assert result["error"].startswith(error)
    assert "fields" not in result
    session.return_value.get.assert_called_once()
    session.return_value.request.assert_not_called()


def compare(metadata1, metadata2):
    tool = configuration.compare_configurations
    with patch.object(configuration, "make_metadata_request", side_effect=[metadata1, metadata2]):
        return getattr(tool, "fn", tool)(
            instance1="one", instance2="two", entity="Position", data_center1="DC55",
            environment1="production", data_center2="DC55", environment2="production",
            auth_user_id="admin", auth_password="test-password",
        )


def test_comparison_uses_requested_entity_and_rejects_empty():
    position = {"@Name": "Position", "Property": {"@Name": "code", "@Type": "Edm.String"}}
    first = document(position, {"@Name": "User", "Property": {"@Name": "unrelated"}})
    second = document(position)
    result = compare(first, second)
    assert result["summary"]["is_identical"] is True
    assert result["instance1"]["field_count"] == 1
    for bad in (document({"@Name": "Position"}), {}):
        result = compare(first, bad)
        assert result["status"] in ("error", "incomplete")
        assert "comparison" not in result


def test_tools_remain_registered_with_required_scope_documented():
    async def check():
        for _, name, _ in TOOLS:
            tool = await mcp.get_tool(name)
            assert "unknown" in tool.description
            assert "required" in tool.description
    asyncio.run(check())
