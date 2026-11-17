package cassette

import (
	"encoding/base64"
	"encoding/json"
	"net/url"
	"regexp"
	"strings"
	"testing"
)

const (
	threadID    = "18c2f0a1b2c3d4e5"
	workspaceID = "550e8400-e29b-41d4-a716-446655440000"
	aliceEmail  = "alice@corp-test.dev"
	bobEmail    = "bob.smith+tests@corp-test.dev"
	userNumber  = "12345678"
	workspace   = "acme-test-workspace"
	dataText    = "Template {{.Secret}} and %s and ${HOME} and $(id) and <b>bold</b> & more"
)

// TestScrubberRemovesTokensEmailsIds is the acceptance test for the
// scrubber: tokens, emails and ids leave every place they were recorded in
// (headers, paths, args, results, JSON inside text, Base64 inside JSON),
// each value gets one stable placeholder across the whole cassette, ids keep
// their type, and recorded vendor text is otherwise data, kept byte for byte.
func TestScrubberRemovesTokensEmailsIds(t *testing.T) {
	known := fakeToken("recording-credential")
	attachment := base64.URLEncoding.EncodeToString([]byte("Hello, write to " + aliceEmail + " about thread " + threadID + "."))
	embedded := `{"threads":[{"id":"` + threadID + `","from":"` + aliceEmail + `","snippet":"cc ` + bobEmail + `"}],"zeta":1,"alpha":2}`
	mcp := newMCPCassette(
		MCPExchange{Method: "initialize", Params: raw(`{"protocolVersion":"2025-11-25"}`),
			Result: raw(`{"protocolVersion":"2025-11-25","serverInfo":{"name":"mail"},"instructions":"` + dataText + `"}`)},
		MCPExchange{Method: "tools/call", Params: raw(`{"name":"search_threads","arguments":{"query":"from:` + aliceEmail + `"}}`),
			Result: raw(`{"content":[{"type":"text","text":` + string(jsonString(embedded)) + `}],` +
				`"structuredContent":{"threads":[{"id":"` + threadID + `","user_id":` + userNumber + `,"workspace_id":"` + workspaceID + `","n":{"id":42}}]}}`)},
		MCPExchange{Method: "tools/call", Params: raw(`{"name":"get_thread","arguments":{"thread_id":"` + threadID + `"}}`),
			Result: raw(`{"content":[{"type":"text","text":"debug: authorized as ` + known + ` in ` + workspace + `"}],` +
				`"structuredContent":{"id":"` + threadID + `","owner":` + userNumber + `,"attachment":{"data":"` + attachment + `"}}}`)},
	)
	text := saveOK(t, mcp, ScrubConfig{KnownSecrets: []string{known}, Literals: []string{workspace}})
	assertAbsent(t, text, known, workspace, threadID, workspaceID, aliceEmail, bobEmail, userNumber, attachment)
	got := loadSaved(t, text).MCP.Exchanges

	t.Run("one stable placeholder per value across exchanges", func(t *testing.T) {
		idPH := regexp.MustCompile(`scrubbed-id-[0-9]+`).FindString(compactJSON(t, got[1].Result))
		if idPH == "" {
			t.Fatal("no id placeholder in the search result")
		}
		for i, where := range []string{string(got[1].Result), string(got[2].Params), string(got[2].Result)} {
			if strings.Count(where, idPH) == 0 {
				t.Errorf("place %d lost the thread id's placeholder %s", i, idPH)
			}
		}
		var args struct {
			Arguments struct{ Query string } `json:"arguments"`
		}
		_ = json.Unmarshal(got[1].Params, &args)
		if !strings.Contains(compactJSON(t, got[1].Result), strings.TrimPrefix(args.Arguments.Query, "from:")) {
			t.Errorf("the email in the args (%s) is not the email in the result", args.Arguments.Query)
		}
	})
	t.Run("ids keep their type and a short id stays", func(t *testing.T) {
		r := compactJSON(t, got[1].Result)
		if !regexp.MustCompile(`"user_id":9[0-9]{7}`).MatchString(r) {
			t.Errorf("numeric user_id is not an 8-digit number placeholder: %s", r)
		}
		if !strings.Contains(r, `"workspace_id":"`+placeholderUUIDPrefix) {
			t.Errorf("the uuid id is not a uuid placeholder: %s", r)
		}
		if !strings.Contains(r, `"n":{"id":42}`) {
			t.Errorf("a short id below the threshold changed: %s", r)
		}
		owner := regexp.MustCompile(`"owner":([0-9]+)`).FindStringSubmatch(compactJSON(t, got[2].Result))
		uid := regexp.MustCompile(`"user_id":([0-9]+)`).FindStringSubmatch(r)
		if owner == nil || uid == nil || owner[1] != uid[1] {
			t.Errorf("the same numeric id under another key got another placeholder: %v vs %v", owner, uid)
		}
	})
	t.Run("json inside text keeps its order and formatting", func(t *testing.T) {
		var content struct {
			Content []struct{ Text string } `json:"content"`
		}
		_ = json.Unmarshal(got[1].Result, &content)
		inner := content.Content[0].Text
		if !strings.HasSuffix(inner, `"zeta":1,"alpha":2}`) || !strings.HasPrefix(inner, `{"threads":[{"id":"scrubbed-id-`) {
			t.Errorf("embedded JSON was reordered or reformatted: %s", inner)
		}
	})
	t.Run("base64 text is decoded, scrubbed and encoded again", func(t *testing.T) {
		var r struct {
			StructuredContent struct {
				Attachment struct{ Data string } `json:"attachment"`
			} `json:"structuredContent"`
		}
		_ = json.Unmarshal(got[2].Result, &r)
		dec, err := base64.URLEncoding.DecodeString(r.StructuredContent.Attachment.Data)
		if err != nil || !regexp.MustCompile(`^Hello, write to user-[0-9]+@example\.com about thread scrubbed-id-[0-9]+\.$`).Match(dec) {
			t.Errorf("attachment decodes to %q (%v)", dec, err)
		}
	})
	t.Run("vendor text is data: no template, format or env expansion, no html escaping", func(t *testing.T) {
		if !strings.Contains(string(got[0].Result), dataText) {
			t.Errorf("instructions changed: %s", got[0].Result)
		}
	})
	t.Run("the cassette counts placeholders, never values", func(t *testing.T) {
		c := loadSaved(t, text)
		if c.Scrubbed["id"] < 3 || c.Scrubbed["email"] != 2 || c.Scrubbed["token"] != 1 || c.Scrubbed["literal"] != 1 {
			t.Errorf("scrubbed counts = %v", c.Scrubbed)
		}
		if c.Notice != Notice || c.TestAccount != "ci-test-account" {
			t.Errorf("notice %q, test account %q", c.Notice, c.TestAccount)
		}
	})

	t.Run("http: tokens, emails and ids in paths, headers and bodies", func(t *testing.T) {
		c := newHTTPCassette(
			HTTPExchange{
				Request: HTTPRequest{Method: "GET", Path: "/gmail/v1/users/" + strings.Replace(aliceEmail, "@", "%40", 1) + "/messages",
					Query:   "q=" + url.QueryEscape("from:"+bobEmail) + "&user_id=" + userNumber + "&cc=carol+dan%40corp-test.dev",
					Headers: hdr("Authorization", "Bearer "+known, "X-Request-Id", workspaceID)},
				Response: HTTPResponse{Status: 200, Headers: hdr("Content-Type", "application/json"),
					Body: jsonBody(map[string]any{"messages": []map[string]string{{"id": threadID, "threadId": threadID}}})}},
			HTTPExchange{
				Request: HTTPRequest{Method: "GET", Path: "/gmail/v1/users/me/messages/" + threadID,
					Headers: hdr("Authorization", "Bearer "+known)},
				Response: HTTPResponse{Status: 200, Headers: hdr("Content-Type", "text/plain; charset=utf-8"),
					Body: &Body{Text: "From: " + aliceEmail + "\r\nTo: " + bobEmail + "\r\n\r\nSee https://mail.example.test/m/" + threadID}}},
		)
		text := saveOK(t, c, ScrubConfig{})
		assertAbsent(t, text, known, threadID, workspaceID, aliceEmail, bobEmail, userNumber,
			strings.Replace(aliceEmail, "@", "%40", 1), url.QueryEscape(bobEmail), "dan%40corp-test.dev")
		xs := loadSaved(t, text).HTTP.Exchanges
		listed := regexp.MustCompile(`"id":"(scrubbed-id-[0-9]+)"`).FindStringSubmatch(compactJSON(t, xs[0].Response.Body.JSON))
		if listed == nil {
			t.Fatalf("no id placeholder in the listing: %s", xs[0].Response.Body.JSON)
		}
		if !strings.HasSuffix(xs[1].Request.Path, "/messages/"+listed[1]) {
			t.Errorf("the listed id %s is not the id the next request asked for (%s)", listed[1], xs[1].Request.Path)
		}
		if !strings.Contains(xs[1].Response.Body.Text, "https://mail.example.test/m/"+listed[1]) {
			t.Errorf("the id inside a URL in text was not replaced the same way: %q", xs[1].Response.Body.Text)
		}
		// A changed path is escaped canonically (Go keeps @ in a path); replay
		// canonicalizes paths on both sides, so either form matches.
		if !regexp.MustCompile(`^/gmail/v1/users/user-[0-9]+@example\.com/messages$`).MatchString(xs[0].Request.Path) {
			t.Errorf("path = %q", xs[0].Request.Path)
		}
	})
}

// TestScrubKeepsUnrelatedTextWhole: substitution respects id boundaries, so
// an id inside a longer word, and text with no recorded value, are kept.
func TestScrubKeepsUnrelatedTextWhole(t *testing.T) {
	c := newMCPCassette(MCPExchange{Method: "tools/call",
		Params: raw(`{"name":"get","arguments":{"id":"abc123"}}`),
		Result: raw(`{"content":[{"type":"text","text":"order xabc123y and abc123 and abc1234"}]}`)})
	text := saveOK(t, c, ScrubConfig{})
	got := loadSaved(t, text).MCP.Exchanges[0]
	if !strings.Contains(string(got.Result), "order xabc123y and scrubbed-id-1 and abc1234") {
		t.Errorf("result = %s", got.Result)
	}
}

// TestSaveRefusesBadInput: no test account, a bad label, or a too-short
// literal stop Save before anything is scrubbed or written.
func TestSaveRefusesBadInput(t *testing.T) {
	for name, tc := range map[string]struct {
		account string
		cfg     ScrubConfig
	}{
		"no test account":      {"", ScrubConfig{}},
		"an email as label":    {"alice@corp-test.dev", ScrubConfig{}},
		"a credential label":   {fakeShape("github-pat"), ScrubConfig{}},
		"a three-char literal": {"ci", ScrubConfig{Literals: []string{"abc"}}},
	} {
		t.Run(name, func(t *testing.T) {
			c := newHTTPCassette()
			c.TestAccount = tc.account
			if err := Save(t.TempDir()+"/c.json", c, tc.cfg); err == nil {
				t.Fatal("Save accepted it")
			}
		})
	}
}

// TestScrubLiteralFromPlaceholderVocabulary: a literal to scrub that also
// spells part of a placeholder ("token", "example") is replaced in the
// data, leaves every placeholder whole, and does not trip the survivor
// check on them.
func TestScrubLiteralFromPlaceholderVocabulary(t *testing.T) {
	bearer := fakeToken("bearer")
	c := newHTTPCassette(HTTPExchange{
		Request: HTTPRequest{Method: "GET", Path: "/v1/me", Headers: hdr("Authorization", "Bearer "+bearer)},
		Response: HTTPResponse{Status: 200, Headers: hdr("Content-Type", "text/plain"),
			Body: &Body{Text: "your token is ready; write to " + aliceEmail + " via example"}},
	})
	text := saveOK(t, c, ScrubConfig{Literals: []string{"token", "example"}})
	got := loadSaved(t, text).HTTP.Exchanges[0]
	if a := got.Request.Headers.Get("Authorization"); a != "Bearer scrubbed-token-1" {
		t.Errorf("Authorization = %q, the placeholder was rewritten", a)
	}
	if b := got.Response.Body.Text; !regexp.MustCompile(`^your scrubbed-literal-[0-9] is ready; write to user-[0-9]+@example\.com via scrubbed-literal-[0-9]$`).MatchString(b) {
		t.Errorf("body = %q", b)
	}
}
