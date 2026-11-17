package cassette

import (
	"regexp"
	"strings"
	"testing"
)

// TestScrubURLUserinfoAndSchemes: a URL of any scheme is a URL. Its user
// info (scheme://user:password@host) is a credential, scrubbed whatever the
// host (localhost and an IP have no letter TLD, so no email rule finds the
// password glued to them), and its query is scrubbed by the usual names (a
// websocket URL's ?token=). An @ in a path is not user info, and a URL with
// none keeps its shape.
func TestScrubURLUserinfoAndSchemes(t *testing.T) {
	pw, tok, oauthTok := fakeToken("userinfopw"), fakeToken("wsstoken"), fakeToken("formtoken")
	for name, tc := range map[string]struct {
		body *Body
		ct   string
		want *regexp.Regexp // the scrubbed body, compact
	}{
		"user info at localhost, in text": {&Body{Text: "callback https://deploy:" + pw + "@localhost:8080/status ok"}, "text/plain",
			regexp.MustCompile(`^callback https://scrubbed-token-[0-9]+@localhost:8080/status ok$`)},
		"user info at an IP, in JSON": {&Body{JSON: raw(`{"hook":"https://admin:` + pw + `@10.0.0.5/hook"}`)}, "application/json",
			regexp.MustCompile(`^\{"hook":"https://scrubbed-token-[0-9]+@10\.0\.0\.5/hook"\}$`)},
		"user info at a TLD host, in JSON": {&Body{JSON: raw(`{"hook":"https://admin:` + pw + `@hooks.example.test/x"}`)}, "application/json",
			regexp.MustCompile(`^\{"hook":"https://scrubbed-token-[0-9]+@hooks\.example\.test/x"\}$`)},
		"a database URI": {&Body{JSON: raw(`{"dsn":"postgres://app:` + pw + `@localhost:5432/app?sslmode=disable"}`)}, "application/json",
			regexp.MustCompile(`^\{"dsn":"postgres://scrubbed-token-[0-9]+@localhost:5432/app\?sslmode=disable"\}$`)},
		"a websocket URL's token": {&Body{JSON: raw(`{"url":"wss://rt.example.test/v2/ws?token=` + tok + `&sample_rate=16000"}`)}, "application/json",
			regexp.MustCompile(`^\{"url":"wss://rt\.example\.test/v2/ws\?token=scrubbed-token-[0-9]+&sample_rate=16000"\}$`)},
		"user info in a form value": {&Body{Text: "oauth_token=" + oauthTok + "&next=https://u:" + pw + "@10.0.0.5/cb"}, "text/plain",
			// a parameter the scrubber changed is written query-escaped (query())
			regexp.MustCompile(`^oauth_token=scrubbed-token-[0-9]+&next=https%3A%2F%2Fscrubbed-token-[0-9]+%4010\.0\.0\.5%2Fcb$`)},
		"an @ in a path, and no user info": {&Body{JSON: raw(`{"profile":"https://social.example.test/@ci_bot/posts","cache":"redis://cache.internal:6379/0"}`)}, "application/json",
			regexp.MustCompile(`^\{"profile":"https://social\.example\.test/@ci_bot/posts","cache":"redis://cache\.internal:6379/0"\}$`)},
	} {
		t.Run(name, func(t *testing.T) {
			c := newHTTPCassette(HTTPExchange{Request: HTTPRequest{Method: "GET", Path: "/v1/x"},
				Response: HTTPResponse{Status: 200, Headers: hdr("Content-Type", tc.ct), Body: tc.body}})
			text := saveOK(t, c, ScrubConfig{})
			assertAbsent(t, text, pw, tok, oauthTok)
			b := loadSaved(t, text).HTTP.Exchanges[0].Response.Body
			got := b.Text
			if len(b.JSON) > 0 {
				got = compactJSON(t, b.JSON)
			}
			if !tc.want.MatchString(got) {
				t.Errorf("body = %s", got)
			}
		})
	}
}

// TestCheckRefusesURLUserinfo: check refuses user info that is not a
// placeholder and a websocket URL's credential, and passes a placeholder
// user info at any host (scrubbed-token-1@host.tld is no email).
func TestCheckRefusesURLUserinfo(t *testing.T) {
	pw := fakeToken("handuserinfo")
	for name, tc := range map[string]struct {
		text    string
		refused bool
	}{
		"a password in user info":         {"https://admin:" + pw + "@10.0.0.5/hook", true},
		"a token as the user":             {"https://" + pw + "@git.internal/org/repo", true},
		"a websocket token":               {"wss://rt.example.test/ws?access_token=" + pw, true},
		"a database password":             {"postgres://app:" + pw + "@localhost:5432/app", true},
		"placeholder user info, an IP":    {"https://scrubbed-token-1@10.0.0.5/hook", false},
		"placeholder user info, a TLD":    {"postgres://scrubbed-token-2@db.example.test:5432/app", false},
		"an @ in a path":                  {"https://social.example.test/@ci_bot/posts", false},
		"an ordinary email next to a URL": {"https://example.test/ and alice@corp-test.dev", true},
	} {
		t.Run(name, func(t *testing.T) {
			c := newHTTPCassette(HTTPExchange{Request: HTTPRequest{Method: "GET", Path: "/v1/link"},
				Response: HTTPResponse{Status: 200, Headers: hdr("Content-Type", "text/plain"), Body: &Body{Text: tc.text}}})
			c.Format, c.Notice = Format, Notice
			data, err := marshal(c)
			if err != nil {
				t.Fatal(err)
			}
			err = Check(data)
			if refused := err != nil; refused != tc.refused {
				t.Fatalf("Check = %v, want refused %v", err, tc.refused)
			}
			if err != nil && strings.Contains(err.Error(), pw) {
				t.Errorf("the refusal carries the value: %v", err)
			}
		})
	}
}
