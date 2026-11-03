"""Tests for sf_mcp.client module."""

from unittest.mock import MagicMock, patch

import pytest

from sf_mcp.cache import get_cache
from sf_mcp.client import (
    check_write_allowed,
    make_metadata_request,
    make_odata_mutation,
    make_odata_request,
    make_service_doc_request,
)
from sf_mcp.config import DEFAULT_TIMEOUT, writes_allowed


class TestMakeOdataMutation:
    def mutate(self, **kwargs):
        return make_odata_mutation(
            "test-instance",
            "/odata/v2/Example('1')",
            "DC55",
            "production",
            "admin",
            "password",
            **kwargs,
        )

    @pytest.mark.parametrize("value", [None, "false", "invalid", "1"])
    @patch("sf_mcp.client.get_session")
    def test_disabled_no_http(self, mock_get_session, monkeypatch, value):
        monkeypatch.delenv("SF_ALLOW_WRITE", raising=False)
        if value is not None:
            monkeypatch.setenv("SF_ALLOW_WRITE", value)
        assert writes_allowed() is False
        result = self.mutate(dry_run=False)
        assert "SF_ALLOW_WRITE=true" in result["error"]
        mock_get_session.assert_not_called()
        with pytest.raises(PermissionError):
            check_write_allowed(dry_run=False)

    @pytest.mark.parametrize("enabled", ["false", "true"])
    @patch("sf_mcp.client.get_session")
    @patch("sf_mcp.client.audit_log")
    def test_preview_no_http(self, mock_audit, mock_get_session, monkeypatch, enabled):
        monkeypatch.setenv("SF_ALLOW_WRITE", enabled)
        result = make_odata_mutation(
            "test-instance",
            "/odata/v2/Example",
            "DC55",
            "production",
            "",
            "",
            payload={"name": "example"},
        )
        assert result == {
            "method": "POST",
            "url": "https://api55.sapsf.eu/odata/v2/Example",
            "endpoint": "/odata/v2/Example",
            "payload": {"name": "example"},
            "params": None,
            "dry_run": True,
            "allow_write": enabled == "true",
        }
        mock_get_session.assert_not_called()
        details = mock_audit.call_args.kwargs["details"]
        assert details["outcome"] == "preview"
        assert details["dry_run"] is True
        assert details["allow_write"] == (enabled == "true")

    @pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "MERGE", "DELETE"])
    @patch("sf_mcp.client.get_session")
    def test_real_write(self, mock_get_session, monkeypatch, method):
        monkeypatch.setenv("SF_ALLOW_WRITE", "true")
        response = mock_get_session.return_value.request.return_value
        response.status_code = 201
        response.text = '{"d": {"name": "example"}}'
        response.json.return_value = {"d": {"name": "example"}}
        cache = get_cache()
        cache.put("test-instance", "/odata/v2/Example", None, {"old": True}, ttl=60)
        cache.put("other-instance", "/odata/v2/Example", None, {"keep": True}, ttl=60)

        result = self.mutate(method=method.lower(), payload={"name": "example"}, dry_run=False)

        assert result == response.json.return_value
        mock_get_session.return_value.request.assert_called_once_with(
            method,
            "https://api55.sapsf.eu/odata/v2/Example('1')",
            auth=("admin@test-instance", "password"),
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            params=None,
            json={"name": "example"},
            timeout=DEFAULT_TIMEOUT,
            allow_redirects=False,
        )
        assert cache.get("test-instance", "/odata/v2/Example") is None
        assert cache.get("other-instance", "/odata/v2/Example") == {"keep": True}

    @pytest.mark.parametrize("status", [200, 204])
    @patch("sf_mcp.client.get_session")
    def test_empty_success(self, mock_get_session, monkeypatch, status):
        monkeypatch.setenv("SF_ALLOW_WRITE", "true")
        response = mock_get_session.return_value.request.return_value
        response.status_code = status
        response.text = ""
        assert self.mutate(dry_run=False) == {"success": True, "http_status": status, "dry_run": False}

    @patch("sf_mcp.client.get_session")
    @patch("sf_mcp.client.get_cache")
    def test_http_error_preserves_cache(self, mock_cache, mock_get_session, monkeypatch):
        monkeypatch.setenv("SF_ALLOW_WRITE", "true")
        response = mock_get_session.return_value.request.return_value
        response.status_code = 500
        response.text = "Internal Server Error"
        assert self.mutate(dry_run=False)["error"] == "HTTP 500"
        mock_cache.assert_not_called()

    @patch("sf_mcp.client.get_session")
    def test_invalid_method_no_http(self, mock_get_session, monkeypatch):
        monkeypatch.setenv("SF_ALLOW_WRITE", "true")
        assert "Unsupported mutation method" in self.mutate(method="GET", dry_run=False)["error"]
        mock_get_session.assert_not_called()


class TestMakeOdataRequest:
    @patch("sf_mcp.client.get_session")
    def test_success(self, mock_get_session):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = '{"d": {"results": []}}'
        mock_resp.json.return_value = {"d": {"results": []}}
        mock_session = mock_get_session.return_value
        mock_session.get.return_value = mock_resp

        result = make_odata_request(
            "test-instance",
            "/odata/v2/User",
            "DC55",
            "production",
            "admin",
            "password123",
            {"$format": "json"},
            "test123",
        )

        assert "error" not in result
        assert result == {"d": {"results": []}}
        mock_session.get.assert_called_once()

    @patch("sf_mcp.client.get_session")
    def test_401_returns_error(self, mock_get_session):
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_get_session.return_value.get.return_value = mock_resp

        result = make_odata_request(
            "test-instance",
            "/odata/v2/User",
            "DC55",
            "production",
            "admin",
            "wrong",
            None,
            "test123",
        )

        assert result["error"] == "HTTP 401"

    @patch("sf_mcp.client.get_session")
    def test_500_returns_error(self, mock_get_session):
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "Internal Server Error"
        mock_get_session.return_value.get.return_value = mock_resp

        result = make_odata_request(
            "test-instance",
            "/odata/v2/User",
            "DC55",
            "production",
            "admin",
            "password",
            None,
            "test123",
        )

        assert "HTTP 500" in result["error"]

    def test_missing_credentials(self):
        result = make_odata_request(
            "test-instance",
            "/odata/v2/User",
            "DC55",
            "production",
            "",
            "",
            None,
            "test123",
        )

        assert "Missing credentials" in result["error"]

    def test_invalid_dc(self):
        result = make_odata_request(
            "test-instance",
            "/odata/v2/User",
            "DC99",
            "production",
            "admin",
            "password",
            None,
            "test123",
        )

        assert "Invalid data_center" in result["error"]

    @patch("sf_mcp.client.get_session")
    def test_empty_response(self, mock_get_session):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "   "
        mock_get_session.return_value.get.return_value = mock_resp

        result = make_odata_request(
            "test-instance",
            "/odata/v2/User",
            "DC55",
            "production",
            "admin",
            "password",
            None,
            "test123",
        )

        assert "Empty response" in result["error"]

    @patch("sf_mcp.client.get_session")
    def test_request_exception(self, mock_get_session):
        import requests

        mock_get_session.return_value.get.side_effect = requests.exceptions.ConnectionError("Connection refused")

        result = make_odata_request(
            "test-instance",
            "/odata/v2/User",
            "DC55",
            "production",
            "admin",
            "password",
            None,
            "test123",
        )

        assert "Request failed" in result["error"]

    @patch("sf_mcp.client.get_session")
    def test_builds_correct_url(self, mock_get_session):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = '{"d": {}}'
        mock_resp.json.return_value = {"d": {}}
        mock_session = mock_get_session.return_value
        mock_session.get.return_value = mock_resp

        make_odata_request(
            "mycompany",
            "/odata/v2/User",
            "DC55",
            "production",
            "admin",
            "password123",
            None,
            "test123",
        )

        call_args = mock_session.get.call_args
        assert call_args.kwargs["auth"] == ("admin@mycompany", "password123")
        assert "api55.sapsf.eu" in call_args.args[0]


class TestMakeMetadataRequest:
    @patch("sf_mcp.client.get_session")
    def test_success(self, mock_get_session):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "<root><child>test</child></root>"
        mock_get_session.return_value.get.return_value = mock_resp

        result = make_metadata_request(
            "test-instance",
            "User",
            "DC55",
            "production",
            "admin",
            "password",
            "test123",
        )

        assert result is not None
        assert "error" not in result

    def test_missing_credentials(self):
        result = make_metadata_request(
            "test-instance",
            "User",
            "DC55",
            "production",
            "",
            "",
            "test123",
        )

        assert "Missing credentials" in result["error"]


class TestMakeServiceDocRequest:
    @patch("sf_mcp.client.get_session")
    def test_success(self, mock_get_session):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"d": {"EntitySets": ["User", "EmpJob"]}}
        mock_get_session.return_value.get.return_value = mock_resp

        result = make_service_doc_request(
            "test-instance",
            "DC55",
            "production",
            "admin",
            "password",
            "test123",
        )

        assert "error" not in result
        assert "d" in result
