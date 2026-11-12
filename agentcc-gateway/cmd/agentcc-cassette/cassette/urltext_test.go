package cassette

import (
	"errors"
	"regexp"
	"strings"
	"testing"
)

// TestScrubURLInFormShapedText: a text body that is a URL, or a pair list
// holding one, keeps that URL's query whole although splitting the text on
// & reads it as form pairs: the URL's credential parameters go even where
// the split glued the URL to its first parameter's name (a bare callback,
// a signed link) or cut an unencoded URL value's later parameters off into
// the form, at any length, whatever the Content-Type says. The review's
// cases A-D, a pair list with credentials of its own and an unencoded URL
// value, and a real form with a URL in name position.
func TestScrubURLInFormShapedText(t *testing.T) {
	code, state, sig, tok, auth := fakeToken("authcode"), fakeToken("oauthstate"), fakeToken("urlsig"), fakeToken("urltoken"), fakeToken("urlauth")
	oauthTok, oauthSec := fakeToken("oauthtoken"), fakeToken("oauthsecret")
	const short = "c0De7" // shorter than minGlobalToken: only a rule can find it
	for name, tc := range map[string]struct {
		ct, body string
		gone     []string
	}{
		"A a bare callback URL, text/plain": {"text/plain",
			"https://app.example.test/cb?code=" + code + "&state=" + state, []string{code, state}},
		"A a short code in a bare callback URL": {"text/plain",
			"https://app.example.test/cb?code=" + short + "&state=" + state, []string{"=" + short, state}},
		"B a pair whose value is an unencoded URL, text/plain": {"text/plain",
			"url=https://app.example.test/cb?code=" + code + "&state=" + state, []string{code, state}},
		"C a signed link, text/plain": {"text/plain",
			"https://files.example.test/d/abc?sig=" + sig + "&token=" + tok, []string{sig, tok}},
		"D a URL with no Content-Type": {"",
			"https://api.example.test/v1/items?auth=" + auth + "&access_token=" + tok, []string{auth, tok}},
		"own credentials and an unencoded URL value, text/html": {"text/html; charset=utf-8",
			"oauth_token=" + oauthTok + "&oauth_token_secret=" + oauthSec + "&next=https://app.example.test/cb?code=" + code + "&state=" + state,
			[]string{oauthTok, oauthSec, code, state}},
		"a URL in name position sent as a form": {"application/x-www-form-urlencoded",
			"https://app.example.test/cb?code=" + short + "&state=" + state, []string{"=" + short, state}},
		"an upper-case scheme (schemes are case-insensitive)": {"text/plain",
			"HTTPS://app.example.test/cb?code=" + code + "&state=" + state, []string{code, state}},
	} {
		t.Run(name, func(t *testing.T) {
			h := hdr()
			if tc.ct != "" {
				h = hdr("Content-Type", tc.ct)
			}
			c := newHTTPCassette(HTTPExchange{Request: HTTPRequest{Method: "GET", Path: "/v1/link"},
				Response: HTTPResponse{Status: 200, Headers: h, Body: &Body{Text: tc.body}}})
			text := saveOK(t, c, ScrubConfig{})
			assertAbsent(t, text, tc.gone...)
			if b := loadSaved(t, text).HTTP.Exchanges[0].Response.Body.Text; !strings.Contains(b, "scrubbed-token-") {
				t.Errorf("body = %q, want the credentials as placeholders", b)
			}
		})
	}
	t.Run("a bare URL keeps its shape, and data in a URL stays", func(t *testing.T) {
		c := newHTTPCassette(
			HTTPExchange{Request: HTTPRequest{Method: "GET", Path: "/v1/link"},
				Response: HTTPResponse{Status: 200, Headers: hdr("Content-Type", "text/plain"),
					Body: &Body{Text: "https://app.example.test/cb?code=" + code + "&state=" + state + "\n"}}},
			HTTPExchange{Request: HTTPRequest{Method: "GET", Path: "/v1/countries"},
				Response: HTTPResponse{Status: 200, Headers: hdr("Content-Type", "text/plain"),
					Body: &Body{Text: "https://api.example.test/v1/countries?code=US&page=2"}}},
		)
		text := saveOK(t, c, ScrubConfig{})
		xs := loadSaved(t, text).HTTP.Exchanges
		if b := xs[0].Response.Body.Text; !regexp.MustCompile(`^https://app\.example\.test/cb\?code=scrubbed-token-[0-9]+&state=scrubbed-token-[0-9]+\n$`).MatchString(b) {
			t.Errorf("callback body = %q", b)
		}
		if b := xs[1].Response.Body.Text; b != "https://api.example.test/v1/countries?code=US&page=2" {
			t.Errorf("a data URL changed: %q", b)
		}
	})
}

// TestScrubURLInQuery: a request query that holds a URL keeps that URL's
// query whole too: an unencoded URL value (whose state the split moved
// into the outer query) and a URL in name position (a redirector called
// as ?https://...) lose their credentials.
func TestScrubURLInQuery(t *testing.T) {
	code, state, tok := fakeToken("qcode"), fakeToken("qstate"), fakeToken("qtoken")
	for name, query := range map[string]string{
		"an unencoded URL value": "callback=https://app.example.test/cb?code=" + code + "&state=" + state + "&per_page=20",
		"a URL in name position": "https://app.example.test/cb?code=" + code + "&state=" + state,
		"a signed URL value":     "u=https://files.example.test/d/abc?X-Amz-Expires=60&token=" + tok,
	} {
		t.Run(name, func(t *testing.T) {
			c := newHTTPCassette(HTTPExchange{Request: HTTPRequest{Method: "GET", Path: "/v1/redirect", Query: query},
				Response: HTTPResponse{Status: 204}})
			text := saveOK(t, c, ScrubConfig{})
			assertAbsent(t, text, code, state, tok)
		})
	}
}

// TestScrubNestedURLKeepsOAuthContext: an unencoded URL inside another
// URL's query is cut where its own & meets the outer query, so its code
// sits apart from the state that makes it one. The outer query's OAuth
// companion counts for the URLs inside its values.
func TestScrubNestedURLKeepsOAuthContext(t *testing.T) {
	code, state := fakeToken("nestedcode"), fakeToken("nestedstate")
	c := newMCPCassette(MCPExchange{Method: "tools/call", Params: raw(`{"name":"get_login_link","arguments":{}}`),
		Result: raw(`{"structuredContent":{"link":"https://accounts.example.test/login?continue=https://app.example.test/cb?code=` +
			code + `&state=` + state + `","country":"https://api.example.test/v1/countries?code=US"}}`)})
	text := saveOK(t, c, ScrubConfig{})
	assertAbsent(t, text, code, state)
	assertPresent(t, text, "countries?code=US")
}

// TestCheckRefusesCredentialInURL: check, run on a cassette edited by hand
// (or written by a scrubber that missed a route), refuses a URL whose
// credential parameter holds anything but a placeholder, wherever the URL
// sits (a text body, a JSON string, a header, a fragment, a parameter the
// recording named), and names the rule and the place, never the value. A
// URL whose credentials are placeholders or empty, or whose code is data,
// passes.
func TestCheckRefusesCredentialInURL(t *testing.T) {
	tok, code := fakeToken("handedited"), fakeToken("handcode")
	link := func(u string) *Cassette {
		return newHTTPCassette(HTTPExchange{Request: HTTPRequest{Method: "GET", Path: "/v1/link"},
			Response: HTTPResponse{Status: 200, Headers: hdr("Content-Type", "text/plain"), Body: &Body{Text: u}}})
	}
	named := link("https://api.example.test/v1/run?vendor_cred=" + tok)
	named.Rules = &Rules{SecretParams: []string{"vendor_cred"}}
	for name, tc := range map[string]struct {
		c       *Cassette
		refused bool
	}{
		"a callback URL in a text body": {link("https://app.example.test/cb?code=" + code + "&state=scrubbed-token-1"), true},
		"a token in a JSON string": {newHTTPCassette(HTTPExchange{Request: HTTPRequest{Method: "GET", Path: "/v1/feed"},
			Response: HTTPResponse{Status: 200, Body: jsonBody(map[string]string{"next": "https://graph.example.test/v1/feed?access_token=" + tok + "&after=X"})}}), true},
		"a token in a Link header": {newHTTPCassette(HTTPExchange{Request: HTTPRequest{Method: "GET", Path: "/v1/items"},
			Response: HTTPResponse{Status: 200, Headers: hdr("Link", "<https://api.example.test/v1/items?page=2&access_token="+tok+">; rel=\"next\"")}}), true},
		"a token in a fragment":                   {link("https://client.example.test/cb#access_token=" + tok + "&token_type=bearer"), true},
		"a parameter the recording named":         {named, true},
		"a code in a URL nested in a URL's query": {link("https://accounts.example.test/login?continue=https://app.example.test/cb?code=" + code + "&state=scrubbed-token-1"), true},
		"an upper-case scheme":                    {link("Https://graph.example.test/v1/feed?access_token=" + tok), true},
		"placeholders and empty values":           {link("https://app.example.test/cb?code=scrubbed-token-1&state=scrubbed-token-2&token=&key=scrubbed-unmatched"), false},
		"a code that is data":                     {link("https://api.example.test/v1/countries?code=US"), false},
	} {
		t.Run(name, func(t *testing.T) {
			tc.c.Format, tc.c.Notice = Format, Notice
			data, err := marshal(tc.c)
			if err != nil {
				t.Fatal(err)
			}
			err = Check(data)
			if !tc.refused {
				if err != nil {
					t.Errorf("Check = %v, want nil", err)
				}
				return
			}
			var refused *RefusedError
			if !errors.As(err, &refused) {
				t.Fatalf("Check = %v, want a refusal", err)
			}
			msg := refused.Error()
			if !strings.Contains(msg, "url-credential at $.http.exchanges[0].") {
				t.Errorf("refusal %q does not name url-credential and the place", msg)
			}
			if strings.Contains(msg, tok) || strings.Contains(msg, code) {
				t.Errorf("the refusal carries the value: %q", msg)
			}
		})
	}
}
