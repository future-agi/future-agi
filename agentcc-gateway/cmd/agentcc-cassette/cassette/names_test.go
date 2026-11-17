package cassette

import (
	"encoding/base64"
	"net/url"
	"regexp"
	"strings"
	"testing"
)

// TestScrubWidenedCredentialNames: a parameter or member named like a
// credential (a token, key or secret suffix: GitLab's private_token, Azure
// APIM's subscription-key, Slack's app_token) loses its value, while
// pagination cursors and data keys named like them (pageToken, $skiptoken,
// spaceKey; issueKey and fileKey in JSON) keep theirs, so replay still
// tells page 1 from page 2 and one issue from another.
func TestScrubWidenedCredentialNames(t *testing.T) {
	secretParams := []string{"private_token", "subscription-key", "app_token", "access_key", "secret_key", "app_key",
		"appid", "user_key", "application_key", "wskey"}
	secretMembers := []string{"app_token", "bot_token", "verification_token", "secret_key", "signing_key", "access_key",
		"application_key", "webhook_secret"}
	values := map[string]string{}
	var query []string
	for _, n := range secretParams {
		v := fakeToken("q-" + normName(n))
		values["parameter "+n] = v
		query = append(query, url.QueryEscape(n)+"="+v)
	}
	keptQuery := []string{"pageToken=PAGECURSOR01", "nextPageToken=NEXTCURSOR02", "syncToken=SYNCCURSOR03",
		"continuationToken=CONTCURSOR04", "next_token=NEXTTOKEN05", "pagination_token=PAGINATION06", "%24skiptoken=SKIPTOKEN07",
		"%24deltatoken=DELTATOKEN08", "pageKey=PAGEKEY09", "spaceKey=ENG", "projectKey=PROJ", "per_page=20"}
	body := map[string]any{"issueKey": "PROJ-123", "projectKey": "PROJ", "fileKey": "FigmaFileKey42",
		"nextPageToken": "NEXTCURSOR02", "token_type": "bearer", "max_tokens": 1024}
	for _, n := range secretMembers {
		v := fakeToken("j-" + normName(n))
		values["member "+n] = v
		body[n] = v
	}
	c := newHTTPCassette(HTTPExchange{
		Request:  HTTPRequest{Method: "GET", Path: "/api/v4/projects", Query: strings.Join(append(query, keptQuery...), "&")},
		Response: HTTPResponse{Status: 200, Headers: hdr("Content-Type", "application/json"), Body: jsonBody(body)},
	})
	text := saveOK(t, c, ScrubConfig{})
	for where, v := range values {
		if strings.Contains(text, v) {
			t.Errorf("%s kept its value", where)
		}
	}
	assertPresent(t, text, keptQuery...)
	assertPresent(t, text, `"issueKey": "PROJ-123"`, `"projectKey": "PROJ"`, `"fileKey": "FigmaFileKey42"`,
		`"nextPageToken": "NEXTCURSOR02"`, `"token_type": "bearer"`, `"max_tokens": 1024`)
}

// TestScrubFormShapedTextBody: a form body is scrubbed parameter by
// parameter whatever its Content-Type says (X's OAuth 1.0a token answer
// comes as text/html; a client may send a form with no Content-Type). Text
// that only looks like a pair (prose, Base64 with = padding) stays text.
func TestScrubFormShapedTextBody(t *testing.T) {
	tok, sec, hook := fakeToken("oauthtoken"), fakeToken("oauthsecret"), fakeToken("hooktoken")
	padded := base64.StdEncoding.EncodeToString([]byte("write to " + aliceEmail + " today"))
	if !strings.HasSuffix(padded, "=") {
		t.Fatalf("want a padded Base64 body, got %q", padded)
	}
	c := newHTTPCassette(
		HTTPExchange{
			Request: HTTPRequest{Method: "POST", Path: "/oauth/access_token"},
			Response: HTTPResponse{Status: 200, Headers: hdr("Content-Type", "text/html; charset=utf-8"),
				Body: &Body{Text: "oauth_token=" + tok + "&oauth_token_secret=" + sec + "&user_id=" + userNumber + "&screen_name=ci\n"}}},
		HTTPExchange{
			Request:  HTTPRequest{Method: "POST", Path: "/api/notify", Body: &Body{Text: "token=" + hook + "&channel=general"}},
			Response: HTTPResponse{Status: 200, Headers: hdr("Content-Type", "text/plain"), Body: &Body{Text: padded}}},
		HTTPExchange{
			Request:  HTTPRequest{Method: "GET", Path: "/notes"},
			Response: HTTPResponse{Status: 200, Headers: hdr("Content-Type", "text/plain"), Body: &Body{Text: "set token=on and retry"}}},
	)
	text := saveOK(t, c, ScrubConfig{})
	assertAbsent(t, text, tok, sec, hook, userNumber, aliceEmail)
	xs := loadSaved(t, text).HTTP.Exchanges
	if b := xs[0].Response.Body.Text; !regexp.MustCompile(`^oauth_token=scrubbed-token-[0-9]+&oauth_token_secret=scrubbed-token-[0-9]+&user_id=9[0-9]{7}&screen_name=ci\n$`).MatchString(b) {
		t.Errorf("text/html token answer = %q", b)
	}
	if b := xs[1].Request.Body.Text; !regexp.MustCompile(`^token=scrubbed-token-[0-9]+&channel=general$`).MatchString(b) {
		t.Errorf("form without a Content-Type = %q", b)
	}
	dec, err := base64.StdEncoding.DecodeString(xs[1].Response.Body.Text)
	if err != nil || !regexp.MustCompile(`^write to user-[0-9]+@example\.com today$`).Match(dec) {
		t.Errorf("padded Base64 text decodes to %q (%v)", dec, err)
	}
	if b := xs[2].Response.Body.Text; b != "set token=on and retry" {
		t.Errorf("prose changed: %q", b)
	}
}

// TestScrubJSONOAuthCode: an authorization code in a JSON token request
// (Notion's and Atlassian's /oauth/token take JSON) is a credential beside
// grant_type, redirect_uri or code_verifier, whichever comes first; a code
// with no such companion is data.
func TestScrubJSONOAuthCode(t *testing.T) {
	notionCode, atlassianCode, verifier := fakeToken("notioncode"), fakeToken("atlassiancode"), fakeToken("pkceverifier")
	jsonCT := hdr("Content-Type", "application/json")
	ok := HTTPResponse{Status: 200, Headers: jsonCT, Body: jsonBody(map[string]string{"token_type": "bearer"})}
	c := newHTTPCassette(
		HTTPExchange{Request: HTTPRequest{Method: "POST", Path: "/v1/oauth/token", Headers: jsonCT,
			Body: &Body{JSON: raw(`{"grant_type":"authorization_code","code":"` + notionCode + `","redirect_uri":"https://app.example.test/cb"}`)}},
			Response: ok},
		HTTPExchange{Request: HTTPRequest{Method: "POST", Path: "/oauth/token", Headers: jsonCT,
			Body: &Body{JSON: raw(`{"code":"` + atlassianCode + `","code_verifier":"` + verifier + `","grant_type":"authorization_code"}`)}},
			Response: ok},
		HTTPExchange{Request: HTTPRequest{Method: "GET", Path: "/v1/countries"},
			Response: HTTPResponse{Status: 200, Headers: jsonCT, Body: &Body{JSON: raw(`{"countries":[{"code":"US","state":"active"}]}`)}}},
	)
	text := saveOK(t, c, ScrubConfig{})
	assertAbsent(t, text, notionCode, atlassianCode, verifier)
	assertPresent(t, text, `"code": "US"`, `"state": "active"`, `"grant_type": "authorization_code"`)
}

// TestScrubNumericSecret: a number under a credential name is a credential:
// it becomes a placeholder string, at any length. Elsewhere it is replaced
// where it stands alone, never inside a longer number.
func TestScrubNumericSecret(t *testing.T) {
	c := newHTTPCassette(HTTPExchange{Request: HTTPRequest{Method: "GET", Path: "/v2/meetings/standup"},
		Response: HTTPResponse{Status: 200, Headers: hdr("Content-Type", "application/json"),
			Body: &Body{JSON: raw(`{"topic":"standup","password":87654321,"settings":{"pstn_password":445566},"duration":30,` +
				`"note":"dial 87654321; ref 1876543210","start":18765432109}`)}}})
	text := saveOK(t, c, ScrubConfig{})
	assertAbsent(t, text, `"87654321`, " 87654321", "445566")
	b := compactJSON(t, loadSaved(t, text).HTTP.Exchanges[0].Response.Body.JSON)
	m := regexp.MustCompile(`^\{"topic":"standup","password":"(scrubbed-token-[0-9]+)","settings":\{"pstn_password":"scrubbed-token-[0-9]+"\},"duration":30,` +
		`"note":"dial (scrubbed-token-[0-9]+); ref 1876543210","start":18765432109\}$`).FindStringSubmatch(b)
	if m == nil || m[1] != m[2] {
		t.Errorf("body = %s", b)
	}
}

// TestScrubQueryValueWithBadEscape: a credential parameter whose value is
// not valid percent-encoding is still scrubbed (taken as written), never
// skipped.
func TestScrubQueryValueWithBadEscape(t *testing.T) {
	tok := fakeToken("badescape")
	c := newHTTPCassette(HTTPExchange{
		Request:  HTTPRequest{Method: "GET", Path: "/v1/items", Query: "token=%zz" + tok + "&q=ok"},
		Response: HTTPResponse{Status: 200},
	})
	text := saveOK(t, c, ScrubConfig{})
	assertAbsent(t, text, tok)
	if q := loadSaved(t, text).HTTP.Exchanges[0].Request.Query; !regexp.MustCompile(`^token=scrubbed-token-[0-9]+&q=ok$`).MatchString(q) {
		t.Errorf("query = %q", q)
	}
}
