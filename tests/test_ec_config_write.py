"""EC configuration writes through the registered tools, without live SF."""

import asyncio
from unittest.mock import patch

import pytest

from sf_mcp.server import mcp
from sf_mcp.tools import mdf, query

COMMON = {
    "instance": "test-instance",
    "data_center": "DC55",
    "environment": "production",
    "auth_user_id": "admin",
    "auth_password": "test-password",
}
CASES = [
    (mdf, "upsert_foundation_object", {"object_type": "department", "external_code": "D1"}, "FODepartment"),
    (query, "upsert_picklist_value", {"picklist_id": "colors", "external_code": "red", "label": "Red"},
     "PickListValueV2"),
]


def invoke(module, name, args, **kwargs):
    tool = getattr(module, name)
    return getattr(tool, "fn", tool)(**COMMON, **args, **kwargs)


@pytest.mark.parametrize("module,name,args,entity", CASES)
def test_default_dry_run_delegates(module, name, args, entity):
    preview = {"dry_run": True, "endpoint": "/odata/v2/upsert"}
    with patch.object(module, "make_odata_mutation", return_value=preview) as mutate:
        assert invoke(module, name, args) == preview
    assert mutate.call_args.args[1] == preview["endpoint"]
    assert mutate.call_args.kwargs["dry_run"] is True
    assert mutate.call_args.kwargs["method"] == "POST"
    assert mutate.call_args.kwargs["params"] == {"purgeType": "incremental"}
    payload = mutate.call_args.kwargs["payload"]
    assert isinstance(payload, list) and len(payload) == 1
    assert payload[0]["__metadata"] == {"uri": entity, "type": f"SFOData.{entity}"}
    assert mutate.call_args.kwargs["request_id"]


@pytest.mark.parametrize("module,name,args,entity", CASES)
@pytest.mark.parametrize("enabled", ["true", "false"])
def test_preview_never_uses_http(module, name, args, entity, enabled, monkeypatch):
    monkeypatch.setenv("SF_ALLOW_WRITE", enabled)
    with patch("sf_mcp.client.get_session") as session:
        result = invoke(module, name, args)
    session.assert_not_called()
    assert result["dry_run"] is True
    assert result["allow_write"] is (enabled == "true")
    assert result["endpoint"] == "/odata/v2/upsert"
    assert result["params"] == {"purgeType": "incremental"}


@pytest.mark.parametrize("module,name,args,entity", CASES)
def test_blocked_write_no_http(module, name, args, entity, monkeypatch):
    monkeypatch.setenv("SF_ALLOW_WRITE", "false")
    with patch("sf_mcp.client.get_session") as session:
        result = invoke(module, name, args, dry_run=False)
    session.assert_not_called()
    assert "SF_ALLOW_WRITE=true" in result["error"]


@pytest.mark.parametrize("module,name,args,entity", CASES)
def test_enabled_write(module, name, args, entity, monkeypatch):
    monkeypatch.setenv("SF_ALLOW_WRITE", "true")
    with patch("sf_mcp.client.get_session") as session:
        response = session.return_value.request.return_value
        response.status_code = 200
        response.text = '{"d": [{"status": "OK", "httpCode": 200}]}'
        response.json.return_value = {"d": [{"status": "OK", "httpCode": 200}]}
        assert invoke(module, name, args, dry_run=False) == response.json.return_value
    call = session.return_value.request.call_args
    assert call.args == ("POST", "https://api55.sapsf.eu/odata/v2/upsert")
    assert call.kwargs["params"] == {"purgeType": "incremental"}
    assert isinstance(call.kwargs["json"], list) and len(call.kwargs["json"]) == 1
    assert call.kwargs["json"][0]["externalCode"] == args["external_code"]
    assert call.kwargs["json"][0]["__metadata"]["type"] == f"SFOData.{entity}"
    session.return_value.request.assert_called_once()


def test_unknown_foundation_type():
    with patch.object(mdf, "make_odata_mutation") as mutate:
        result = invoke(mdf, "upsert_foundation_object", {"object_type": "User", "external_code": "1"})
    assert "Unknown foundation object type" in result["error"]
    assert result["valid_types"] == sorted(mdf._FOUNDATION_ENTITIES)
    mutate.assert_not_called()


@pytest.mark.parametrize("object_type,entity", mdf._FOUNDATION_ENTITIES.items())
def test_shared_foundation_map_and_fields(object_type, entity):
    fields = {"start_date": "/Date(0)/", "name_default_value": "Example", "cust_extra": "x",
              "externalCode": "wrong", "__metadata": {"uri": "User"}}
    result = invoke(mdf, "upsert_foundation_object",
                    {"object_type": object_type.upper(), "external_code": "O'Brien"}, fields=fields)
    assert result["endpoint"] == "/odata/v2/upsert"
    assert result["params"] == {"purgeType": "incremental"}
    assert result["payload"] == [{
        "startDate": "/Date(0)/", "name_defaultValue": "Example", "cust_extra": "x",
        "externalCode": "O'Brien", "__metadata": {"uri": entity, "type": f"SFOData.{entity}"},
    }]
    assert fields["externalCode"] == "wrong"
    assert "startDate" not in fields


def test_picklist_fields_and_literal_ids():
    result = invoke(query, "upsert_picklist_value", {
        "picklist_id": "O'Brien", "external_code": "r'ed", "label": "Rouge", "locale": "fr-FR",
    }, status="A", sort_order=0, fields={"label_en_US": "Red", "PickListV2_effectiveStartDate": "/Date(0)/"})
    assert result["payload"] == [{
        "PickListV2_id": "O'Brien", "externalCode": "r'ed", "label_fr_FR": "Rouge",
        "label_en_US": "Red", "status": "A", "sortOrder": 0,
        "PickListV2_effectiveStartDate": "/Date(0)/",
        "__metadata": {"uri": "PickListValueV2", "type": "SFOData.PickListValueV2"},
    }]


def test_optional_fields_omitted():
    result = invoke(query, "upsert_picklist_value", {"picklist_id": "colors", "external_code": "red"})
    assert set(result["payload"][0]) == {"PickListV2_id", "externalCode", "__metadata"}


def test_invalid_locale_no_mutation():
    with patch.object(query, "make_odata_mutation") as mutate:
        result = invoke(query, "upsert_picklist_value", CASES[1][2], locale="../invalid")
    assert "error" in result
    mutate.assert_not_called()


def test_tools_registered():
    tools = asyncio.run(mcp.get_tools())
    for name in ("upsert_foundation_object", "upsert_picklist_value"):
        schema = tools[name].parameters
        assert schema["properties"]["dry_run"]["default"] is True
        assert "request_id" not in schema["properties"]
