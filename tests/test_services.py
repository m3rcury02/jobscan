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


@pytest.mark.parametrize("title, senior", [
    ("Staff Software Engineer", True), ("Senior Staff Software Engineer", True),
    ("Staff ML Engineer", True),
    ("EY - GDS Consulting - AI and DATA - Python Developer - Staff", False),
])
def test_staff_is_a_senior_grade_only_before_a_role_noun(title, senior):
    # judged on the JD's band like "Senior", not dropped on the title
    assert J.title_ok(title)
    assert J.is_senior_title(title) is senior


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


def test_tcs_echoes_xsrf_token_and_pages_until_last():
    # /candidate/next API (2026-10-06 shape): the session GET hands back the
    # XSRF token in a header; search pages are Spring "content" pages
    s = MagicMock()
    s.headers = {}
    hello = resp({"result": True})
    hello.headers = {"x-next-xsrf-token": "tok-1"}
    s.get.return_value = hello
    s.post.side_effect = [
        resp({"payload": {"last": False, "content": [
            {"id": f"43{i}J", "title": "Java Developer", "location": "Chennai",
             "minimumExperienceInYears": 2, "maximumExperienceInYears": 6,
             "skills": ["Java"], "walkIn": False} for i in range(50)]}}),
        resp({"payload": {"last": True, "content": [
            {"id": "420J", "title": "Python Developer", "location": "Pune",
             "minimumExperienceInYears": 1, "maximumExperienceInYears": 3,
             "skills": ["Python", "Django"], "walkIn": False}]}}),
    ]
    with patch.object(J.requests, "Session", return_value=s):
        jobs = J.fetch_tcs()
    assert s.headers["next-xsrf-token"] == "tok-1"
    assert s.post.call_args.args[0].endswith("/api/en-IN/search/jobs")
    assert [c.kwargs["json"]["page"] for c in s.post.call_args_list] == [1, 2]
    assert s.post.call_args.kwargs["json"]["resultsPerPage"] in (10, 25, 50)
    assert len(jobs) == 51 and jobs[0]["location"] == "Chennai, India"
    assert jobs[0]["url"] == "https://ibegin.tcsapps.com/candidate/next/en-IN/jobs/430J"
    assert jobs[0]["_detail"] == ("tcs", "430J")
    assert "Django" in jobs[-1]["description"]
    assert J.yoe_band(jobs[-1]["title"], jobs[-1]["description"]) == (1, 3)


def test_tcs_detail_drops_the_suffix_and_routes_walk_ins():
    r = resp({"payload": {"description": "<p>Build APIs</p>", "skilldetail": "Java"}})
    with patch.object(J.requests, "get", return_value=r) as get:
        assert "Build APIs" in J._detail_tcs("434103J")
        J._detail_tcs("5120W")
    urls = [c.args[0] for c in get.call_args_list]
    assert urls[0].endswith("/api/en-IN/job/desc/434103")
    assert urls[1].endswith("/api/en-IN/job/desc/walkin/5120")


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


TECHM_PAGE = '''
<input type="hidden" name="__VIEWSTATE" id="__VIEWSTATE" value="vs1" />
<input type="hidden" name="ctl00$ContentPlaceHolder1$DataListRecommended$ctl00$HdnJobCode"
 id="ctl00_ContentPlaceHolder1_DataListRecommended_ctl00_HdnJobCode" value="87316" /> <span>IT </span>
<div style="margin-bottom: 5px;"> Sr. Software Engineer </div>
<p style="font-size: 12px;"> <b>Skill Set </b>: Java <br /> <b>Experience</b> : 2.00-4.00 Years
 <br /> <b>Location</b> : HYDERABAD </p>
<a href="javascript:__doPostBack(&#39;ctl00$ContentPlaceHolder1$rptPager$ctl01$lnkPage&#39;,&#39;&#39;)" id="p">2</a>
'''


def test_techmahindra_posts_the_form_and_parses_cards():
    s = MagicMock()
    s.get.return_value = resp(text=TECHM_PAGE)
    s.post.side_effect = [resp(text=TECHM_PAGE), resp(text=TECHM_PAGE)]  # page 2 repeats
    with patch.object(J.requests, "Session", return_value=s):
        jobs = J.fetch_techmahindra("IND")
    assert len(jobs) == 1                       # a repeated page ends the walk
    j = jobs[0]
    assert j["title"] == "Sr. Software Engineer" and j["location"] == "Hyderabad, India"
    assert j["url"].endswith("#job-ref-87316")
    assert J.yoe_band(j["title"], j["description"]) == (2, 4)
    second = s.post.call_args_list[1].kwargs["data"]
    assert second["__EVENTTARGET"].endswith("rptPager$ctl01$lnkPage")
    assert second["__VIEWSTATE"] == "vs1"


AVATURE_PAGE = '''
<article class="article--result " data-total="1">
 <h3 class="article__header__text__title"> <a class="link"
  href="https://usijobs.deloitte.com/en_US/careersUSI/JobDetail/X-Engineer/369235"> Software Engineer </a> </h3>
 <div class="article__header__text__subtitle"> <span> Deloitte US – India Offices </span> |
  <span>Hyderabad, Telangana, India</span> </div>
 <a href="https://usijobs.deloitte.com/en_US/careersUSI/JobDetail/X-Engineer/369235">Read more</a>
</article>
'''


def test_avature_reads_cards_prefers_the_city_line_and_stops_at_total():
    with patch.object(J.requests, "Session") as S:
        S.return_value.get.return_value = resp(text=AVATURE_PAGE)
        (j,) = J.fetch_avature("https://usijobs.deloitte.com/en_US/careersUSI/SearchJobs", "India")
    assert j["title"] == "Software Engineer"
    assert j["location"] == "Hyderabad, Telangana, India"
    assert j["_detail"][0] == "html"
    assert S.return_value.get.call_count == 1


SM_PAGE = '''<span class="total_results">1</span>
<div id="job_list_79930" class="job_list_row">
 <p><a href="https://v.selectminds.com/jobs/data-engineer-79930" class="job_link font_bold">Data Engineer</a></p>
 <span class="font_bold">Location:</span> <span class="location">
   Chennai, Tamil Nadu, India </span>
 <p class="jlr_description">Owns the data pipelines</p>
</div>'''


def test_selectminds_parses_search_rows():
    with patch.object(J.requests, "Session") as S:
        first = resp(text=SM_PAGE)
        first.url = "https://v.selectminds.com/jobs/search/2311639"
        S.return_value.get.return_value = first
        (j,) = J.fetch_selectminds("https://v.selectminds.com")
    assert j["title"] == "Data Engineer" and j["location"] == "Chennai, Tamil Nadu, India"
    assert j["url"].endswith("data-engineer-79930") and j["_teaser"]


def test_jibe_pages_api_and_names_the_unit():
    body = {"totalCount": 1, "jobs": [{"data": {
        "slug": "145047", "language": "en-us", "title": "Software Engineer",
        "tags2": ["Epsilon"], "city": "Bengaluru", "country": "India",
        "posted_date": "2026-09-20T12:44:00+0000", "description": "2-4 years of experience in Java.",
        "qualifications": "Spring Boot"}}]}
    with patch.object(J.requests, "get", return_value=resp(body)) as get:
        (j,) = J.fetch_jibe("careers.publicisgroupe.com", "India")
    assert get.call_args.kwargs["params"]["location"] == "India"
    assert j["title"] == "Software Engineer [Epsilon]" and j["posted"] == "2026-09-20"
    assert j["url"] == "https://careers.publicisgroupe.com/jobs/145047?lang=en-us"
    assert J.yoe_band(j["title"], j["description"]) == (2, 4)


RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>Jobs</title>
<item><title>Software Engineer</title>
<link>https://careers.cognizant.com/india-en/jobs/00070622251/software-engineer/</link>
<description>&lt;p&gt;Chennai - 2-4 years of experience&lt;/p&gt;</description>
<pubDate>Tue, 30 Sep 2026 10:00:00 GMT</pubDate></item>
<item><title>Sr Developer</title><link>https://careers.cognizant.com/india-en/jobs/1/sr-developer/</link>
<category>Hyderabad, Telangana, India</category></item>
</channel></rss>"""


def test_rss_feed_items_dates_and_locations():
    r = resp()
    r.content = RSS
    with patch.object(J.requests, "get", return_value=r):
        a, b = J.fetch_rss("https://careers.cognizant.com/india-en/jobs/xml/?rss=true", "India")
    assert a["location"] == "Chennai, India" and a["posted"] == "2026-09-30"
    assert b["location"] == "Hyderabad, Telangana, India"      # no second ", India"
    assert a["_detail"] == ("html", a["url"]) and a["_teaser"]


JOBFEED = b"""<?xml version="1.0" encoding="utf-8"?><source><publisher>Cognizant</publisher>
<job><title><![CDATA[Software Engineer]]></title><date><![CDATA[Thu, 01 Oct 2026 04:09:13 GMT]]></date>
<url><![CDATA[https://careers.cognizant.com/india-en/jobs/00070622251/software-engineer/]]></url>
<city><![CDATA[Chennai]]></city><state><![CDATA[Tamil Nadu]]></state><country><![CDATA[India]]></country>
<description><![CDATA[<p>""" + b"Build Java microservices. 2-4 years of experience. " * 8 + b"""</p>]]></description></job>
<job><title><![CDATA[Frontier Engineer]]></title><url><![CDATA[https://x/uk]]></url>
<city><![CDATA[London,UK]]></city><country><![CDATA[United Kingdom]]></country></job>
</source>"""


def test_indeed_style_job_feed_filters_country_and_keeps_full_jd():
    r = resp()
    r.content = JOBFEED
    with patch.object(J.requests, "get", return_value=r):
        (j,) = J.fetch_rss("https://careers.cognizant.com/india-en/jobs/xml/?rss=true", "India")
    assert j["location"] == "Chennai, Tamil Nadu, India" and j["posted"] == "2026-10-01"
    assert J.has_jd(j) and "_detail" not in j        # the feed carries the whole JD
    assert J.yoe_band(j["title"], j["description"]) == (2, 4)


def test_ripplehire_renews_a_session_that_returns_empty_bodies():
    # 2026-10-04: a session without JSESSIONID gets 200 + "" on every call
    empty = resp()
    empty.json.side_effect = ValueError("Expecting value: line 1 column 1 (char 0)")
    good = resp({"totalJobCount": 1, "jobVoList": [{
        "jobSeq": "1", "jobTitle": "Backend Engineer", "locations": "Pune"}]})
    broken, fresh = MagicMock(), MagicMock()
    broken.post.return_value = empty
    fresh.post.return_value = good
    with patch.object(J.requests, "Session", side_effect=[broken, fresh]):
        (j,) = J.fetch_ripplehire("altimetrik", "TOKEN")
    assert j["title"] == "Backend Engineer"


def test_ripplehire_gives_up_after_two_renewals():
    empty = resp()
    empty.json.side_effect = ValueError("Expecting value")
    s = MagicMock()
    s.post.return_value = empty
    with patch.object(J.requests, "Session", return_value=s), pytest.raises(ValueError):
        J.fetch_ripplehire("altimetrik", "TOKEN")


@pytest.mark.parametrize("bullets, loc", [
    (["R00336753", "Bengaluru, Bdc4C"], "Bengaluru, Bdc4C"),          # Accenture
    (["Bangalore", "Karnataka", "JREQ203723"], "Bangalore, Karnataka"),  # Thomson Reuters
    (["SR-15059"], ""),                                                # Fractal
    (None, ""),
])
def test_workday_bullet_location_skips_requisition_ids(bullets, loc):
    assert J._wd_bullet_location(bullets) == loc


def test_workday_role_without_a_location_waits_for_the_detail_call():
    job = {"title": "Software Engineer", "location": "", "url": "https://x.wd1/job/1",
           "company": "Fractal Analytics", "posted": "", "description": "",
           "_detail": ("workday", "https://x.wd1/api/job/1")}

    def enrich(jobs):
        for j in jobs:
            j["location"] = "Bengaluru, India"
            j["description"] = "Python APIs. 2-4 years of experience." * 10
        return len(jobs), 0
    with patch.object(J, "enrich_all", side_effect=enrich):
        kept, _, _ = J.select([job], set(), 2)
    assert [j["location"] for j in kept] == ["Bengaluru, India"]


def test_freshteam_reads_the_widget_feed_with_branch_and_date():
    body = {"branches": [{"id": 7, "city": "Bengaluru", "state": None, "country_code": "IN"}],
            "jobs": [
                {"title": " Software Engineer - Backend ", "branch_id": 7, "remote": False,
                 "created_at": "2026-10-05T09:00:00.000Z", "deleted": False,
                 "url": "https://haptik.freshteam.com/jobs/abc/software-engineer-backend",
                 "description": "<p>Python, Django, 2-4 years</p>"},
                {"title": "Old", "branch_id": 7, "deleted": True, "created_at": "2024-01-01"}]}
    with patch.object(J.requests, "get", return_value=resp(body)) as get:
        (j,) = J.fetch_freshteam("https://haptik.freshteam.com/jobs")
    assert get.call_args.args[0] == "https://haptik.freshteam.com/hire/widgets/jobs.json"
    assert j["title"] == "Software Engineer - Backend" and j["location"] == "Bengaluru, India"
    assert j["posted"] == "2026-10-05" and "Django" in j["description"]
    assert j["url"].endswith("/software-engineer-backend")


def test_freshteam_names_a_closed_account():
    gone = resp()
    gone.json.side_effect = ValueError("Expecting value")
    with patch.object(J.requests, "get", return_value=gone), \
            pytest.raises(ValueError, match="no Freshteam account 'vida'"):
        J.fetch_freshteam("vida")


def test_get_json_waits_out_a_429():
    limited = MagicMock(status_code=429, headers={"Retry-After": "7"})
    err = J.requests.HTTPError("429 Client Error: Too Many Requests", response=limited)
    first = resp()
    first.raise_for_status.side_effect = err
    ok = resp({"jobs": []})
    with patch.object(J.requests, "get", side_effect=[first, ok]), \
            patch.object(J.time, "sleep") as sleep:
        assert J.get_json("https://apply.workable.com/api/v1/widget/accounts/x") == {"jobs": []}
    sleep.assert_called_once_with(7)


def test_pcsx_pages_newest_first_and_points_detail_at_position_details():
    page = lambda n, count: resp({"data": {"count": count, "positions": [
        {"id": 100 + i, "name": "Software Engineer II", "locations": ["India, Karnataka, Bangalore"],
         "postedTs": 1791222098, "positionUrl": f"/careers/job/{100 + i}"} for i in range(n)]}})
    with patch.object(J.requests, "get", side_effect=[page(10, 11), page(1, 11)]) as get:
        jobs = J.fetch_pcsx("apply.careers.microsoft.com", "microsoft.com")
    starts = [c.kwargs["params"]["start"] for c in get.call_args_list]
    assert starts == [0, 10] and get.call_args.kwargs["params"]["sort_by"] == "timestamp"
    assert len(jobs) == 11 and jobs[0]["posted"] == "2026-10-05"
    assert jobs[0]["url"] == "https://apply.careers.microsoft.com/careers/job/100"
    kind, url = jobs[0]["_detail"]
    assert kind == "pcsx" and "position_details?position_id=100&domain=microsoft.com" in url
