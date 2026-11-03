"""Employee tools: profile, search, history, team roster."""

from datetime import date, timedelta
from typing import Any

from sf_mcp.client import make_odata_request
from sf_mcp.decorators import sf_tool
from sf_mcp.dependencies import ApiHost, RequestId, StartTime
from sf_mcp.server import mcp
from sf_mcp.tools.ec_upsert import upsert_ec_entity
from sf_mcp.tools.utils import display_name as _display_name
from sf_mcp.validation import sanitize_odata_string


BACKGROUND_TYPES = frozenset({
    "Awards", "Benefitselection", "Community", "Compensation", "Courses", "Documents",
    "Education", "Fsaelection", "FuncExperience", "Googledocs", "InsideWorkExperience",
    "Languages", "LeadExperience", "Memberships", "Mobility", "OutsideWorkExperience",
    "PreferredNextMove", "Promotability", "SpecialAssign",
    "SysScoreCardDevelopmentObjectivesPortlet", "TalentPool", "TalentPoolcorp",
    "VarPayEmpHistData", "VarPayEmpHistDataECSource",
})
_BACKGROUND_ID_ONLY_TYPES = frozenset({"VarPayEmpHistData", "VarPayEmpHistDataECSource"})


@mcp.tool()
@sf_tool("upsert_background_element")
def upsert_background_element(
    instance: str,
    background_type: str,
    background_element_id: str | int,
    data_center: str,
    environment: str,
    auth_user_id: str,
    auth_password: str,
    user_id: str = "",
    fields: dict[str, Any] | None = None,
    dry_run: bool = True,
    *,
    request_id: str = RequestId(),
    start_time: float = StartTime(),
    api_host: str = ApiHost(),
) -> dict[str, Any]:
    """Upsert one Background employee profile portlet; defaults to a dry-run preview.

    background_type selects a supported type, with an optional Background_ prefix.
    background_element_id is always required. user_id is required except for
    VarPayEmpHistData and VarPayEmpHistDataECSource, where this argument is ignored
    (userId may still be supplied through fields). Unknown types return valid_types.
    Explicit keys and generated metadata override fields. Real writes require
    dry_run=False and SF_ALLOW_WRITE=true; there is no environment whitelist.
    """
    background_type = background_type.removeprefix("Background_")
    if background_type not in BACKGROUND_TYPES:
        return {
            "error": f"Unknown background_type '{background_type}'",
            "valid_types": sorted(BACKGROUND_TYPES),
        }
    keys: dict[str, Any] = {"background_element_id": background_element_id}
    if background_type not in _BACKGROUND_ID_ONLY_TYPES:
        keys["user_id"] = user_id
    return upsert_ec_entity(
        f"Background_{background_type}", keys,
        instance, data_center, environment, auth_user_id, auth_password,
        fields, dry_run, request_id,
    )


@mcp.tool()
@sf_tool("get_employee_profile")
def get_employee_profile(
    instance: str,
    user_id: str,
    data_center: str,
    environment: str,
    auth_user_id: str,
    auth_password: str,
    include_compensation: bool = False,
    *,
    request_id: str = RequestId(),
    start_time: float = StartTime(),
    api_host: str = ApiHost(),
) -> dict[str, Any]:
    """
    Get a complete employee profile including job info, contact details, and manager.

    Returns the employee's current job title, department, location, manager,
    email, phone, and hire date in a single call. Optionally includes compensation.

    Args:
        instance: The SuccessFactors instance/company ID
        user_id: The employee's user ID (e.g., 'jsmith')
        data_center: SAP data center code (e.g., 'DC55', 'DC10', 'DC4')
        environment: Environment type ('preview', 'production', 'sales_demo')
        auth_user_id: SuccessFactors user ID for authentication (required)
        auth_password: SuccessFactors password for authentication (required)
        include_compensation: If True, also fetches current compensation details
    """
    safe_user_id = sanitize_odata_string(user_id)

    params = {
        "$filter": f"userId eq '{safe_user_id}'",
        "$select": (
            "userId,username,firstName,lastName,displayName,email,hireDate,"
            "status,hr,manager,department,division,location,title"
        ),
        "$expand": "manager,hr",
        "$format": "json",
        "$top": "1",
    }

    result = make_odata_request(
        instance,
        "/odata/v2/User",
        data_center,
        environment,
        auth_user_id,
        auth_password,
        params,
        request_id,
    )

    if "error" in result:
        return result

    users = result.get("d", {}).get("results", [])
    if not users:
        return {"error": f"Employee '{user_id}' not found", "message": "Check the user ID and try again."}

    user = users[0]

    profile = {
        "user_id": user.get("userId"),
        "display_name": _display_name(user),
        "first_name": user.get("firstName"),
        "last_name": user.get("lastName"),
        "email": user.get("email"),
        "hire_date": user.get("hireDate"),
        "status": user.get("status"),
        "title": user.get("title"),
        "department": user.get("department"),
        "division": user.get("division"),
        "location": user.get("location"),
    }

    # Extract manager info
    manager_data = user.get("manager", {})
    if isinstance(manager_data, dict) and "results" in manager_data:
        mgrs = manager_data["results"]
        if mgrs:
            mgr = mgrs[0]
            profile["manager"] = {"user_id": mgr.get("userId"), "name": _display_name(mgr)}
    elif isinstance(manager_data, dict) and manager_data.get("userId"):
        profile["manager"] = {"user_id": manager_data.get("userId"), "name": _display_name(manager_data)}

    # Extract HR info
    hr_data = user.get("hr", {})
    if isinstance(hr_data, dict) and hr_data.get("userId"):
        profile["hr_rep"] = {"user_id": hr_data.get("userId"), "name": _display_name(hr_data)}

    if include_compensation:
        comp_params = {
            "$filter": f"userId eq '{safe_user_id}'",
            "$select": "userId,startDate,payGroup,payGrade,compensatedHours",
            "$expand": "empPayCompRecurringNav",
            "$format": "json",
            "$top": "5",
            "$orderby": "startDate desc",
        }
        comp_result = make_odata_request(
            instance,
            "/odata/v2/EmpCompensation",
            data_center,
            environment,
            auth_user_id,
            auth_password,
            comp_params,
            request_id,
        )
        if "error" not in comp_result:
            comp_records = comp_result.get("d", {}).get("results", [])
            if comp_records:
                latest = comp_records[0]
                comp_info = {
                    "pay_group": latest.get("payGroup"),
                    "pay_grade": latest.get("payGrade"),
                    "effective_date": latest.get("startDate"),
                }
                recurring = latest.get("empPayCompRecurringNav", {})
                if isinstance(recurring, dict) and "results" in recurring:
                    comp_info["pay_components"] = [
                        {
                            "pay_component": r.get("payComponent"),
                            "amount": r.get("paycompvalue"),
                            "currency": r.get("currencyCode"),
                            "frequency": r.get("frequency"),
                        }
                        for r in recurring["results"]
                    ]
                profile["compensation"] = comp_info

    return {"profile": profile}


@mcp.tool()
@sf_tool("search_employees", max_top=200)
def search_employees(
    instance: str,
    data_center: str,
    environment: str,
    auth_user_id: str,
    auth_password: str,
    search_text: str = "",
    department: str = "",
    location: str = "",
    manager_id: str = "",
    status: str = "active",
    top: int = 50,
    *,
    request_id: str = RequestId(),
    start_time: float = StartTime(),
    api_host: str = ApiHost(),
) -> dict[str, Any]:
    """
    Search for employees by name, department, location, or manager.

    Find employees without knowing their exact user IDs. Supports partial name
    matching and filtering by department, location, or manager.

    Args:
        instance: The SuccessFactors instance/company ID
        data_center: SAP data center code (e.g., 'DC55', 'DC10', 'DC4')
        environment: Environment type ('preview', 'production', 'sales_demo')
        auth_user_id: SuccessFactors user ID for authentication (required)
        auth_password: SuccessFactors password for authentication (required)
        search_text: Partial name to search (searches first name and last name)
        department: Filter by department name or code
        location: Filter by work location
        manager_id: Filter to show only this manager's direct reports
        status: Employee status filter: 'active', 'inactive', or 'all' (default: 'active')
        top: Maximum number of results to return (default: 50, max: 200)
    """
    filters = []

    if search_text:
        safe_text = sanitize_odata_string(search_text)
        filters.append(
            f"(substringof('{safe_text}',firstName) or "
            f"substringof('{safe_text}',lastName) or "
            f"substringof('{safe_text}',displayName))"
        )
    if department:
        filters.append(f"department eq '{sanitize_odata_string(department)}'")
    if location:
        filters.append(f"location eq '{sanitize_odata_string(location)}'")
    if manager_id:
        filters.append(f"manager eq '{sanitize_odata_string(manager_id)}'")
    if status == "active":
        filters.append("(status eq 'active' or status eq 't')")
    elif status == "inactive":
        filters.append("(status eq 'inactive' or status eq 'f')")

    params = {
        "$select": (
            "userId,firstName,lastName,displayName,email,hireDate,status,title,department,division,location,manager"
        ),
        "$format": "json",
        "$top": str(top),
    }
    if filters:
        params["$filter"] = " and ".join(filters)

    result = make_odata_request(
        instance,
        "/odata/v2/User",
        data_center,
        environment,
        auth_user_id,
        auth_password,
        params,
        request_id,
    )

    if "error" in result:
        return result

    employees = [
        {
            "user_id": e.get("userId"),
            "display_name": _display_name(e),
            "email": e.get("email"),
            "title": e.get("title"),
            "department": e.get("department"),
            "division": e.get("division"),
            "location": e.get("location"),
            "hire_date": e.get("hireDate"),
            "status": e.get("status"),
        }
        for e in result.get("d", {}).get("results", [])
    ]

    return {
        "employees": employees,
        "count": len(employees),
        "search_criteria": {
            "search_text": search_text or None,
            "department": department or None,
            "location": location or None,
            "manager_id": manager_id or None,
            "status": status,
        },
    }


@mcp.tool()
@sf_tool("get_employee_history")
def get_employee_history(
    instance: str,
    user_id: str,
    data_center: str,
    environment: str,
    auth_user_id: str,
    auth_password: str,
    include_compensation_changes: bool = False,
    *,
    request_id: str = RequestId(),
    start_time: float = StartTime(),
    api_host: str = ApiHost(),
) -> dict[str, Any]:
    """
    View an employee's job history including promotions, transfers, and title changes.

    Shows chronological job records with title, department, location, and manager
    for each period. Useful for reviewing career progression.

    Args:
        instance: The SuccessFactors instance/company ID
        user_id: The employee's user ID
        data_center: SAP data center code (e.g., 'DC55', 'DC10', 'DC4')
        environment: Environment type ('preview', 'production', 'sales_demo')
        auth_user_id: SuccessFactors user ID for authentication (required)
        auth_password: SuccessFactors password for authentication (required)
        include_compensation_changes: If True, also fetches salary history
    """
    safe_user_id = sanitize_odata_string(user_id)

    params = {
        "$filter": f"userId eq '{safe_user_id}'",
        "$select": (
            "userId,startDate,endDate,jobTitle,department,location,"
            "position,managerId,employeeClass,eventReason,emplStatus"
        ),
        "$orderby": "startDate desc",
        "$format": "json",
        "$top": "100",
    }

    result = make_odata_request(
        instance,
        "/odata/v2/EmpJob",
        data_center,
        environment,
        auth_user_id,
        auth_password,
        params,
        request_id,
    )

    if "error" in result:
        return result

    history = [
        {
            "start_date": e.get("startDate"),
            "end_date": e.get("endDate"),
            "job_title": e.get("jobTitle"),
            "department": e.get("department"),
            "location": e.get("location"),
            "position": e.get("position"),
            "manager_id": e.get("managerId"),
            "employee_class": e.get("employeeClass"),
            "event_reason": e.get("eventReason"),
            "employment_status": e.get("emplStatus"),
        }
        for e in result.get("d", {}).get("results", [])
    ]

    response_data = {"user_id": user_id, "job_history": history, "record_count": len(history)}

    if include_compensation_changes:
        comp_params = {
            "$filter": f"userId eq '{safe_user_id}'",
            "$select": "userId,startDate,payGroup,payGrade",
            "$expand": "empPayCompRecurringNav",
            "$orderby": "startDate desc",
            "$format": "json",
            "$top": "50",
        }
        comp_result = make_odata_request(
            instance,
            "/odata/v2/EmpCompensation",
            data_center,
            environment,
            auth_user_id,
            auth_password,
            comp_params,
            request_id,
        )
        if "error" not in comp_result:
            comp_history = []
            for entry in comp_result.get("d", {}).get("results", []):
                comp_record = {
                    "effective_date": entry.get("startDate"),
                    "pay_group": entry.get("payGroup"),
                    "pay_grade": entry.get("payGrade"),
                }
                recurring = entry.get("empPayCompRecurringNav", {})
                if isinstance(recurring, dict) and "results" in recurring:
                    comp_record["pay_components"] = [
                        {
                            "pay_component": r.get("payComponent"),
                            "amount": r.get("paycompvalue"),
                            "currency": r.get("currencyCode"),
                            "frequency": r.get("frequency"),
                        }
                        for r in recurring["results"]
                    ]
                comp_history.append(comp_record)
            response_data["compensation_history"] = comp_history

    return response_data


@mcp.tool()
@sf_tool("get_team_roster", max_top=200)
def get_team_roster(
    instance: str,
    manager_id: str,
    data_center: str,
    environment: str,
    auth_user_id: str,
    auth_password: str,
    include_indirect_reports: bool = False,
    top: int = 100,
    *,
    request_id: str = RequestId(),
    start_time: float = StartTime(),
    api_host: str = ApiHost(),
) -> dict[str, Any]:
    """
    Get a manager's team roster with direct (and optionally indirect) reports.

    Shows all active team members with their job title, department, location,
    and hire date. Useful for org chart views, team planning, and 1-on-1 prep.

    Args:
        instance: The SuccessFactors instance/company ID
        manager_id: The manager's user ID
        data_center: SAP data center code (e.g., 'DC55', 'DC10', 'DC4')
        environment: Environment type ('preview', 'production', 'sales_demo')
        auth_user_id: SuccessFactors user ID for authentication (required)
        auth_password: SuccessFactors password for authentication (required)
        include_indirect_reports: If True, also fetches reports-of-reports (1 level deep)
        top: Maximum direct reports to return (default: 100, max: 200)
    """
    safe_mgr = sanitize_odata_string(manager_id)

    params = {
        "$filter": f"manager eq '{safe_mgr}' and (status eq 'active' or status eq 't')",
        "$select": "userId,firstName,lastName,displayName,email,hireDate,title,department,division,location",
        "$format": "json",
        "$top": str(top),
    }

    result = make_odata_request(
        instance,
        "/odata/v2/User",
        data_center,
        environment,
        auth_user_id,
        auth_password,
        params,
        request_id,
    )

    if "error" in result:
        return result

    direct_reports = [
        {
            "user_id": e.get("userId"),
            "display_name": _display_name(e),
            "email": e.get("email"),
            "title": e.get("title"),
            "department": e.get("department"),
            "division": e.get("division"),
            "location": e.get("location"),
            "hire_date": e.get("hireDate"),
        }
        for e in result.get("d", {}).get("results", [])
    ]

    response_data = {
        "manager_id": manager_id,
        "direct_reports": direct_reports,
        "direct_report_count": len(direct_reports),
    }

    if include_indirect_reports and direct_reports:
        indirect_reports = []
        for dr in direct_reports[:10]:  # Cap at 10 sub-queries to avoid timeout
            dr_id = dr["user_id"]
            if not dr_id:
                continue
            sub_params = {
                "$filter": f"manager eq '{sanitize_odata_string(dr_id)}' and (status eq 'active' or status eq 't')",
                "$select": "userId,firstName,lastName,displayName,email,hireDate,title,department,location",
                "$format": "json",
                "$top": "50",
            }
            sub_result = make_odata_request(
                instance,
                "/odata/v2/User",
                data_center,
                environment,
                auth_user_id,
                auth_password,
                sub_params,
                request_id,
            )
            if "error" not in sub_result:
                for entry in sub_result.get("d", {}).get("results", []):
                    indirect_reports.append(
                        {
                            "user_id": entry.get("userId"),
                            "display_name": _display_name(entry),
                            "title": entry.get("title"),
                            "department": entry.get("department"),
                            "location": entry.get("location"),
                            "reports_to": dr_id,
                        }
                    )

        response_data["indirect_reports"] = indirect_reports
        response_data["indirect_report_count"] = len(indirect_reports)
        response_data["total_team_size"] = len(direct_reports) + len(indirect_reports)
    else:
        response_data["total_team_size"] = len(direct_reports)

    return response_data


@mcp.tool()
@sf_tool("get_global_assignments", max_top=200)
def get_global_assignments(
    instance: str,
    data_center: str,
    environment: str,
    auth_user_id: str,
    auth_password: str,
    user_id: str = "",
    active_only: bool = True,
    top: int = 100,
    *,
    request_id: str = RequestId(),
    start_time: float = StartTime(),
    api_host: str = ApiHost(),
) -> dict[str, Any]:
    """
    List employees on international/global assignments with home and host details.

    Shows the home and host company/country for each assignment, useful for
    tracking expatriates and cross-border compliance requirements.

    Args:
        instance: The SuccessFactors instance/company ID
        data_center: SAP data center code (e.g., 'DC55', 'DC10', 'DC4')
        environment: Environment type ('preview', 'production', 'sales_demo')
        auth_user_id: SuccessFactors user ID for authentication (required)
        auth_password: SuccessFactors password for authentication (required)
        user_id: Optional employee user ID to filter to a single assignment history
        active_only: If True, only show currently active assignments (default: True)
        top: Maximum results (default: 100, max: 200)
    """
    filters = []
    if user_id:
        filters.append(f"userId eq '{sanitize_odata_string(user_id)}'")
    if active_only:
        today = date.today().isoformat()
        filters.append(f"startDate le datetime'{today}T23:59:59'")
        filters.append(f"(endDate eq null or endDate ge datetime'{today}T00:00:00')")

    params = {
        "$select": "userId,startDate,endDate,assignmentType,homeCompany,hostCompany,homeCountry,hostCountry",
        "$format": "json",
        "$top": str(top),
        "$orderby": "startDate desc",
    }
    if filters:
        params["$filter"] = " and ".join(filters)

    result = make_odata_request(
        instance,
        "/odata/v2/EmpGlobalAssignment",
        data_center,
        environment,
        auth_user_id,
        auth_password,
        params,
        request_id,
    )

    if "error" in result:
        return result

    assignments = [
        {
            "user_id": e.get("userId"),
            "start_date": e.get("startDate"),
            "end_date": e.get("endDate"),
            "assignment_type": e.get("assignmentType"),
            "home_company": e.get("homeCompany"),
            "host_company": e.get("hostCompany"),
            "home_country": e.get("homeCountry"),
            "host_country": e.get("hostCountry"),
        }
        for e in result.get("d", {}).get("results", [])
    ]

    return {
        "assignments": assignments,
        "count": len(assignments),
        "filters_applied": {"user_id": user_id or None, "active_only": active_only},
    }


@mcp.tool()
@sf_tool("get_employee_documents")
def get_employee_documents(
    instance: str,
    user_id: str,
    data_center: str,
    environment: str,
    auth_user_id: str,
    auth_password: str,
    document_type: str = "",
    top: int = 50,
    *,
    request_id: str = RequestId(),
    start_time: float = StartTime(),
    api_host: str = ApiHost(),
) -> dict[str, Any]:
    """
    List documents attached to an employee's record (contracts, IDs, certificates).

    Args:
        instance: The SuccessFactors instance/company ID
        user_id: The employee's user ID
        data_center: SAP data center code (e.g., 'DC55', 'DC10', 'DC4')
        environment: Environment type ('preview', 'production', 'sales_demo')
        auth_user_id: SuccessFactors user ID for authentication (required)
        auth_password: SuccessFactors password for authentication (required)
        document_type: Optional filter by document type (applied client-side)
        top: Maximum results (default: 50, max: 100)
    """
    top = max(1, min(top, 100))
    safe_user_id = sanitize_odata_string(user_id)

    params = {
        "$filter": f"personIdExternal eq '{safe_user_id}'",
        "$select": "personIdExternal,documentType,fileName,uploadedDate,expirationDate",
        "$format": "json",
        "$top": str(top),
        "$orderby": "uploadedDate desc",
    }

    result = make_odata_request(
        instance,
        "/odata/v2/PerDocuments",
        data_center,
        environment,
        auth_user_id,
        auth_password,
        params,
        request_id,
    )

    if "error" in result:
        return result

    documents = []
    for e in result.get("d", {}).get("results", []):
        doc_type = e.get("documentType")
        if document_type and doc_type and document_type.lower() not in str(doc_type).lower():
            continue
        documents.append(
            {
                "document_type": doc_type,
                "file_name": e.get("fileName"),
                "uploaded_date": e.get("uploadedDate"),
                "expiration_date": e.get("expirationDate"),
            }
        )

    return {
        "user_id": user_id,
        "documents": documents,
        "count": len(documents),
        "filters_applied": {"document_type": document_type or None},
    }


@mcp.tool()
@sf_tool("get_pay_component_groups")
def get_pay_component_groups(
    instance: str,
    user_id: str,
    data_center: str,
    environment: str,
    auth_user_id: str,
    auth_password: str,
    effective_date: str = "",
    *,
    request_id: str = RequestId(),
    start_time: float = StartTime(),
    api_host: str = ApiHost(),
) -> dict[str, Any]:
    """
    Get an employee's recurring pay components (allowances, bonuses, etc).

    Unlike get_compensation_details (which returns the full compensation record),
    this queries recurring pay elements directly for a lighter-weight view.

    Args:
        instance: The SuccessFactors instance/company ID
        user_id: The employee's user ID
        data_center: SAP data center code (e.g., 'DC55', 'DC10', 'DC4')
        environment: Environment type ('preview', 'production', 'sales_demo')
        auth_user_id: SuccessFactors user ID for authentication (required)
        auth_password: SuccessFactors password for authentication (required)
        effective_date: Show components as of this date (YYYY-MM-DD). Defaults to latest.
    """
    safe_user_id = sanitize_odata_string(user_id)
    filter_expr = f"userId eq '{safe_user_id}'"
    if effective_date:
        filter_expr += f" and startDate le datetime'{sanitize_odata_string(effective_date)}T23:59:59'"

    params = {
        "$filter": filter_expr,
        "$select": "userId,payComponent,paycompvalue,currencyCode,frequency,startDate,endDate",
        "$format": "json",
        "$top": "100",
        "$orderby": "startDate desc",
    }

    result = make_odata_request(
        instance,
        "/odata/v2/EmpPayCompRecurring",
        data_center,
        environment,
        auth_user_id,
        auth_password,
        params,
        request_id,
    )

    if "error" in result:
        return result

    components = [
        {
            "pay_component": e.get("payComponent"),
            "amount": e.get("paycompvalue"),
            "currency": e.get("currencyCode"),
            "frequency": e.get("frequency"),
            "start_date": e.get("startDate"),
            "end_date": e.get("endDate"),
        }
        for e in result.get("d", {}).get("results", [])
    ]

    by_component: dict[str, int] = {}
    for c in components:
        key = c.get("pay_component") or "Unknown"
        by_component[key] = by_component.get(key, 0) + 1

    return {
        "user_id": user_id,
        "pay_components": components,
        "count": len(components),
        "by_component": by_component,
    }


@mcp.tool()
@sf_tool("get_work_permit_expiry", max_top=500)
def get_work_permit_expiry(
    instance: str,
    data_center: str,
    environment: str,
    auth_user_id: str,
    auth_password: str,
    within_days: int = 90,
    country: str = "",
    top: int = 100,
    *,
    request_id: str = RequestId(),
    start_time: float = StartTime(),
    api_host: str = ApiHost(),
) -> dict[str, Any]:
    """
    Find employees with work permits or visas expiring soon (compliance risk).

    Scans national ID card records for entries whose type looks like a work
    permit or visa and whose expiration date falls within the given window.

    Args:
        instance: The SuccessFactors instance/company ID
        data_center: SAP data center code (e.g., 'DC55', 'DC10', 'DC4')
        environment: Environment type ('preview', 'production', 'sales_demo')
        auth_user_id: SuccessFactors user ID for authentication (required)
        auth_password: SuccessFactors password for authentication (required)
        within_days: Flag permits/visas expiring within this many days (default: 90)
        country: Optional country code to filter (e.g., 'US')
        top: Maximum results (default: 100, max: 500)
    """
    today = date.today()
    cutoff = today + timedelta(days=within_days)

    filters = [
        f"expirationDate ge datetime'{today.isoformat()}T00:00:00'",
        f"expirationDate le datetime'{cutoff.isoformat()}T23:59:59'",
    ]
    if country:
        filters.append(f"country eq '{sanitize_odata_string(country)}'")

    params = {
        "$filter": " and ".join(filters),
        "$select": "personIdExternal,country,nationalIdCardType,expirationDate",
        "$format": "json",
        "$top": str(min(top * 3, 1000)),
        "$orderby": "expirationDate asc",
    }

    result = make_odata_request(
        instance,
        "/odata/v2/PerNationalId",
        data_center,
        environment,
        auth_user_id,
        auth_password,
        params,
        request_id,
    )

    if "error" in result:
        return result

    keywords = ("work permit", "visa", "permit")
    expiring = []
    for e in result.get("d", {}).get("results", []):
        id_type = str(e.get("nationalIdCardType") or "").lower()
        if not any(k in id_type for k in keywords):
            continue
        expiring.append(
            {
                "user_id": e.get("personIdExternal"),
                "country": e.get("country"),
                "id_type": e.get("nationalIdCardType"),
                "expiration_date": e.get("expirationDate"),
            }
        )

    expiring = expiring[:top]

    return {
        "expiring_permits": expiring,
        "count": len(expiring),
        "within_days": within_days,
        "filters_applied": {"country": country or None},
    }


@mcp.tool()
@sf_tool("get_probation_end_dates", max_top=500)
def get_probation_end_dates(
    instance: str,
    data_center: str,
    environment: str,
    auth_user_id: str,
    auth_password: str,
    within_days: int = 30,
    department: str = "",
    top: int = 100,
    *,
    request_id: str = RequestId(),
    start_time: float = StartTime(),
    api_host: str = ApiHost(),
) -> dict[str, Any]:
    """
    Find employees approaching the end of their probation period.

    Args:
        instance: The SuccessFactors instance/company ID
        data_center: SAP data center code (e.g., 'DC55', 'DC10', 'DC4')
        environment: Environment type ('preview', 'production', 'sales_demo')
        auth_user_id: SuccessFactors user ID for authentication (required)
        auth_password: SuccessFactors password for authentication (required)
        within_days: Flag probation periods ending within this many days (default: 30)
        department: Filter by department
        top: Maximum results (default: 100, max: 500)
    """
    today = date.today()
    cutoff = today + timedelta(days=within_days)

    filters = [
        "probationEndDate ne null",
        f"probationEndDate ge datetime'{today.isoformat()}T00:00:00'",
        f"probationEndDate le datetime'{cutoff.isoformat()}T23:59:59'",
    ]
    if department:
        filters.append(f"department eq '{sanitize_odata_string(department)}'")

    params = {
        "$filter": " and ".join(filters),
        "$select": "userId,probationEndDate,startDate,jobTitle,department",
        "$format": "json",
        "$top": str(top),
        "$orderby": "probationEndDate asc",
    }

    result = make_odata_request(
        instance,
        "/odata/v2/EmpJob",
        data_center,
        environment,
        auth_user_id,
        auth_password,
        params,
        request_id,
    )

    if "error" in result:
        return result

    employees = [
        {
            "user_id": e.get("userId"),
            "job_title": e.get("jobTitle"),
            "department": e.get("department"),
            "hire_date": e.get("startDate"),
            "probation_end_date": e.get("probationEndDate"),
        }
        for e in result.get("d", {}).get("results", [])
    ]

    return {
        "employees": employees,
        "count": len(employees),
        "within_days": within_days,
        "filters_applied": {"department": department or None},
    }


@mcp.tool()
@sf_tool("upsert_emp_job")
def upsert_emp_job(
    instance: str,
    user_id: str,
    start_date: str,
    seq_number: str | int,
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
    """Preview or upsert one EmpJob record via POST /odata/v2/upsert.

    Required business keys: user_id, start_date, seq_number.
    fields accepts SF properties and known snake_case key/date aliases.
    Explicit keys and generated SFOData metadata override fields. Dates pass
    through in SF OData format; supply tenant-required properties in fields.
    No event reason or effective date is inferred. dry_run defaults to True;
    real writes require SF_ALLOW_WRITE=true, with no environment whitelist.
    """
    return upsert_ec_entity(
        "EmpJob",
        {"user_id": user_id, "start_date": start_date, "seq_number": seq_number},
        instance,
        data_center,
        environment,
        auth_user_id,
        auth_password,
        fields,
        dry_run,
        request_id,
    )


@mcp.tool()
@sf_tool("upsert_emp_employment")
def upsert_emp_employment(
    instance: str,
    person_id_external: str,
    user_id: str,
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
    """Preview or upsert one EmpEmployment record via POST /odata/v2/upsert.

    Required business keys: person_id_external, user_id.
    fields accepts SF properties and known snake_case key/date aliases.
    Explicit keys and generated SFOData metadata override fields. Dates pass
    through in SF OData format; supply tenant-required properties in fields.
    No event reason or effective date is inferred. dry_run defaults to True;
    real writes require SF_ALLOW_WRITE=true, with no environment whitelist.
    """
    return upsert_ec_entity(
        "EmpEmployment",
        {"person_id_external": person_id_external, "user_id": user_id},
        instance,
        data_center,
        environment,
        auth_user_id,
        auth_password,
        fields,
        dry_run,
        request_id,
    )


@mcp.tool()
@sf_tool("upsert_emp_job_relationship")
def upsert_emp_job_relationship(
    instance: str,
    user_id: str,
    start_date: str,
    relationship_type: str,
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
    """Preview or upsert one EmpJobRelationships record via POST /odata/v2/upsert.

    Required business keys: user_id, start_date, relationship_type.
    fields accepts SF properties and known snake_case key/date aliases.
    Explicit keys and generated SFOData metadata override fields. Dates pass
    through in SF OData format; supply tenant-required properties in fields.
    No event reason or effective date is inferred. dry_run defaults to True;
    real writes require SF_ALLOW_WRITE=true, with no environment whitelist.
    """
    return upsert_ec_entity(
        "EmpJobRelationships",
        {"user_id": user_id, "start_date": start_date, "relationship_type": relationship_type},
        instance,
        data_center,
        environment,
        auth_user_id,
        auth_password,
        fields,
        dry_run,
        request_id,
    )


@mcp.tool()
@sf_tool("upsert_per_person")
def upsert_per_person(
    instance: str,
    person_id_external: str,
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
    """Preview or upsert one PerPerson record via POST /odata/v2/upsert.

    Required business keys: person_id_external.
    fields accepts SF properties and known snake_case key/date aliases.
    Explicit keys and generated SFOData metadata override fields. Dates pass
    through in SF OData format; supply tenant-required properties in fields.
    No event reason or effective date is inferred. dry_run defaults to True;
    real writes require SF_ALLOW_WRITE=true, with no environment whitelist.
    """
    return upsert_ec_entity(
        "PerPerson",
        {"person_id_external": person_id_external},
        instance,
        data_center,
        environment,
        auth_user_id,
        auth_password,
        fields,
        dry_run,
        request_id,
    )


@mcp.tool()
@sf_tool("upsert_per_personal")
def upsert_per_personal(
    instance: str,
    person_id_external: str,
    start_date: str,
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
    """Preview or upsert one PerPersonal record via POST /odata/v2/upsert.

    Required business keys: person_id_external, start_date.
    fields accepts SF properties and known snake_case key/date aliases.
    Explicit keys and generated SFOData metadata override fields. Dates pass
    through in SF OData format; supply tenant-required properties in fields.
    No event reason or effective date is inferred. dry_run defaults to True;
    real writes require SF_ALLOW_WRITE=true, with no environment whitelist.
    """
    return upsert_ec_entity(
        "PerPersonal",
        {"person_id_external": person_id_external, "start_date": start_date},
        instance,
        data_center,
        environment,
        auth_user_id,
        auth_password,
        fields,
        dry_run,
        request_id,
    )


@mcp.tool()
@sf_tool("upsert_per_email")
def upsert_per_email(
    instance: str,
    person_id_external: str,
    email_type: str,
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
    """Preview or upsert one PerEmail record via POST /odata/v2/upsert.

    Required business keys: person_id_external, email_type.
    fields accepts SF properties and known snake_case key/date aliases.
    Explicit keys and generated SFOData metadata override fields. Dates pass
    through in SF OData format; supply tenant-required properties in fields.
    No event reason or effective date is inferred. dry_run defaults to True;
    real writes require SF_ALLOW_WRITE=true, with no environment whitelist.
    """
    return upsert_ec_entity(
        "PerEmail",
        {"person_id_external": person_id_external, "email_type": email_type},
        instance,
        data_center,
        environment,
        auth_user_id,
        auth_password,
        fields,
        dry_run,
        request_id,
    )


@mcp.tool()
@sf_tool("upsert_per_phone")
def upsert_per_phone(
    instance: str,
    person_id_external: str,
    phone_type: str,
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
    """Preview or upsert one PerPhone record via POST /odata/v2/upsert.

    Required business keys: person_id_external, phone_type.
    fields accepts SF properties and known snake_case key/date aliases.
    Explicit keys and generated SFOData metadata override fields. Dates pass
    through in SF OData format; supply tenant-required properties in fields.
    No event reason or effective date is inferred. dry_run defaults to True;
    real writes require SF_ALLOW_WRITE=true, with no environment whitelist.
    """
    return upsert_ec_entity(
        "PerPhone",
        {"person_id_external": person_id_external, "phone_type": phone_type},
        instance,
        data_center,
        environment,
        auth_user_id,
        auth_password,
        fields,
        dry_run,
        request_id,
    )


@mcp.tool()
@sf_tool("upsert_per_address")
def upsert_per_address(
    instance: str,
    person_id_external: str,
    address_type: str,
    start_date: str,
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
    """Preview or upsert one PerAddressDEFLT record via POST /odata/v2/upsert.

    Required business keys: person_id_external, address_type, start_date.
    fields accepts SF properties and known snake_case key/date aliases.
    Explicit keys and generated SFOData metadata override fields. Dates pass
    through in SF OData format; supply tenant-required properties in fields.
    No event reason or effective date is inferred. dry_run defaults to True;
    real writes require SF_ALLOW_WRITE=true, with no environment whitelist.
    """
    return upsert_ec_entity(
        "PerAddressDEFLT",
        {"person_id_external": person_id_external, "address_type": address_type, "start_date": start_date},
        instance,
        data_center,
        environment,
        auth_user_id,
        auth_password,
        fields,
        dry_run,
        request_id,
    )


@mcp.tool()
@sf_tool("upsert_per_national_id")
def upsert_per_national_id(
    instance: str,
    person_id_external: str,
    country: str,
    card_type: str,
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
    """Preview or upsert one PerNationalId record via POST /odata/v2/upsert.

    Required business keys: person_id_external, country, card_type.
    fields accepts SF properties and known snake_case key/date aliases.
    Explicit keys and generated SFOData metadata override fields. Dates pass
    through in SF OData format; supply tenant-required properties in fields.
    No event reason or effective date is inferred. dry_run defaults to True;
    real writes require SF_ALLOW_WRITE=true, with no environment whitelist.
    """
    return upsert_ec_entity(
        "PerNationalId",
        {"person_id_external": person_id_external, "country": country, "card_type": card_type},
        instance,
        data_center,
        environment,
        auth_user_id,
        auth_password,
        fields,
        dry_run,
        request_id,
    )


PER_GLOBAL_INFO_COUNTRIES = frozenset(
    {
        "ARE", "ARG", "AUS", "AUT", "BEL", "BGD", "BRA", "CAN", "CHE", "CHL", "CHN", "COL",
        "CZE", "DEU", "DNK", "EGY", "ESP", "FIN", "FRA", "GBR", "GTM", "HKG", "HUN", "IDN",
        "IND", "IRL", "ITA", "JPN", "KOR", "LKA", "MEX", "MYS", "NLD", "NOR", "NZL", "OMN",
        "PER", "PHL", "POL", "PRT", "QAT", "ROU", "RUS", "SAU", "SGP", "SWE", "THA", "TUR",
        "TWN", "USA", "VEN", "VNM", "ZAF",
    }
)


@mcp.tool()
@sf_tool("upsert_emp_compensation")
def upsert_emp_compensation(
    instance: str,
    user_id: str,
    start_date: str,
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
    """Upsert one EmpCompensation record; defaults to a dry-run preview.

    Supply additional tenant properties through fields. Required keys and metadata
    override fields. Dates pass through unchanged. Real writes require
    dry_run=False and SF_ALLOW_WRITE=true; there is no environment whitelist.
    """
    return upsert_ec_entity(
        "EmpCompensation",
        {"user_id": user_id, "start_date": start_date},
        instance, data_center, environment, auth_user_id, auth_password,
        fields, dry_run, request_id,
    )


@mcp.tool()
@sf_tool("upsert_emp_pay_comp_recurring")
def upsert_emp_pay_comp_recurring(
    instance: str,
    user_id: str,
    start_date: str,
    seq_number: str | int,
    pay_component: str,
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
    """Upsert one EmpPayCompRecurring record; defaults to a dry-run preview.

    Supply additional tenant properties through fields. Required keys and metadata
    override fields. Dates pass through unchanged. Real writes require
    dry_run=False and SF_ALLOW_WRITE=true; there is no environment whitelist.
    """
    return upsert_ec_entity(
        "EmpPayCompRecurring",
        {"user_id": user_id, "start_date": start_date, "seq_number": seq_number, "pay_component": pay_component},
        instance, data_center, environment, auth_user_id, auth_password,
        fields, dry_run, request_id,
    )


@mcp.tool()
@sf_tool("upsert_emp_pay_comp_non_recurring")
def upsert_emp_pay_comp_non_recurring(
    instance: str,
    user_id: str,
    pay_date: str,
    pay_component_code: str,
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
    """Upsert one EmpPayCompNonRecurring record; defaults to a dry-run preview.

    Supply additional tenant properties through fields. Required keys and metadata
    override fields. Dates pass through unchanged. Real writes require
    dry_run=False and SF_ALLOW_WRITE=true; there is no environment whitelist.
    """
    return upsert_ec_entity(
        "EmpPayCompNonRecurring",
        {"user_id": user_id, "pay_date": pay_date, "pay_component_code": pay_component_code},
        instance, data_center, environment, auth_user_id, auth_password,
        fields, dry_run, request_id,
    )


@mcp.tool()
@sf_tool("upsert_emp_work_permit")
def upsert_emp_work_permit(
    instance: str,
    user_id: str,
    country: str,
    document_type: str,
    document_number: str,
    issue_date: str,
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
    """Upsert one EmpWorkPermit record; defaults to a dry-run preview.

    Supply additional tenant properties through fields. Required keys and metadata
    override fields. Dates pass through unchanged. Real writes require
    dry_run=False and SF_ALLOW_WRITE=true; there is no environment whitelist.
    """
    return upsert_ec_entity(
        "EmpWorkPermit",
        {
            "user_id": user_id, "country": country, "document_type": document_type,
            "document_number": document_number, "issue_date": issue_date,
        },
        instance, data_center, environment, auth_user_id, auth_password,
        fields, dry_run, request_id,
    )


@mcp.tool()
@sf_tool("upsert_emp_employment_termination")
def upsert_emp_employment_termination(
    instance: str,
    person_id_external: str,
    user_id: str,
    end_date: str,
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
    """Upsert one EmpEmploymentTermination record; defaults to a dry-run preview.

    Supply additional tenant properties through fields. Required keys and metadata
    override fields. Dates pass through unchanged. Real writes require
    dry_run=False and SF_ALLOW_WRITE=true; there is no environment whitelist.

    Dangerous: terminates employment; review all fields and dates with dry_run first.
    """
    return upsert_ec_entity(
        "EmpEmploymentTermination",
        {"person_id_external": person_id_external, "user_id": user_id, "end_date": end_date},
        instance, data_center, environment, auth_user_id, auth_password,
        fields, dry_run, request_id,
    )


@mcp.tool()
@sf_tool("upsert_emp_employment_higher_duty")
def upsert_emp_employment_higher_duty(
    instance: str,
    user_id: str,
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
    """Upsert one EmpEmploymentHigherDuty record; defaults to a dry-run preview.

    Supply additional tenant properties through fields. Required keys and metadata
    override fields. Dates pass through unchanged. Real writes require
    dry_run=False and SF_ALLOW_WRITE=true; there is no environment whitelist.

    Only userId is a metadata key; supply other properties through fields.
    """
    return upsert_ec_entity(
        "EmpEmploymentHigherDuty",
        {"user_id": user_id},
        instance, data_center, environment, auth_user_id, auth_password,
        fields, dry_run, request_id,
    )


@mcp.tool()
@sf_tool("upsert_user")
def upsert_user(
    instance: str,
    user_id: str,
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
    """Upsert one User record; defaults to a dry-run preview.

    Supply additional tenant properties through fields. Required keys and metadata
    override fields. Dates pass through unchanged. Real writes require
    dry_run=False and SF_ALLOW_WRITE=true; there is no environment whitelist.

    Large surface (login/HRIS hybrid): prefer Per*/Emp* for person/job data.
    The caller owns field choice; review with dry_run first.
    """
    return upsert_ec_entity(
        "User",
        {"user_id": user_id},
        instance, data_center, environment, auth_user_id, auth_password,
        fields, dry_run, request_id,
    )


@mcp.tool()
@sf_tool("upsert_per_global_info")
def upsert_per_global_info(
    instance: str,
    country_code: str,
    person_id_external: str,
    start_date: str,
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
    """Upsert one PerGlobalInfo{ISO3} record; defaults to a dry-run preview.

    Supply additional tenant properties through fields. Required keys and metadata
    override fields. Dates pass through unchanged. Real writes require
    dry_run=False and SF_ALLOW_WRITE=true; there is no environment whitelist.

    The selected ISO3 country is also the authoritative payload country.

    country_code must be an exact uppercase ISO3 code from the documented list.
    """
    if not country_code.strip():
        return {"error": "country_code must not be empty"}
    if country_code not in PER_GLOBAL_INFO_COUNTRIES:
        valid_types = sorted(PER_GLOBAL_INFO_COUNTRIES)
        return {
            "error": f"Unknown country_code '{country_code}'. Valid codes: {', '.join(valid_types)}",
            "valid_types": valid_types,
        }
    return upsert_ec_entity(
        f"PerGlobalInfo{country_code}",
        {"person_id_external": person_id_external, "start_date": start_date, "country": country_code},
        instance, data_center, environment, auth_user_id, auth_password,
        fields, dry_run, request_id,
    )
