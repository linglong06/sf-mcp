"""Workforce SCIM contract tests. All transport is mocked; no live tenants."""

import asyncio
import json
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlsplit

import pytest
import requests

from sf_mcp.config import DEFAULT_TIMEOUT
from sf_mcp.rate_limiter import RateLimitExceeded
from sf_mcp.scim import ERROR_SCHEMA, SCHEMAS, SF_SCHEMA
from sf_mcp.server import mcp
from sf_mcp.tools import workforce_scim_users as tools

COMMON = dict(
    instance="test-instance",
    data_center="DC55",
    environment="production",
    auth_user_id="mock-admin",
    auth_password="mock-secret-do-not-log",
)
CREATE = dict(user_name="New.User", user_type="EMPLOYEE", active=True, person_id_external="p1")
REAL = dict(dry_run=False, provisioning_model_confirmed=True)
RESOURCE = {
    "id": "scim-id",
    "userName": "New.User",
    "userType": "EMPLOYEE",
    "active": True,
    SF_SCHEMA: {"personIdExternal": "p1", "perPersonUuid": "uuid-1"},
}


def response(body=None, status=200):
    result = MagicMock(spec=requests.Response)
    result.status_code = status
    result.json.return_value = body
    return result


def page(resources=None, total=0, **extra):
    return response({"Resources": resources or [], "totalResults": total, **extra})


def invoke(name, **kwargs):
    tool = getattr(tools, name)
    return getattr(tool, "fn", tool)(**(COMMON | kwargs))


def create(**kwargs):
    return invoke("create_scim_user", **(CREATE | kwargs))


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setenv("SF_ALLOW_WRITE", "false")
    # Catch accidental transport bypasses too.
    with (
        patch("requests.sessions.Session.request", side_effect=AssertionError("Unexpected network")),
        patch("sf_mcp.scim.get_session") as factory,
    ):
        factory.return_value.request.side_effect = AssertionError("Unexpected SCIM request")
        yield factory.return_value


def scripted(session, *responses):
    session.request.side_effect = responses


@pytest.mark.parametrize(
    "key,attribute", [("user_name", "userName"), ("person_id_external", f"{SF_SCHEMA}:personIdExternal")]
)
def test_exact_query_wire_shape_and_escaping(no_network, key, attribute):
    value = 'AbC"\\ &雪'
    scripted(no_network, page([RESOURCE], 1))
    result = invoke("get_scim_user", **{key: value})
    assert result["outcome"] == "found"
    call = no_network.request.call_args
    assert call.args == ("GET", "https://api55.sapsf.eu/rest/iam/scim/v2/Users")
    assert call.kwargs["headers"] == {"Accept": "application/scim+json, application/json"}
    assert call.kwargs["auth"] == ("mock-admin@test-instance", COMMON["auth_password"])
    assert call.kwargs["timeout"] == DEFAULT_TIMEOUT
    assert call.kwargs["allow_redirects"] is False
    assert "json" not in call.kwargs
    params = call.kwargs["params"]
    assert params == {"filter": f"{attribute} eq {json.dumps(value, ensure_ascii=False)}", "count": 2, "startIndex": 1}
    # Exercise requests' URL encoding without sending anything.
    prepared = requests.Request("GET", call.args[1], params=params).prepare()
    assert parse_qs(urlsplit(prepared.url).query)["filter"] == [params["filter"]]
    assert '"' not in prepared.url and "雪" not in prepared.url


@pytest.mark.parametrize(
    "args",
    [
        {},
        {"user_name": "u", "person_id_external": "p"},
        {"user_name": ""},
        {"person_id_external": " "},
        {"user_name": "u", "person_id_external": ""},
    ],
)
def test_query_key_validation(no_network, args):
    assert invoke("get_scim_user", **args)["outcome"] == "invalid_input"
    no_network.request.assert_not_called()


@pytest.mark.parametrize(
    "body,outcome",
    [
        ({"Resources": [], "totalResults": 0}, "not_found"),
        ({"Resources": []}, "not_found"),
        ({"totalResults": 0}, "not_found"),
        ({"Resources": [RESOURCE], "totalResults": 1}, "found"),
        ({"Resources": [RESOURCE, RESOURCE], "totalResults": 2}, "multiple"),
        ({"Resources": [RESOURCE], "totalResults": 20}, "multiple"),
        ({"Resources": [], "totalResults": 20}, "multiple"),
        ({"Resources": [], "totalResults": 1}, "incomplete"),
        ({"Resources": [RESOURCE]}, "incomplete"),
        ({"Resources": [RESOURCE], "totalResults": 0}, "incomplete"),
        ({"Resources": [RESOURCE], "totalResults": 1, "startIndex": 2}, "incomplete"),
        ({"Resources": None, "totalResults": 1}, "incomplete"),
        ({"Resources": [RESOURCE], "totalResults": "1"}, "incomplete"),
        ({}, "incomplete"),
        ([], "incomplete"),
    ],
)
def test_query_outcomes_and_partial_pages(no_network, body, outcome):
    scripted(no_network, response(body))
    result = invoke("get_scim_user", user_name="New.User")
    assert result["outcome"] == outcome
    no_network.request.assert_called_once()


@pytest.mark.parametrize("status,outcome", [(401, "unauthorized"), (403, "forbidden"), (429, "rate_limited")])
def test_query_http_errors(no_network, status, outcome):
    scripted(no_network, response({"detail": COMMON["auth_password"]}, status))
    result = invoke("get_scim_user", user_name="u")
    assert result["outcome"] == outcome
    assert COMMON["auth_password"] not in str(result)
    no_network.request.assert_called_once()


@pytest.mark.parametrize("status,outcome", [("401", "unauthorized"), ("403", "forbidden")])
def test_scim_error_envelope(no_network, status, outcome):
    scripted(no_network, response({"schemas": [ERROR_SCHEMA], "status": status, "detail": "private"}))
    assert invoke("get_scim_user", user_name="u")["outcome"] == outcome


def test_http_permission_error_with_malformed_scim_envelope(no_network):
    scripted(no_network, response({"schemas": 42}, 403))
    assert invoke("get_scim_user", user_name="u")["outcome"] == "forbidden"


@pytest.mark.parametrize("invalid_id", [None, " ", 42, {"password": "hidden"}])
def test_invalid_resource_id_cannot_prove_found(no_network, invalid_id):
    scripted(no_network, page([RESOURCE | {"id": invalid_id}], 1))
    assert invoke("get_scim_user", user_name="u")["outcome"] == "incomplete"


def test_uuid_only_preview():
    result = create(person_id_external=None, per_person_uuid="uuid-1")
    assert result["outcome"] == "preview"
    assert result["payload"][SF_SCHEMA] == {"perPersonUuid": "uuid-1"}


@pytest.mark.parametrize("key", ["user_name", "user_type", "active"])
def test_missing_required_inputs(no_network, key):
    args = dict(CREATE)
    args.pop(key)
    assert "error" in invoke("create_scim_user", **args)
    no_network.request.assert_not_called()


@pytest.mark.parametrize(
    "override",
    [
        {"user_name": ""},
        {"user_type": " "},
        {"active": None},
        {"active": "true"},
        {"active": 1},
        {"person_id_external": None},
        {"person_id_external": " "},
        {"per_person_uuid": ""},
        {"dry_run": "false"},
    ],
)
def test_invalid_required_inputs(no_network, override):
    assert create(**override)["outcome"] == "invalid_input"
    no_network.request.assert_not_called()


def test_preview_default_no_credentials_no_http(no_network):
    with patch("sf_mcp.scim.resolve_credentials") as resolve:
        result = create(auth_user_id="", auth_password="", active=False)
    assert result["outcome"] == "preview"
    assert result["method"] == "POST"
    assert result["url"] == "https://api55.sapsf.eu/rest/iam/scim/v2/Users"
    assert result["endpoint"] == "/rest/iam/scim/v2/Users"
    assert result["params"] is None
    assert result["dry_run"] is True
    assert result["allow_write"] is False
    assert "not validated by SAP" in result["message"]
    assert result["payload"] == {
        "schemas": list(SCHEMAS),
        "userName": "New.User",
        "userType": "EMPLOYEE",
        "active": False,
        SF_SCHEMA: {"personIdExternal": "p1"},
    }
    assert set(result["payload"]["schemas"]) == {
        "urn:ietf:params:scim:schemas:core:2.0:User",
        "urn:ietf:params:scim:schemas:extension:successfactors:2.0:User",
        "urn:ietf:params:scim:schemas:extension:sap:2.0:User",
        "urn:ietf:params:scim:schemas:extension:enterprise:2.0:User",
    }
    resolve.assert_not_called()
    no_network.request.assert_not_called()


def test_uuid_precedence_and_explicit_optional_fields():
    result = create(
        per_person_uuid="uuid-1",
        locale="en_US",
        timezone="Europe/Berlin",
        external_id="external-1",
        login_method="sso",
        emails=[{"value": "example@example.com", "type": "work", "primary": True}],
        phone_numbers=[{"value": "12345"}],
    )
    payload = result["payload"]
    assert payload[SF_SCHEMA] == {"perPersonUuid": "uuid-1", "loginMethod": "sso"}
    assert payload["locale"] == "en_US" and payload["timezone"] == "Europe/Berlin"
    assert payload["externalId"] == "external-1"
    assert payload["emails"][0]["primary"] is True
    assert payload["phoneNumbers"] == [{"value": "12345"}]


def test_write_gate_blocks_before_queries(no_network):
    result = create(**REAL)
    assert result["outcome"] == "blocked" and "SF_ALLOW_WRITE=true" in result["error"]
    no_network.request.assert_not_called()


def test_provisioning_confirmation_required(no_network, monkeypatch):
    monkeypatch.setenv("SF_ALLOW_WRITE", "true")
    assert create(dry_run=False)["outcome"] == "blocked"
    no_network.request.assert_not_called()


@pytest.mark.parametrize("duplicate_at", [0, 1, 2])
def test_precreate_duplicates_for_each_key(no_network, monkeypatch, duplicate_at):
    monkeypatch.setenv("SF_ALLOW_WRITE", "true")
    scripted(no_network, *([page()] * duplicate_at), page([RESOURCE], 1))
    result = create(**REAL, per_person_uuid="uuid-1")
    assert result["outcome"] == "conflict"
    assert result["matched_by"] == ["userName", "personIdExternal", "perPersonUuid"][duplicate_at]
    assert no_network.request.call_count == duplicate_at + 1
    assert all(call.args[0] == "GET" for call in no_network.request.call_args_list)


@pytest.mark.parametrize(
    "failed_page,outcome",
    [
        (page([RESOURCE], 7), "conflict"),
        (page([], 1), "incomplete"),
        (response({}, 403), "forbidden"),
        (response({}, 401), "unauthorized"),
        (response({}, 400), "error"),
        (response({}), "incomplete"),
    ],
)
def test_failed_preflight_never_posts(no_network, monkeypatch, failed_page, outcome):
    monkeypatch.setenv("SF_ALLOW_WRITE", "true")
    scripted(no_network, failed_page)
    assert create(**REAL)["outcome"] == outcome
    no_network.request.assert_called_once()
    assert no_network.request.call_args.args[0] == "GET"


def test_real_create_wire_shape_and_all_checks(no_network, monkeypatch):
    monkeypatch.setenv("SF_ALLOW_WRITE", "true")
    scripted(no_network, page(), page(), page(), response(RESOURCE, 201))
    with patch("sf_mcp.scim.get_rate_limiter") as limiter:
        result = create(**REAL, per_person_uuid="uuid-1")
    assert result["outcome"] == "created"
    assert limiter.return_value.check_and_record.call_count == 4
    assert all(
        call.kwargs["request_id"] == result["request_id"]
        for call in limiter.return_value.check_and_record.call_args_list
    )
    calls = no_network.request.call_args_list
    assert [call.args[0] for call in calls] == ["GET", "GET", "GET", "POST"]
    assert [call.kwargs["params"]["filter"] for call in calls[:3]] == [
        'userName eq "New.User"',
        f'{SF_SCHEMA}:personIdExternal eq "p1"',
        f'{SF_SCHEMA}:perPersonUuid eq "uuid-1"',
    ]
    post = calls[-1]
    assert post.args[1] == "https://api55.sapsf.eu/rest/iam/scim/v2/Users"
    assert post.kwargs["headers"]["Content-Type"] == "application/scim+json"
    assert post.kwargs["json"][SF_SCHEMA] == {"perPersonUuid": "uuid-1"}
    assert post.kwargs["json"]["schemas"] == list(SCHEMAS)
    assert post.kwargs["params"] is None and post.kwargs["allow_redirects"] is False


@pytest.mark.parametrize(
    "status,outcome",
    [(409, "conflict"), (401, "unauthorized"), (403, "forbidden"), (429, "rate_limited"), (500, "unknown")],
)
def test_post_errors_no_retry(no_network, monkeypatch, status, outcome):
    monkeypatch.setenv("SF_ALLOW_WRITE", "true")
    scripted(no_network, page(), page(), response({"detail": COMMON["auth_password"]}, status))
    result = create(**REAL)
    assert result["outcome"] == outcome
    assert no_network.request.call_count == 3
    assert COMMON["auth_password"] not in str(result)


@pytest.mark.parametrize("exc", [requests.Timeout, requests.ConnectionError])
def test_transport_unknown_no_retry_and_no_secrets(no_network, monkeypatch, exc):
    monkeypatch.setenv("SF_ALLOW_WRITE", "true")
    scripted(no_network, page(), page(), exc(COMMON["auth_password"]))
    with patch("sf_mcp.scim.audit_log") as audit, patch("sf_mcp.decorators.audit_log") as tool_audit:
        result = create(**REAL)
    assert result["outcome"] == "unknown"
    assert "get_scim_user" in result["error"]
    assert no_network.request.call_count == 3
    assert COMMON["auth_password"] not in str((result, audit.call_args_list, tool_audit.call_args_list))
    assert "Authorization" not in str(audit.call_args_list)


@pytest.mark.parametrize("operation", ["query", "create"])
def test_sanitized_summaries(no_network, monkeypatch, operation):
    monkeypatch.setenv("SF_ALLOW_WRITE", "true")
    resource = RESOURCE | {
        "password": "hidden",
        "credentials": "hidden",
        "emails": [{"value": "a@example.com", "password": "hidden"}],
        "userName": {"password": "hidden"},
    }
    if operation == "query":
        scripted(no_network, page([resource], 1))
        result = invoke("get_scim_user", user_name="New.User")
    else:
        scripted(no_network, page(), page(), response(resource, 201))
        result = create(**REAL)
    assert "hidden" not in str(result)
    assert result["user"]["emails"] == [{"value": "a@example.com"}]
    assert result["user"]["personIdExternal"] == "p1"
    assert result["user"]["perPersonUuid"] == "uuid-1"


def test_sensitive_optional_input_not_logged(no_network):
    with patch("sf_mcp.decorators.audit_log") as audit:
        result = create(emails=[{"value": "a@example.com", "password": "hidden"}])
    assert result["outcome"] == "invalid_input"
    assert "hidden" not in str((audit.call_args_list, result))
    no_network.request.assert_not_called()


def test_local_rate_limit_and_missing_credentials(no_network):
    assert invoke("get_scim_user", user_name="u", auth_password="")["outcome"] == "unauthorized"
    with patch("sf_mcp.scim.get_rate_limiter") as limiter:
        limiter.return_value.check_and_record.side_effect = RateLimitExceeded("test-instance", 1, 1, 60)
        assert invoke("get_scim_user", user_name="u")["outcome"] == "rate_limited"
    no_network.request.assert_not_called()


def test_invalid_json_and_empty_post_response(no_network, monkeypatch):
    monkeypatch.setenv("SF_ALLOW_WRITE", "true")
    invalid = response()
    invalid.json.side_effect = ValueError(COMMON["auth_password"])
    scripted(no_network, invalid, page(), page(), invalid)
    assert invoke("get_scim_user", user_name="u")["outcome"] == "incomplete"
    result = create(**REAL)
    assert result["outcome"] == "unknown"
    assert COMMON["auth_password"] not in str(result)


def test_registered_tool_schemas_and_descriptions():
    registered = asyncio.run(mcp.get_tools())
    query = registered["get_scim_user"]
    create_tool = registered["create_scim_user"]
    assert {"user_name", "user_type", "active"} <= set(create_tool.parameters["required"])
    assert create_tool.parameters["properties"]["dry_run"]["default"] is True
    assert create_tool.parameters["properties"]["provisioning_model_confirmed"]["default"] is False
    assert "auth_password" not in create_tool.parameters["required"]
    assert "request_id" not in query.parameters["properties"]
    assert "IAS/IPS" in query.description and "IAS/IPS" in create_tool.description
    assert "not proof of a login account" in create_tool.description
