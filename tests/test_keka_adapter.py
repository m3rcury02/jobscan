"""Run: python -m pytest tests/

fetch_keka has to handle two Keka template generations that coexist across
live tenants (see jobscan.py fetch_keka docstring):
  - old: shell embeds a GUID `identifier`, jobs come from
    /careers/api/embedjobs/<portal>/active/<id>
  - new (cdn.keka.com/careers/v/2026/): no GUID at all, jobs come from
    /careers/api/jobs/<portal>/active keyed only by portal name
Both are mocked here so a future template change is caught without hitting
the network.
"""
from unittest.mock import patch, Mock

import jobscan as J

OLD_SHELL = """
<html><body>
<script>var config = {identifier: 'a51996b6-2361-4447-93c7-e013b233d713', portalName: 'acme'};</script>
</body></html>
"""

NEW_SHELL = """
<!DOCTYPE html>
<html><head><meta name="portalName"></head><body></body></html>
"""

JOB_PAYLOAD = [{
    "id": 141780,
    "title": "Backend Engineer",
    "jobLocations": [{"city": "Bengaluru", "countryName": "India"}],
    "experience": "2-4 Years",
    "description": "<p>Build things.</p>",
    "publishedOn": "2026-09-15T13:00:44.52Z",
}]


def _resp(text=None, json_body=None):
    r = Mock()
    r.raise_for_status = Mock()
    if text is not None:
        r.text = text
    if json_body is not None:
        r.json = Mock(return_value=json_body)
    return r


def test_old_template_uses_embedjobs_guid_endpoint():
    calls = []

    def fake_get(url, headers=None, timeout=None):
        calls.append(url)
        if url.endswith("/careers/"):
            return _resp(text=OLD_SHELL)
        if "embedjobs/acme/active/a51996b6-2361-4447-93c7-e013b233d713" in url:
            return _resp(text="", json_body=JOB_PAYLOAD)
        raise AssertionError(f"unexpected URL {url}")

    with patch.object(J.requests, "get", side_effect=fake_get):
        jobs = J.fetch_keka("acme")

    assert len(jobs) == 1
    assert jobs[0]["title"] == "Backend Engineer"
    assert any("embedjobs/acme/active" in u for u in calls)


def test_new_template_falls_back_to_portal_only_endpoint():
    calls = []

    def fake_get(url, headers=None, timeout=None):
        calls.append(url)
        if url.endswith("/careers/"):
            return _resp(text=NEW_SHELL)
        if url.endswith("/careers/api/jobs/default/active"):
            return _resp(text="", json_body=JOB_PAYLOAD)
        raise AssertionError(f"unexpected URL {url}")

    with patch.object(J.requests, "get", side_effect=fake_get):
        jobs = J.fetch_keka("newspace")

    assert len(jobs) == 1
    assert jobs[0]["location"] == "Bengaluru, India"
    assert any(u.endswith("/careers/api/jobs/default/active") for u in calls)
