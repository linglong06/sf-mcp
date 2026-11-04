"""Regression tests for service discovery, dated queries and EC related data."""

import asyncio
from unittest.mock import patch

import pytest

from sf_mcp.cache import ResponseCache
from sf_mcp.client import make_service_doc_request
from sf_mcp.server import mcp
from sf_mcp.tools import configuration, ec_related, mdf, query

COMMON = {"instance": "test", "data_center": "DC55", "environment": "preview",
          "auth_user_id": "admin", "auth_password": "secret"}


def call(fn, **kwargs):
    return getattr(fn, "fn", fn)(**COMMON, **kwargs)


@pytest.mark.parametrize("service", [{"EntitySets": ["User", "EmpJob"]},
                                     {"d": {"EntitySets": ["User", "EmpJob"]}}])
def test_service_document_variants(service):
    with patch("sf_mcp.tools.configuration.make_service_doc_request", return_value=service):
        result = call(configuration.list_entities)
    assert result["entities"] == ["EmpJob", "User"]
    assert result["instance"] == "test"


def test_unexpected_service_document_is_not_silently_empty():
    with patch("sf_mcp.tools.configuration.make_service_doc_request", return_value={"unexpected": []}):
        assert call(configuration.list_entities)["error"] == "invalid_service_document"


def test_date_parameters_forwarded_to_paginated_query():
    with patch("sf_mcp.tools.query.make_paginated_odata_request", return_value={"results": []}) as request:
        result = call(query.query_odata, entity="EmpJob", paginate=True, from_date="2025-01-01",
                      to_date="2026-02-01", filter_parent_date=True)
    assert result["entity"] == "EmpJob"
    params = request.call_args.args[6]
    assert params["fromDate"] == "2025-01-01"
    assert params["toDate"] == "2026-02-01"
    assert params["filterParentDate"] == "true"


@pytest.mark.parametrize("args", [
    {"as_of_date": "2026-02-01", "from_date": "2025-01-01"},
    {"as_of_date": "2026-02-30"}, {"as_of_date": "2026-1-01"},
    {"from_date": "2026-02-01", "to_date": "2025-01-01"},
])
def test_invalid_date_combination_never_queries(args):
    with patch("sf_mcp.tools.query.make_odata_request") as request:
        assert "error" in call(query.query_odata, entity="EmpJob", **args)
    request.assert_not_called()


def test_mdf_effective_date_uses_sap_as_of_parameter_not_filter():
    with patch("sf_mcp.tools.mdf.make_odata_request", return_value={"d": {"results": []}}) as request:
        result = call(mdf.query_mdf_object, object_name="Position", effective_date="2026-02-01")
    assert result["count"] == 0
    params = request.call_args.args[6]
    assert params["asOfDate"] == "2026-02-01"
    assert "$filter" not in params


@pytest.mark.parametrize("tool,params,entity,expected", [
    (ec_related.upsert_emergency_contact, {"person_id_external": "1", "name": "A", "relationship": "B"},
     "PerEmergencyContacts", {"personIdExternal": "1", "name": "A", "relationship": "B"}),
    (ec_related.upsert_person_relationship, {"person_id_external": "1", "related_person_id_external": "2", "start_date": "/Date(0)/"},
     "PerPersonRelationship", {"personIdExternal": "1", "relatedPersonIdExternal": "2", "startDate": "/Date(0)/"}),
    (ec_related.upsert_payment_information, {"worker": "1", "effective_start_date": "/Date(0)/"},
     "PaymentInformationV3", {"worker": "1", "effectiveStartDate": "/Date(0)/"}),
    (ec_related.upsert_payment_detail, {"worker": "1", "effective_start_date": "/Date(0)/", "external_code": "12", "fields": {}},
     "PaymentInformationDetailV3", {"PaymentInformationV3_worker": "1", "PaymentInformationV3_effectiveStartDate": "/Date(0)/", "externalCode": "12"}),
])
def test_related_writes_default_preview(tool, params, entity, expected):
    result = call(tool, **params)
    assert result["dry_run"] is True
    assert result["payload"][0] == {**expected, "__metadata": {"uri": entity, "type": f"SFOData.{entity}"}}


def test_attachment_validation_and_no_implicit_http():
    with patch("sf_mcp.client.get_session") as session:
        bad = call(ec_related.create_attachment, file_name="x.pdf", file_content_base64="not base64!", module="EC")
        good = call(ec_related.create_attachment, file_name="x.pdf", file_content_base64="YQ==", module="EC")
    assert "error" in bad
    assert good["dry_run"] is True
    session.assert_not_called()


def test_country_payment_child_constructs_parent_and_child_keys():
    result = call(ec_related.upsert_payment_detail_country, country_code="PRY", worker="w1",
                  effective_start_date="/Date(0)/", payment_detail_external_code="10", external_code="11",
                  fields={"accountType": "SA"})
    payload = result["payload"][0]
    assert payload["__metadata"]["uri"] == "PaymentInformationDetailV3PRY"
    assert payload["PaymentInformationDetailV3_externalCode"] == "10"
    assert payload["externalCode"] == "11"
    assert result["dry_run"] is True


def test_catalog_reports_metadata_failure_and_next_page():
    with patch("sf_mcp.tools.configuration.make_service_doc_request", return_value={"EntitySets": ["EmpJob", "EmpEmployment"]}), \
         patch("sf_mcp.tools.configuration.make_metadata_request", return_value={"error": "HTTP 403"}):
        result = call(configuration.get_entity_catalog, top=1)
    assert result["total"] == 2
    assert result["next_skip"] == 1
    assert result["results"][0]["metadata_status"] == "unavailable"


def test_service_doc_cache_isolated_by_host_and_credentials():
    cache = ResponseCache()
    with patch("sf_mcp.client.get_cache", return_value=cache), \
         patch("sf_mcp.client._execute_with_rate_limit") as fetch:
        response = fetch.return_value
        response.status_code = 200
        response.json.return_value = {"EntitySets": ["EmpJob"]}
        args = ("test", "DC55", "preview", "user", "password")
        assert make_service_doc_request(*args) == {"EntitySets": ["EmpJob"]}
        assert make_service_doc_request(*args) == {"EntitySets": ["EmpJob"]}
        make_service_doc_request("test", "DC55", "preview", "user", "another-password")
        make_service_doc_request("test", "DC55", "production", "user", "password")
        assert fetch.call_count == 3


def test_read_escapes_person_key():
    with patch("sf_mcp.tools.ec_related.make_odata_request", return_value={"d": {"results": []}}) as request:
        assert call(ec_related.get_emergency_contacts, person_id_external="O'Neil")["count"] == 0
    assert request.call_args.args[6]["$filter"] == "personIdExternal eq 'O''Neil'"


def test_new_tools_registered():
    names = asyncio.run(mcp.get_tools())
    for name in ("get_entity_catalog", "get_emergency_contacts", "upsert_emergency_contact",
                 "get_person_relationships", "upsert_person_relationship", "get_payment_information",
                 "upsert_payment_information", "upsert_payment_detail", "upsert_payment_detail_country",
                 "get_attachment", "create_attachment"):
        assert name in names
