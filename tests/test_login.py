"""Sign-in page with a remembered session (works in iPhone home-screen apps, which can't show
the browser's password pop-up), while the Shortcut and scripts keep using Basic auth."""

import base64

import pytest

from videoautomation.config import Settings

PASSWORD = "correct horse"


@pytest.fixture
def client(settings, tmp_path):
    pytest.importorskip("flask")
    from videoautomation.web.app import create_app

    settings.web_password = PASSWORD
    settings.dotenv_path = tmp_path / ".env"
    app = create_app(settings)
    app.config["TESTING"] = True
    return app.test_client()


def basic(pw):
    return {"Authorization": "Basic " + base64.b64encode(f"vauto:{pw}".encode()).decode()}


def test_pages_redirect_to_sign_in_and_api_says_sign_in(client):
    resp = client.get("/")
    assert resp.status_code == 302 and resp.headers["Location"].startswith("/login?next=")
    api = client.get("/api/status")
    assert api.status_code == 401 and api.get_json()["login"] is True
    assert "WWW-Authenticate" not in api.headers  # no browser pop-up
    for public in ("/login", "/manifest.webmanifest", "/sw.js", "/static/style.css", "/static/apple-touch-icon.png"):
        assert client.get(public).status_code == 200, public


def test_sign_in_remembers_you(client, settings):
    page = client.get("/login?next=/%23posts").get_data(as_text=True)
    assert 'autocomplete="current-password"' in page and 'value="/#posts"' in page

    wrong = client.post("/login", data={"password": "nope", "next": "/"})
    assert wrong.status_code == 200 and "isn't right" in wrong.get_data(as_text=True)
    assert "vauto_auth" not in wrong.headers.get("Set-Cookie", "")

    ok = client.post("/login", data={"password": PASSWORD, "next": "/#posts"},
                     headers={"X-Forwarded-Proto": "https"})
    assert ok.status_code == 303 and ok.headers["Location"] == "/#posts"
    cookie = ok.headers["Set-Cookie"]
    assert "vauto_auth=" in cookie and "HttpOnly" in cookie and "Secure" in cookie and "Max-Age=34560000" in cookie
    assert client.get("/").status_code == 200
    assert client.get("/api/status").status_code == 200

    # Changing the password signs every device out.
    settings.web_password = "new password"
    assert client.get("/api/status").status_code == 401
    assert client.get("/logout").status_code == 303


def test_basic_auth_still_works_for_the_shortcut(client):
    assert client.get("/api/status", headers=basic(PASSWORD)).status_code == 200
    assert client.get("/api/status", headers=basic("wrong")).status_code == 401


@pytest.mark.parametrize("target", ["//evil.example/x", "https://evil.example", "/\\evil.example", "posts"])
def test_sign_in_never_redirects_off_site(client, target):
    resp = client.post("/login", data={"password": PASSWORD, "next": target})
    assert resp.headers["Location"] == "/"


def test_sign_in_rejects_other_sites(client, settings):
    resp = client.post("/login", data={"password": PASSWORD}, headers={"Origin": "https://evil.example"})
    assert resp.status_code == 403


def test_without_a_password_the_login_page_just_goes_home(settings, tmp_path):
    pytest.importorskip("flask")
    from videoautomation.web.app import create_app

    settings.web_password = None
    client = create_app(settings).test_client()
    assert client.get("/login?next=/%23stats").headers["Location"] == "/#stats"
    assert client.get("/").status_code == 200
