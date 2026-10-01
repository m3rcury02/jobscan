"""Run: python -m pytest tests/

IT services adapters (2026-10-01). Response shapes are trimmed copies of what
each live endpoint returned that day; the network is mocked throughout.
"""
import json
from unittest.mock import MagicMock, patch

import pytest

import jobscan as J


def resp(json_body=None, text=""):
    r = MagicMock()
    r.raise_for_status = MagicMock()
    r.json = MagicMock(return_value=json_body)
    r.text = text
    return r


def test_service_firms_pass_the_company_filter_but_agencies_do_not():
    for co in ("Tata Consultancy Services", "Infosys", "Wipro", "HCLTech", "Cognizant"):
        assert J.company_ok(co)
    assert not J.company_ok("Randstad India")


@pytest.mark.parametrize("title, ok", [
    ("Staff Software Engineer", False), ("Senior Staff Engineer", False),
    ("Staff ML Engineer", False),
    ("EY - GDS Consulting - AI and DATA - Python Developer - Staff", True),
])
def test_staff_is_a_senior_grade_only_before_a_role_noun(title, ok):
    assert J.title_ok(title) is ok


def test_successfactors_total_reads_the_of_n_count():
    page = ('<span class="paginationLabel" aria-label="Results 1 – 25">Results '
            '<b>1 – 25</b> of <b>2,242</b></span>')
    m = J.SF_TOTAL.search(page)
    assert (m.group(1) or m.group(2)) == "2,242"


def test_infosys_maps_fields_and_experience_band():
    body = [{"postingTitle": "AI Engineer (Gen AI, Databricks)", "location": "BANGALORE",
             "country": "India", "referenceCode": "INFSYS-EXTERNAL-254173",
             "createdOn": "2026-09-30T13:08:37.461", "minExperienceLevel": 2,
             "maxExperienceLevel": 5, "postingDescription": "Build RAG pipelines.",
             "technicalRequirement": "Python", "rolesResponsibilities": None,
             "additionalResponsibility": "LangGraph"}]
    with patch.object(J.requests, "get", return_value=resp(body)):
        (j,) = J.fetch_infosys()
    assert j["location"] == "Bangalore, India" and j["posted"] == "2026-09-30"
    assert j["url"].endswith("jobReferenceCode=INFSYS-EXTERNAL-254173")
    assert J.yoe_band(j["title"], j["description"]) == (2, 5)


def test_zwayam_pages_by_row_offset():
    def row(code, title):
        return {"_source": {"jobTitle": title, "location": "Pune, Maharashtra, India",
                            "jobUrl": f"{code}-slug", "minYrsOfExperience": "2.0",
                            "maxYrsOfExperience": "4.0", "createdDate": "1790110211000",
                            "mediumDescriptionWithoutHtml": "Java microservices."}}
    pages = [
        {"data": {"data": [row(1, "Java Developer"), row(2, "Python Developer")],
                  "totalCount": 3, "hasMoreData": True}},
        {"data": {"data": [row(3, "Data Engineer")], "totalCount": 3, "hasMoreData": False}},
    ]
    with patch.object(J.requests, "post", side_effect=[resp(p) for p in pages]) as post:
        jobs = J.fetch_zwayam("https://careers.coforge.com/coforge/", "15173")
    offsets = [json.loads(c.kwargs["files"]["filterCri"][1])["paginationStartNo"]
               for c in post.call_args_list]
    assert offsets == [0, 2]
    assert post.call_args_list[0].kwargs["files"]["companyId"][1] == "MTUxNzM="
    assert jobs[0]["url"] == "https://careers.coforge.com/coforge/jobview/1-slug"
    assert J.yoe_band(jobs[0]["title"], jobs[0]["description"]) == (2, 4)


def test_tcs_switches_session_to_india_and_pages_newest_first():
    s = MagicMock()
    s.put.return_value = resp({"result": "Y"})
    s.post.side_effect = [
        resp({"data": {"totalJobs": "11", "jobs": [
            {"id": f"43{i}J", "jobTitle": "Java Developer", "location": "Chennai",
             "experience": "2-6", "skills": "Java"} for i in range(10)]}}),
        resp({"data": {"totalJobs": "11", "jobs": [
            {"id": "420J", "jobTitle": "Python Developer", "location": "Pune",
             "experience": "1-3", "skills": "Python"}]}}),
    ]
    with patch.object(J.requests, "Session", return_value=s):
        jobs = J.fetch_tcs()
    assert "/current/country/IN/" in s.put.call_args.args[0]
    assert len(jobs) == 11 and jobs[0]["location"] == "Chennai, India"
    assert jobs[0]["_detail"] == ("tcs", "430J")
    assert J.yoe_band(jobs[-1]["title"], jobs[-1]["description"]) == (1, 3)


def test_sfcsb_uses_csrf_token_and_facet_filter():
    s = MagicMock()
    s.get.return_value = resp(text='var CSRFToken = "tok-123";')
    s.post.return_value = resp({"totalJobs": 1, "jobSearchResult": [{"response": {
        "id": "159002", "urlTitle": "Software-Engineer",
        "unifiedStandardTitle": "Software Engineer", "unifiedStandardStart": "10/1/26",
        "custprimecity": "Noida", "custCountryRegion": ["India"]}}]})
    with patch.object(J.requests, "Session", return_value=s):
        (j,) = J.fetch_sfcsb("careers.hcltech.com", "custCountryRegion=India")
    body = s.post.call_args.kwargs["json"]
    assert body["facetFilters"] == {"custCountryRegion": ["India"]} and body["location"] == ""
    assert s.post.call_args.kwargs["headers"]["X-CSRF-Token"] == "tok-123"
    assert j["posted"] == "2026-10-01" and j["location"] == "Noida, India"
    assert j["url"] == "https://careers.hcltech.com/job/Software-Engineer/159002-en_US/"


def test_ripplehire_passes_region_filter_from_tenant():
    s = MagicMock()
    s.post.return_value = resp({"totalJobCount": 1, "jobVoList": [{
        "jobSeq": "898588", "jobTitle": "Software Engineer", "locations": "Bengaluru",
        "jobLocation": "India", "jobMinExp": 2, "jobMaxExp": 4}]})
    with patch.object(J.requests, "Session", return_value=s):
        (j,) = J.fetch_ripplehire("ltimindtree", "TOKEN|geo=India")
    params = json.loads(s.post.call_args.kwargs["data"]["careerSiteUrlParams"])
    assert params["geo"] == "India" and params["token"] == "TOKEN"
    assert j["url"].endswith("#detail/job/898588") and j["location"] == "Bengaluru, India"
    assert J.yoe_band(j["title"], j["description"]) == (2, 4)


def test_phenom_reads_site_ids_from_page_then_widgets():
    page = resp(text='{"refNum":"QGRQGAGLOBAL","pageId":"page3","locale":"en_global",'
                     '"country":"global"}')
    widgets = resp({"refineSearch": {"totalHits": 1, "data": {"jobs": [{
        "title": "Embedded Engineer", "jobId": "P-1", "location": "Pune, India",
        "postedDate": "2026-09-30T00:00:00.000+0000", "descriptionTeaser": "x" * 300}]}}})
    with patch.object(J.requests, "get", return_value=page), \
            patch.object(J.requests, "post", return_value=widgets) as post:
        (j,) = J.fetch_phenom("https://careers.quest-global.com/global/en/search-results")
    assert post.call_args.kwargs["json"]["refNum"] == "QGRQGAGLOBAL"
    assert j["url"] == "https://careers.quest-global.com/global/en/job/P-1"
    assert j["_teaser"] and j["posted"] == "2026-09-30"


def test_oracle_pages_newest_first_past_200():
    def page(n):
        return {"items": [{"TotalJobsCount": 220, "requisitionList": [
            {"Id": str(i), "Title": "Developer", "PrimaryLocation": "Pune, India",
             "PostedDate": "2026-09-30"} for i in range(n)]}]}
    with patch.object(J, "get_json", side_effect=[page(200), page(20)]) as gj:
        jobs = J.fetch_oracle("CX_1", "fa-etvl-saasfaprod1.fa.ocs.oraclecloud.com")
    assert len(jobs) == 220
    assert "sortBy=POSTING_DATES_DESC" in gj.call_args_list[0].args[0]
    assert "offset=200" in gj.call_args_list[1].args[0]


def test_teaser_is_replaced_by_the_fetched_jd_not_kept():
    j = {"title": "Developer", "description": "teaser " * 60, "_teaser": True,
         "_detail": ("phenom", "https://x/job/1"), "company": "Lilly"}
    with patch.dict(J.DETAIL_TEXT, {"phenom": lambda url: "Full JD. 2-4 years of experience."}):
        J.enrich_all([j])
    assert j["description"] == "Full JD. 2-4 years of experience."


def test_more_matches_caps_each_company():
    def kept(i, co):
        return {"title": f"Java Developer {i}", "company": co, "location": "Pune",
                "referral": False, "url": f"https://x/{co}/{i}",
                "description": "Java Spring Boot Kafka " * 20, "age": 0,
                "FitScore": 50 - i, "FitReason": "Overlap: java", "Skills": ["java"],
                "Gaps": [], "yoe_band": (2, 4), "yoe_fit": "in-band", "GapNote": ""}
    jobs = [kept(i, "TCS") for i in range(7)]
    text, _ = J.build_digest(jobs, {}, 7, 0, "2026-10-01", 2)
    assert text.count("- TCS, Pune") == J.MORE_PER_COMPANY
    assert "+3 more at TCS" in text


def test_call_retries_a_reset_connection_then_succeeds():
    ok = resp({"fine": True})
    flaky = MagicMock(side_effect=[J.requests.ConnectionError("reset by peer"), ok])
    with patch.object(J.time, "sleep"):
        assert J._call(flaky, "https://x") is ok
    assert flaky.call_count == 2
