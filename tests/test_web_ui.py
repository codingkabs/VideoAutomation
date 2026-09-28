"""The web app in a real browser (Chromium), at phone size: tabs, My posts, Stats, Platforms, saved captions.

Skipped when Playwright or Chromium is missing. Posting itself is covered elsewhere;
here the database is seeded directly so the screens have something to show.
"""

import threading
from datetime import timedelta
from pathlib import Path

import pytest

from videoautomation.models import MediaFile, PostJob, PostResult
from videoautomation.scheduler import iso, utcnow
from videoautomation.tracker import Tracker


def _chromium():
    try:
        import playwright  # noqa: F401
    except ImportError:
        return None
    path = Path("/opt/pw-browsers/chromium")
    return str(path) if path.exists() else "default"


pytestmark = pytest.mark.skipif(_chromium() is None, reason="playwright not installed")


def _seed(settings):
    tr = Tracker.open(settings)
    now = utcnow()
    for i, (caption, views) in enumerate([("Hyde Park run #running #uk", 30000), ("Pasta test #food #uk", 54000),
                                          ("Guitar day 30 #guitar", 29000)]):
        pid = f"uipost{i}"
        for platform, surface, backend in (("instagram", "reel", "meta"), ("tiktok", "video", "zernio")):
            key = f"{pid}{platform}"
            job = PostJob(platform, surface, backend, [MediaFile("/x.mp4", "video")], caption, idem_key=key,
                          post_id=pid, run_at=iso(now - timedelta(days=i, hours=2)))
            tr.store.insert(job, "running")
            tr.store.finish(key, PostResult(platform, surface, "published", url=f"https://example.com/{key}"))
            tr.add_stats(key, {"views": views / 2, "likes": views / 20})
    hand = PostJob("snapchat", "spotlight", "handoff", [], "Hyde Park run #running #uk", idem_key="uipost0snap",
                   post_id="uipost0", run_at=iso(now - timedelta(hours=2)))
    tr.store.insert(hand, "running")
    tr.store.finish("uipost0snap", PostResult("snapchat", "spotlight", "handoff"))
    later = PostJob("instagram", "reel", "meta", [], "Scotland vlog", idem_key="uisoon", post_id="uisoon",
                    run_at=iso(now + timedelta(hours=20)))
    tr.store.insert(later, "pending")
    tr.add_snippet("Sign-off", "Follow for daily UK vlogs")


@pytest.fixture
def app_url(settings, tmp_path):
    pytest.importorskip("flask")
    from werkzeug.serving import make_server

    from videoautomation.web.app import create_app

    settings.dotenv_path = tmp_path / ".env"
    _seed(settings)
    server = make_server("127.0.0.1", 0, create_app(settings), threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/"
    server.shutdown()


@pytest.fixture
def page(app_url):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        path = _chromium()
        browser = p.chromium.launch(**({} if path == "default" else {"executable_path": path}))
        pg = browser.new_page(viewport={"width": 390, "height": 844})
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(app_url)
        pg.wait_for_selector("#ready-pill:not(:text('…'))")
        pg.errors = errors
        yield pg
        browser.close()


def test_phone_layout_and_tab_navigation(page):
    nav = page.locator(".tabs")
    box = nav.bounding_box()
    assert box["y"] + box["height"] >= 844 - 1, "tabs should sit at the bottom of a phone screen"
    page.click("[data-tab='posts']")
    page.wait_for_selector(".pitem")
    assert page.url.endswith("#posts")
    page.click("[data-tab='stats']")
    page.wait_for_selector(".kpi")
    page.go_back()  # the phone's back gesture returns to the previous tab
    page.wait_for_selector("#tab-posts.active")
    assert page.evaluate("getComputedStyle(document.querySelector('#caption')).fontSize") == "16px"  # no iOS zoom
    assert not page.errors


def test_my_posts_list_filters_and_marking_a_handoff(page):
    page.click("[data-tab='posts']")
    page.wait_for_selector(".pitem")
    assert page.locator("#upcoming-card").is_visible() and "Scotland vlog" in page.inner_text("#upcoming-list")
    assert page.locator(".pitem").count() == 4
    page.click("#tab-posts .seg [data-show='attention']")
    page.wait_for_function("document.querySelectorAll('.pitem').length === 1")
    item = page.locator(".pitem").first
    assert "Hyde Park run" in item.inner_text() and "needs you" in item.inner_text().lower()
    item.locator(".phead").click()
    row = item.locator(".jrow", has_text="Snapchat")
    row.get_by_role("button", name="Mark as posted").click()
    row.locator("input[name=url]").fill("https://www.snapchat.com/spotlight/abc")
    row.get_by_role("button", name="Save").click()
    page.wait_for_selector("#posts-list .empty")  # nothing left that needs you
    page.click("#tab-posts .seg [data-show='all']")
    page.fill("#posts-q", "pasta")
    page.wait_for_function("document.querySelectorAll('.pitem').length === 1")
    assert "Pasta test" in page.inner_text("#posts-list")
    assert not page.errors


def test_stats_screen_shows_numbers_and_charts(page):
    page.click("[data-tab='stats']")
    page.wait_for_selector(".kpi")
    kpis = page.inner_text("#kpis")
    assert "113K" in kpis.replace(",", "") or "113" in kpis  # total views 30k+54k+29k
    assert page.locator(".hbar-row").count() == 2  # Instagram and TikTok
    assert page.locator("#top-posts .trow").count() == 3
    assert page.locator(".vcol-fill").count() == 24 and page.locator(".cal-cell.l1, .cal-cell.l2").count() >= 3
    bar = page.locator(".hbar-track").first
    bar.focus()
    assert page.locator("#viz-tip").is_visible() and "views" in page.inner_text("#viz-tip")
    assert bar.get_attribute("role") == "img" and bar.get_attribute("aria-label")
    page.click("#tab-stats .seg [data-days='7']")
    page.wait_for_function("document.querySelector('#stats-body').classList.contains('loading') === false")
    assert not page.errors


def test_platforms_guide_search_and_open(page):
    page.click("[data-tab='platforms']")
    page.wait_for_selector("details.plat")
    page.fill("#plat-q", "india")
    page.wait_for_function("document.querySelectorAll('details.plat').length < 12")
    names = page.locator("details.plat summary b").all_inner_texts()
    assert "Moj" in names and "Instagram" not in names
    page.fill("#plat-q", "tiktok")
    page.locator("details.plat summary").first.click()
    body = page.inner_text("details.plat[open]").lower()  # headings are upper-cased by CSS
    assert "how posts get seen" in body and "when to post" in body and "in vauto" in body
    assert not page.errors


def test_saved_caption_inserts_and_saves(page):
    page.wait_for_selector(".snip-use")
    page.fill("#caption", "New video")
    page.click(".snip-use")
    assert page.input_value("#caption") == "New video\n\nFollow for daily UK vlogs"
    page.click("#snippet-toggle")
    page.fill("#snippet-name", "Whole caption")
    page.click("#snippet-save")
    page.wait_for_function("document.querySelectorAll('.snip-use').length === 2")
    assert not page.errors


def test_every_control_has_a_name(page):
    """Buttons, links and form fields need a name a screen reader can say."""
    for tab in ("post", "posts", "stats", "setup", "platforms"):
        page.click(f"[data-tab='{tab}']")
        page.wait_for_timeout(600)
        unnamed = page.evaluate("""() => [...document.querySelectorAll('.tab.active button, .tab.active a, .tab.active input, .tab.active select, .tab.active textarea')]
          .filter((n) => n.offsetParent !== null && n.type !== 'hidden')
          .filter((n) => !(n.textContent || '').trim() && !n.getAttribute('aria-label') && !n.getAttribute('placeholder')
                         && !n.closest('label') && !(n.id && document.querySelector(`label[for="${n.id}"]`)))
          .map((n) => n.outerHTML.slice(0, 80))""")
        assert not unnamed, f"{tab}: {unnamed}"
