"""EC personnel writes through registered tools, without live SF access."""

import asyncio
from copy import deepcopy
from unittest.mock import patch

import pytest

from sf_mcp.server import mcp
from sf_mcp.tools import employee, permissions, position, time_off

COMMON = {
    "instance": "test-instance",
    "data_center": "DC55",
    "environment": "production",
    "auth_user_id": "admin",
    "auth_password": "test-password",
}
CASES = [
    ("upsert_emp_job", "EmpJob", {"user_id": "u1", "start_date": "/Date(0)/", "seq_number": 1},
     {"userId": "u1", "startDate": "/Date(0)/", "seqNumber": 1}),
    ("upsert_emp_employment", "EmpEmployment", {"person_id_external": "p1", "user_id": "u1"},
     {"personIdExternal": "p1", "userId": "u1"}),
    ("upsert_emp_job_relationship", "EmpJobRelationships",
     {"user_id": "u1", "start_date": "/Date(0)/", "relationship_type": "hr"},
     {"userId": "u1", "startDate": "/Date(0)/", "relationshipType": "hr"}),
    ("upsert_per_person", "PerPerson", {"person_id_external": "p1"}, {"personIdExternal": "p1"}),
    ("upsert_per_personal", "PerPersonal", {"person_id_external": "p1", "start_date": "/Date(0)/"},
     {"personIdExternal": "p1", "startDate": "/Date(0)/"}),
    ("upsert_per_email", "PerEmail", {"person_id_external": "p1", "email_type": "business"},
     {"personIdExternal": "p1", "emailType": "business"}),
    ("upsert_per_phone", "PerPhone", {"person_id_external": "p1", "phone_type": "business"},
     {"personIdExternal": "p1", "phoneType": "business"}),
    ("upsert_per_address", "PerAddressDEFLT",
     {"person_id_external": "p1", "address_type": "home", "start_date": "/Date(0)/"},
     {"personIdExternal": "p1", "addressType": "home", "startDate": "/Date(0)/"}),
    ("upsert_per_national_id", "PerNationalId",
     {"person_id_external": "p1", "country": "USA", "card_type": "ssn"},
     {"personIdExternal": "p1", "country": "USA", "cardType": "ssn"}),
]

CASES += [
    ("upsert_emp_compensation", "EmpCompensation", {"user_id": "u1", "start_date": "/Date(0)/"},
     {"userId": "u1", "startDate": "/Date(0)/"}),
    ("upsert_emp_pay_comp_recurring", "EmpPayCompRecurring",
     {"user_id": "u1", "start_date": "/Date(0)/", "seq_number": 1, "pay_component": "BASE"},
     {"userId": "u1", "startDate": "/Date(0)/", "seqNumber": 1, "payComponent": "BASE"}),
    ("upsert_emp_pay_comp_non_recurring", "EmpPayCompNonRecurring",
     {"user_id": "u1", "pay_date": "/Date(0)/", "pay_component_code": "BONUS"},
     {"userId": "u1", "payDate": "/Date(0)/", "payComponentCode": "BONUS"}),
    ("upsert_position", "Position", {"code": "POS1", "effective_start_date": "/Date(0)/"},
     {"code": "POS1", "effectiveStartDate": "/Date(0)/"}),
    ("upsert_emp_work_permit", "EmpWorkPermit",
     {"user_id": "u1", "country": "USA", "document_type": "visa", "document_number": "123",
      "issue_date": "/Date(0)/"},
     {"userId": "u1", "country": "USA", "documentType": "visa", "documentNumber": "123",
      "issueDate": "/Date(0)/"}),
    ("upsert_emp_employment_termination", "EmpEmploymentTermination",
     {"person_id_external": "p1", "user_id": "u1", "end_date": "/Date(0)/"},
     {"personIdExternal": "p1", "userId": "u1", "endDate": "/Date(0)/"}),
    ("upsert_emp_employment_higher_duty", "EmpEmploymentHigherDuty", {"user_id": "u1"}, {"userId": "u1"}),
    ("upsert_user", "User", {"user_id": "u1"}, {"userId": "u1"}),
    ("upsert_employee_time", "EmployeeTime", {"external_code": "t1"}, {"externalCode": "t1"}),
    ("upsert_employee_time_country", "EmployeeTimeUSA", {"country_code": "USA", "external_code": "t1"},
     {"externalCode": "t1"}),
    ("upsert_rbp_role", "RBPRole", {"role_id": "1"}, {"roleId": "1"}),
    ("upsert_rbp_rule", "RBPRule", {"rule_id": "1"}, {"ruleId": "1"}),
    ("upsert_dynamic_group", "DynamicGroup", {"group_id": "1"}, {"groupID": "1"}),
    ("upsert_per_global_info", "PerGlobalInfoUSA",
     {"country_code": "USA", "person_id_external": "p1", "start_date": "/Date(0)/"},
     {"personIdExternal": "p1", "startDate": "/Date(0)/", "country": "USA"}),
]



def invoke(name, args, **kwargs):
    tool = next(getattr(module, name) for module in (employee, permissions, position, time_off)
                if hasattr(module, name))
    return getattr(tool, "fn", tool)(**COMMON, **args, **kwargs)


@pytest.mark.parametrize("name,entity,args,keys", CASES)
def test_default_dry_run_delegates(name, entity, args, keys):
    preview = {"dry_run": True}
    with patch("sf_mcp.tools.ec_upsert.make_odata_mutation", return_value=preview) as mutate:
        assert invoke(name, args) == preview
    mutate.assert_called_once()
    assert mutate.call_args.args == (
        COMMON["instance"], "/odata/v2/upsert", COMMON["data_center"], COMMON["environment"],
        COMMON["auth_user_id"], COMMON["auth_password"],
    )
    options = mutate.call_args.kwargs
    assert options["dry_run"] is True
    assert options["method"] == "POST"
    assert options["params"] == {"purgeType": "incremental"}
    assert options["payload"] == [{**keys, "__metadata": {"uri": entity, "type": f"SFOData.{entity}"}}]
    assert options["request_id"]


@pytest.mark.parametrize("name,entity,args,keys", CASES)
@pytest.mark.parametrize("enabled", ["true", "false"])
def test_preview_no_http(name, entity, args, keys, enabled, monkeypatch):
    monkeypatch.setenv("SF_ALLOW_WRITE", enabled)
    with patch("sf_mcp.client.get_session") as session:
        result = invoke(name, args)
    session.assert_not_called()
    assert result["dry_run"] is True
    assert result["allow_write"] is (enabled == "true")


@pytest.mark.parametrize("name,entity,args,keys", CASES)
def test_blocked_write(name, entity, args, keys, monkeypatch):
    monkeypatch.setenv("SF_ALLOW_WRITE", "false")
    with patch("sf_mcp.client.get_session") as session:
        result = invoke(name, args, dry_run=False)
    session.assert_not_called()
    assert "SF_ALLOW_WRITE=true" in result["error"]


@pytest.mark.parametrize("name,entity,args,keys", CASES)
@pytest.mark.parametrize("empty", ["", "  \t"])
def test_empty_keys(name, entity, args, keys, empty):
    for key in args:
        with patch("sf_mcp.tools.ec_upsert.make_odata_mutation") as mutate:
            result = invoke(name, {**args, key: empty})
        assert result == {"error": f"{key} must not be empty"}
        mutate.assert_not_called()


@pytest.mark.parametrize("name,entity,args,keys", CASES)
def test_keys_and_metadata_override_fields(name, entity, args, keys):
    fields = {
        **dict.fromkeys((key for key in args if key != "country_code"), "wrong alias"),
        **dict.fromkeys(keys, "wrong canonical"),
        "__metadata": {"uri": "User"}, "cust_extra": {"value": "unchanged"},
    }
    original = deepcopy(fields)
    result = invoke(name, args, fields=fields)
    assert result["payload"] == [{**keys, "cust_extra": {"value": "unchanged"},
                                  "__metadata": {"uri": entity, "type": f"SFOData.{entity}"}}]
    assert fields == original


def test_optional_aliases_and_literal_values():
    fields = {
        "user_id": "O'Brien", "start_date": "/Date(0)/", "end_date": "/Date(86400000)/",
        "seq_number": 0, "relationship_type": "hr", "email_type": "work", "phone_type": "home",
        "address_type": "home", "card_type": "ssn", "person_id_external": "wrong", "custom_field": "x",
    }
    result = invoke("upsert_per_person", {"person_id_external": "O'Brien"}, fields=fields)
    assert result["payload"][0] == {
        "personIdExternal": "O'Brien", "userId": "O'Brien", "startDate": "/Date(0)/",
        "endDate": "/Date(86400000)/", "seqNumber": 0, "relationshipType": "hr", "emailType": "work",
        "phoneType": "home", "addressType": "home", "cardType": "ssn", "custom_field": "x",
        "__metadata": {"uri": "PerPerson", "type": "SFOData.PerPerson"},
    }


@pytest.mark.parametrize("name,entity,args,keys", [CASES[0], CASES[5]])
def test_enabled_write(name, entity, args, keys, monkeypatch):
    monkeypatch.setenv("SF_ALLOW_WRITE", "true")
    fields = {"jobTitle": "Engineer"} if entity == "EmpJob" else {"emailAddress": "person@example.com"}
    with patch("sf_mcp.client.get_session") as session:
        response = session.return_value.request.return_value
        response.status_code = 200
        response.text = '{"d": [{"status": "OK", "httpCode": 200}]}'
        response.json.return_value = {"d": [{"status": "OK", "httpCode": 200}]}
        assert invoke(name, args, fields=fields, dry_run=False) == response.json.return_value
    session.return_value.request.assert_called_once()
    call = session.return_value.request.call_args
    assert call.args == ("POST", "https://api55.sapsf.eu/odata/v2/upsert")
    assert call.kwargs["params"] == {"purgeType": "incremental"}
    assert call.kwargs["json"] == [{**keys, **fields,
                                   "__metadata": {"uri": entity, "type": f"SFOData.{entity}"}}]


def test_tools_registered():
    tools = asyncio.run(mcp.get_tools())
    for name, _, args, _ in CASES:
        schema = tools[name].parameters
        assert schema["properties"]["dry_run"]["default"] is True
        assert set(args).issubset(schema["required"])
        for injected in ("request_id", "start_time", "api_host"):
            assert injected not in schema["properties"]


@pytest.mark.parametrize("name,args,valid_codes", [
    ("upsert_per_global_info", {"person_id_external": "p1", "start_date": "/Date(0)/"},
     "ARE ARG AUS AUT BEL BGD BRA CAN CHE CHL CHN COL CZE DEU DNK EGY ESP FIN FRA GBR GTM HKG HUN "
     "IDN IND IRL ITA JPN KOR LKA MEX MYS NLD NOR NZL OMN PER PHL POL PRT QAT ROU RUS SAU SGP "
     "SWE THA TUR TWN USA VEN VNM ZAF"),
    ("upsert_employee_time_country", {"external_code": "t1"}, "AUS CAN COL DEU ESP ITA MEX NLD NOR POL SWE USA"),
])
def test_country_allowlists(name, args, valid_codes):
    for invalid in ("ZZZ", "US", "usa", "USA/Other", " USA "):
        with patch("sf_mcp.tools.ec_upsert.make_odata_mutation") as mutate:
            result = invoke(name, {**args, "country_code": invalid})
        mutate.assert_not_called()
        assert result["valid_types"] == sorted(valid_codes.split())
        assert all(code in result["error"] for code in valid_codes.split())
    for code in valid_codes.split():
        result = invoke(name, {**args, "country_code": code})
        entity = ("PerGlobalInfo" if name == "upsert_per_global_info" else "EmployeeTime") + code
        assert result["payload"][0]["__metadata"] == {"uri": entity, "type": f"SFOData.{entity}"}
        assert "country_code" not in result["payload"][0]
        if name == "upsert_per_global_info":
            assert result["payload"][0]["country"] == code


def test_new_optional_aliases():
    aliases = {
        "pay_component": "payComponent", "pay_component_code": "payComponentCode", "pay_date": "payDate",
        "effective_start_date": "effectiveStartDate", "document_type": "documentType",
        "document_number": "documentNumber", "issue_date": "issueDate", "role_id": "roleId",
        "rule_id": "ruleId", "group_id": "groupID", "external_code": "externalCode",
    }
    fields = dict.fromkeys(aliases, "literal")
    result = invoke("upsert_user", {"user_id": "u1"}, fields=fields)
    assert result["payload"][0] == {
        **dict.fromkeys(aliases.values(), "literal"), "userId": "u1",
        "__metadata": {"uri": "User", "type": "SFOData.User"},
    }


@pytest.mark.parametrize("background_type", ["Education", "Background_Education"])
def test_background_education_dry_run(background_type):
    fields = {"background_element_id": "wrong", "userId": "wrong", "school": "Example"}
    original = deepcopy(fields)
    with patch("sf_mcp.client.get_session") as session:
        result = invoke("upsert_background_element", {
            "background_type": background_type, "background_element_id": "123", "user_id": "u1",
        }, fields=fields)
    session.assert_not_called()
    assert result["dry_run"] is True
    assert result["payload"] == [{
        "backgroundElementId": "123", "userId": "u1", "school": "Example",
        "__metadata": {"uri": "Background_Education", "type": "SFOData.Background_Education"},
    }]
    assert fields == original


@pytest.mark.parametrize("background_type", ["VarPayEmpHistData", "VarPayEmpHistDataECSource"])
def test_background_varpay_id_only(background_type):
    args = {"background_type": background_type, "background_element_id": 123}
    result = invoke("upsert_background_element", args)
    assert result["dry_run"] is True
    assert result["payload"] == [{
        "backgroundElementId": 123,
        "__metadata": {"uri": f"Background_{background_type}", "type": f"SFOData.Background_{background_type}"},
    }]
    result = invoke("upsert_background_element", args, user_id="ignored", fields={"user_id": "from-fields"})
    assert result["payload"][0]["userId"] == "from-fields"
    result = invoke("upsert_background_element", args, user_id="ignored")
    assert "userId" not in result["payload"][0]


@pytest.mark.parametrize("background_type", ["Unknown", "", "Background_Unknown", "Education/Other"])
def test_background_unknown_type(background_type):
    with patch("sf_mcp.tools.ec_upsert.make_odata_mutation") as mutate:
        result = invoke("upsert_background_element", {
            "background_type": background_type, "background_element_id": "123", "user_id": "u1",
        }, dry_run=False)
    mutate.assert_not_called()
    assert "error" in result
    assert result["valid_types"] == sorted(
        "Awards Benefitselection Community Compensation Courses Documents Education Fsaelection "
        "FuncExperience Googledocs InsideWorkExperience Languages LeadExperience Memberships Mobility "
        "OutsideWorkExperience PreferredNextMove Promotability SpecialAssign "
        "SysScoreCardDevelopmentObjectivesPortlet TalentPool TalentPoolcorp "
        "VarPayEmpHistData VarPayEmpHistDataECSource".split()
    )


@pytest.mark.parametrize("key", ["background_element_id", "user_id"])
@pytest.mark.parametrize("empty", ["", "  \t"])
def test_background_empty_required_ids(key, empty):
    args = {"background_type": "Education", "background_element_id": "123", "user_id": "u1", key: empty}
    with patch("sf_mcp.tools.ec_upsert.make_odata_mutation") as mutate:
        assert invoke("upsert_background_element", args, dry_run=False) == {"error": f"{key} must not be empty"}
    mutate.assert_not_called()


def test_background_user_required_and_registered():
    with patch("sf_mcp.tools.ec_upsert.make_odata_mutation") as mutate:
        assert invoke("upsert_background_element", {
            "background_type": "Education", "background_element_id": "123",
        }) == {"error": "user_id must not be empty"}
    mutate.assert_not_called()
    schema = asyncio.run(mcp.get_tools())["upsert_background_element"].parameters
    assert schema["properties"]["dry_run"]["default"] is True
    assert {"background_type", "background_element_id"}.issubset(schema["required"])
    assert "user_id" not in schema["required"]
    for injected in ("request_id", "start_time", "api_host"):
        assert injected not in schema["properties"]


def test_background_upsert_path():
    test_default_dry_run_delegates(
        "upsert_background_element", "Background_Education",
        {"background_type": "Education", "background_element_id": "123", "user_id": "u1"},
        {"backgroundElementId": "123", "userId": "u1"},
    )
