"""Workforce SCIM transport and bounded exact lookup; shares the OData session.

No automatic HTTP retries (especially POST). Never expose raw error bodies,
request exceptions, credentials, or response fields outside the summary allowlist.
"""

import json
import time
import uuid
from typing import Any

import requests

from sf_mcp.auth import resolve_credentials
from sf_mcp.client import check_write_allowed, get_session
from sf_mcp.config import DEFAULT_TIMEOUT, get_api_host
from sf_mcp.logging_config import audit_log
from sf_mcp.rate_limiter import RateLimitExceeded, get_rate_limiter

USERS_ENDPOINT = "/rest/iam/scim/v2/Users"
SF_SCHEMA = "urn:ietf:params:scim:schemas:extension:successfactors:2.0:User"
SCHEMAS = (
    "urn:ietf:params:scim:schemas:core:2.0:User",
    SF_SCHEMA,
    "urn:ietf:params:scim:schemas:extension:sap:2.0:User",
    "urn:ietf:params:scim:schemas:extension:enterprise:2.0:User",
)
ERROR_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:Error"


def user_summary(resource: dict) -> dict[str, Any]:
    """Return only supported scalar identity fields and sanitized email entries."""
    summary = {
        k: resource[k]
        for k in ("id", "userName", "userType", "active")
        if k in resource and isinstance(resource[k], (str, bool))
    }
    extension = resource.get(SF_SCHEMA)
    if isinstance(extension, dict):
        summary.update(
            {k: extension[k] for k in ("personIdExternal", "perPersonUuid") if isinstance(extension.get(k), str)}
        )
    emails = resource.get("emails")
    if isinstance(emails, list):
        summary["emails"] = [
            {k: entry[k] for k in ("value", "type", "primary") if k in entry and isinstance(entry[k], (str, bool))}
            for entry in emails
            if isinstance(entry, dict)
        ]
    return summary


def scim_request(
    instance: str,
    data_center: str,
    environment: str,
    auth_user_id: str,
    auth_password: str,
    *,
    method: str = "GET",
    params: dict | None = None,
    payload: dict | None = None,
    dry_run: bool = True,
    request_id: str | None = None,
) -> dict[str, Any]:
    """Internal GET/POST transport. GET is always read-only; dry_run gates POST."""
    req_id = request_id or str(uuid.uuid4())[:8]
    started = time.time()
    user_id = ""
    allow_write = False

    def finish(outcome: str, **result: Any) -> dict[str, Any]:
        audit_log(
            event_type="api_mutation" if method == "POST" else "api_request",
            instance=instance,
            user_id=user_id,
            status="success" if outcome in {"preview", "success", "created"} else "error",
            details={
                "method": method,
                "endpoint": USERS_ENDPOINT,
                "outcome": outcome,
                "dry_run": dry_run if method == "POST" else False,
                "allow_write": allow_write,
                "http_status": result.get("http_status"),
            },
            request_id=req_id,
            duration_ms=(time.time() - started) * 1000,
        )
        return {"outcome": outcome, "request_id": req_id, **result}

    if method not in {"GET", "POST"}:
        return finish("invalid_input", error="Only GET and POST are supported.")
    try:
        if method == "POST":
            allow_write = check_write_allowed(dry_run)
        url = f"https://{get_api_host(data_center, environment)}{USERS_ENDPOINT}"
    except PermissionError as exc:
        return finish("blocked", error=str(exc))
    except ValueError:
        return finish("invalid_input", error="Invalid data_center or environment.")

    if method == "POST" and dry_run:
        return finish(
            "preview",
            method=method,
            url=url,
            endpoint=USERS_ENDPOINT,
            payload=payload,
            params=params,
            dry_run=True,
            allow_write=allow_write,
            message="Payload preview only; not validated by SAP. No HTTP was performed.",
        )

    user_id, password = resolve_credentials(auth_user_id, auth_password)
    if not user_id or not password:
        return finish("unauthorized", error="Missing credentials: auth_user_id and auth_password are required.")
    try:
        get_rate_limiter().check_and_record(instance, request_id=req_id)
    except RateLimitExceeded:
        return finish("rate_limited", error="Local rate limit exceeded; no request was sent.")

    headers = {"Accept": "application/scim+json, application/json"}
    options: dict[str, Any] = {"params": params}
    if method == "POST":
        headers["Content-Type"] = "application/scim+json"
        options["json"] = payload
    try:
        response = get_session().request(
            method,
            url,
            auth=(f"{user_id}@{instance}", password),
            headers=headers,
            timeout=DEFAULT_TIMEOUT,
            allow_redirects=False,
            **options,
        )
    except requests.exceptions.RequestException:
        if method == "POST":
            return finish("unknown", error="The write may have succeeded. Verify with get_scim_user before retrying.")
        return finish("error", error="SCIM query failed; no absence can be inferred. Retry the query.")

    status = response.status_code
    try:
        body = response.json()
    except ValueError:
        body = None
    schemas = body.get("schemas") if isinstance(body, dict) else None
    scim_error = isinstance(schemas, list) and ERROR_SCHEMA in schemas
    error_status = str(body.get("status")) if scim_error else str(status)
    # HTTP auth failures take precedence over malformed or contradictory bodies.
    if status in {401, 403, 409}:
        error_status = str(status)
    if error_status in {"401", "403", "409"}:
        outcome, message = {
            "401": ("unauthorized", "SCIM authentication failed. Check credentials."),
            "403": (
                "forbidden",
                "SCIM permission denied. Check SCIM RBP and target population; OData access is separate.",
            ),
            "409": ("conflict", "SCIM account conflict. Verify with get_scim_user; POST was not retried."),
        }[error_status]
        return finish(outcome, error=message, http_status=status)
    if status == 429:
        return finish("rate_limited", error="SCIM rate limit exceeded; request was not retried.", http_status=status)
    if not 200 <= status < 300 or scim_error:
        outcome = "unknown" if method == "POST" and status >= 500 else "error"
        return finish(
            outcome,
            http_status=status,
            error="SCIM request failed. Verify via query before retrying a create; no automatic retry.",
        )
    if method == "POST":
        # A 202 or unusable success body cannot prove a completed account creation.
        if status == 202 or not isinstance(body, dict) or not isinstance(body.get("id"), str) or not body["id"].strip():
            return finish(
                "unknown",
                http_status=status,
                error="POST returned success without a confirmed account. Verify with get_scim_user.",
            )
        return finish("created", user=user_summary(body), http_status=status, dry_run=False)
    if not isinstance(body, dict):
        return finish("incomplete", error="SCIM query did not return a JSON object.", http_status=status)
    return finish("success", data=body, http_status=status)


def lookup_scim_user(*, attribute: str, value: str, **connection: Any) -> dict[str, Any]:
    """One page, count=2: enough to detect ambiguity, never assume partial uniqueness."""
    if attribute not in {"userName", "personIdExternal", "perPersonUuid"}:
        return {"outcome": "invalid_input", "error": "Unsupported lookup attribute."}
    path = attribute if attribute == "userName" else f"{SF_SCHEMA}:{attribute}"
    result = scim_request(
        **connection,
        params={"filter": f"{path} eq {json.dumps(value, ensure_ascii=False)}", "startIndex": 1, "count": 2},
    )
    if result["outcome"] != "success":
        return result
    body = result["data"]
    resources = body.get("Resources", [] if body.get("totalResults") == 0 else None)
    total = body.get("totalResults")
    base = {"request_id": result["request_id"], "totalResults": total if type(total) is int else None}
    if not isinstance(resources, list) or any(not isinstance(r, dict) for r in resources):
        return {**base, "outcome": "incomplete", "error": "Invalid SCIM Resources; absence is unconfirmed."}
    users = [user_summary(r) for r in resources[:2]]
    if len(resources) > 1 or (type(total) is int and total > 1):
        return {
            **base,
            "outcome": "multiple",
            "users": users,
            "complete": type(total) is int and total == len(resources),
        }
    if (total is not None and (type(total) is not int or total < 0)) or body.get("startIndex", 1) != 1:
        return {**base, "outcome": "incomplete", "users": users, "error": "Invalid SCIM pagination metadata."}
    if not resources and total in (None, 0):
        return {**base, "outcome": "not_found"}
    if total == 1 and len(resources) == 1 and isinstance(resources[0].get("id"), str) and resources[0]["id"].strip():
        return {**base, "outcome": "found", "user": users[0]}
    return {
        **base,
        "outcome": "incomplete",
        "users": users,
        "error": "Missing or inconsistent totalResults/Resources; uniqueness and absence are unconfirmed.",
    }
