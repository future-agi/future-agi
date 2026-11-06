"""Small deployment smoke, not a replacement for the catalogued e2e/ flows.

Only synthetic fixture credentials arrive on stdin. No saved auth state, HAR,
request bodies, response bodies or browser trace is written to evidence.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
from urllib.parse import urlencode, urlsplit

from playwright.sync_api import expect, sync_playwright


def main():
    config = json.load(sys.stdin)
    output = Path(config["output"])
    origin = config["ui"]
    api = config["api"]
    profile = "/dashboard/settings/profile-settings"
    stage = "launch"
    result = {"status": "failed", "page_errors": 0, "local_server_errors": [],
              "external_requests_qualified": False}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.set_default_timeout(30000)
        page.on("pageerror", lambda _: result.update(page_errors=result["page_errors"] + 1))

        def response_seen(response):
            url = urlsplit(response.url)
            if f"{url.scheme}://{url.netloc}" in (origin, api) and response.status >= 500:
                # No query strings, headers or bodies in retained diagnostics.
                result["local_server_errors"].append({"path": url.path, "status": response.status})

        page.on("response", response_seen)
        try:
            stage = "login form"
            page.goto(origin + "/auth/jwt/login?" + urlencode({"returnTo": profile}), wait_until="domcontentloaded")
            page.get_by_label("Email address", exact=True).fill(config["email"])
            page.get_by_label("Password", exact=True).fill(config["password"])
            stage = "real login and profile request"
            with page.expect_response(lambda r: r.url == api + "/accounts/get-user-profile-details/" and r.request.method == "GET") as profile_response:
                with page.expect_response(lambda r: r.url == api + "/accounts/token/" and r.request.method == "POST") as login_response:
                    page.get_by_role("button", name="Continue", exact=True).click()
                if login_response.value.status != 200:
                    raise AssertionError("login response was not successful")
            if profile_response.value.status != 200:
                raise AssertionError("profile response was not successful")
            stage = "rendered retained identity"
            expect(page).to_have_url(origin + profile)
            expect(page.get_by_text("Profile Details", exact=True)).to_be_visible()
            expect(page.get_by_text(config["email"], exact=True).last).to_be_visible()
            expect(page.get_by_text(config["name"], exact=True).last).to_be_visible()
            page.screenshot(path=str(output.with_suffix(".png")), full_page=True)
            if result["page_errors"] or result["local_server_errors"]:
                raise AssertionError("browser or local server error during smoke")
            result.update(status="passed", login_status=200, profile_status=200, retained_identity_visible=True)
        except Exception as exc:
            # Playwright exception text can echo entered fields; retain only a
            # bounded stage/type and a screenshot with the password field masked.
            result.update(stage=stage, error_type=type(exc).__name__)
            try:
                page.screenshot(path=str(output.with_suffix(".png")), full_page=True,
                                mask=[page.locator('input[type="password"]')])
            except Exception:
                pass
        finally:
            browser.close()
            output.write_text(json.dumps(result, indent=2) + "\n")
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
