package contracttest

import (
	"io"
	"net/http"
	"path/filepath"
	"strings"
	"testing"
)

func TestDiscover_CaseFile(t *testing.T) {
	root := fixtureTree(t, syntheticMeta)
	dir := filepath.Join(root, "acme-1")
	// A streaming request the upstream refused before streaming.
	writeFile(t, filepath.Join(dir, "rate_limited", RequestFile), `{"model":"acme-1","stream":true,"messages":[]}`)
	writeFile(t, filepath.Join(dir, "rate_limited", ResponseJSONFile), `{"error":{"type":"rate_limit"}}`)
	writeFile(t, filepath.Join(dir, "rate_limited", CaseFile), `{
		"status": 429, "headers": {"Retry-After": "3"}, "recordable": false, "notes": "cannot induce",
		"sanity_gaps": [{"failure": "no retry hint", "reason": "known gap: test"}]}`)
	// A non-chat endpoint with a binary body and its own content type.
	writeFile(t, filepath.Join(dir, "speech", RequestFile), `{"model":"acme-1","input":"hi"}`)
	writeFile(t, filepath.Join(dir, "speech", ResponseBinFile), "\x00\x01")
	writeFile(t, filepath.Join(dir, "speech", CaseFile), `{"endpoint": "speech", "content_type": "audio/mpeg"}`)
	// An HTML error page from a proxy.
	writeFile(t, filepath.Join(dir, "proxy_502", RequestFile), `{"model":"acme-1","messages":[]}`)
	writeFile(t, filepath.Join(dir, "proxy_502", ResponseTextFile), "<html>bad gateway</html>")
	writeFile(t, filepath.Join(dir, "proxy_502", CaseFile), `{"status": 502, "content_type": "text/html", "recordable": false, "notes": "proxy"}`)

	var cases []Case
	ft := runFake(func(tb *fakeTB) { cases = discover(tb, "acme", root, "", false) })
	if len(ft.errors) != 0 {
		t.Fatalf("unexpected errors: %q", ft.errors)
	}
	byName := map[string]Case{}
	for _, c := range cases {
		byName[c.Name] = c
	}
	if c := byName["basic"]; c.Endpoint != EndpointChat || c.Status != 200 || !c.Recordable || c.IsError() || c.Headers != nil {
		t.Errorf("a case without case.json = %+v", c)
	}
	c := byName["rate_limited"]
	if !c.IsError() || !c.IsStream || c.Status != 429 || c.Recordable || c.Headers["Retry-After"] != "3" ||
		c.ContentType != "application/json" || len(c.SanityGaps) != 1 || c.Request == nil || !c.Request.Stream {
		t.Errorf("rate_limited = %+v", c)
	}
	c = byName["speech"]
	if c.Endpoint != EndpointSpeech || c.Request != nil || !strings.Contains(string(c.RawRequest), `"input"`) ||
		c.ContentType != "audio/mpeg" || c.IsStream {
		t.Errorf("speech = %+v", c)
	}
	if c := byName["proxy_502"]; c.ContentType != "text/html" || string(c.Body) != "<html>bad gateway</html>" || !c.IsError() {
		t.Errorf("proxy_502 = %+v", c)
	}
}

func TestDiscover_RejectsBadCaseFiles(t *testing.T) {
	tests := []struct{ name, caseJSON, want string }{
		{"unknown key", `{"statuss": 429}`, "unknown field"},
		{"unknown endpoint", `{"endpoint": "chats"}`, `endpoint "chats"`},
		{"redirect status", `{"status": 302}`, "status 302"},
		{"content type as header", `{"headers": {"Content-Type": "x"}}`, "content_type"},
		{"bad header name", `{"headers": {"a b": "x"}}`, "not a valid header name"},
		{"not recordable without notes", `{"status": 529, "recordable": false}`, "needs notes"},
		{"gap without reason", `{"sanity_gaps": [{"failure": "x"}]}`, "needs a failure and a reason"},
		{"bad content type", `{"content_type": "/"}`, "content_type"},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			root := fixtureTree(t, syntheticMeta)
			writeFile(t, filepath.Join(root, "acme-1", "basic", CaseFile), tc.caseJSON)
			ft := runFake(func(tb *fakeTB) { discover(tb, "acme", root, "", false) })
			if !ft.fatal || len(ft.errors) == 0 || !strings.Contains(ft.errors[0], tc.want) {
				t.Errorf("want a fatal mentioning %q, got fatal=%v %q", tc.want, ft.fatal, ft.errors)
			}
		})
	}

	root := fixtureTree(t, syntheticMeta)
	writeFile(t, filepath.Join(root, "acme-1", "stream", CaseFile), `{"status": 500}`)
	ft := runFake(func(tb *fakeTB) { discover(tb, "acme", root, "", false) })
	if !ft.fatal || !strings.Contains(ft.errors[0], "a mid-stream error is a 200 stream") {
		t.Errorf("an error status on a stream body: %q", ft.errors)
	}
}

func TestDiscover_LiveCaseFile(t *testing.T) {
	repo := fixtureTree(t, syntheticMeta)
	writeFile(t, filepath.Join(repo, "acme-1", "basic", CaseFile), `{"status": 400}`)
	writeFile(t, filepath.Join(repo, "acme-1", "stream", CaseFile), `{"recordable": false, "notes": "synthetic only"}`)
	live := t.TempDir()
	writeFile(t, filepath.Join(live, "acme-1", "basic", ResponseJSONFile), `{"error":{}}`)
	writeFile(t, filepath.Join(live, "acme-1", "stream", ResponseSSEFile), "data: {}\n\n")

	var cases []Case
	ft := runFake(func(tb *fakeTB) { cases = discover(tb, "acme", live, repo, true) })
	if len(ft.errors) != 0 {
		t.Fatalf("unexpected errors: %q", ft.errors)
	}
	if len(cases) != 1 || cases[0].Name != "basic" || cases[0].Status != 400 {
		t.Errorf("live cases = %+v; want basic with the repo's case.json, and stream (not recordable) skipped", cases)
	}
}

func TestMeta_NewFields(t *testing.T) {
	for name, tc := range map[string]struct{ from, to, want string }{
		"cloned from itself":       {`"recorded_at": null,`, `"recorded_at": null, "cloned_from": "acme-1",`, "cloned_from"},
		"optional without reason":  {`"notes"`, `"optional_fields": [{"path": "$.x"}], "notes"`, "optional_fields"},
		"type mismatch not a path": {`"notes"`, `"allowed_type_mismatches": [{"path": "x", "reason": "r"}], "notes"`, "allowed_type_mismatches"},
	} {
		t.Run(name, func(t *testing.T) {
			root := fixtureTree(t, strings.Replace(syntheticMeta, tc.from, tc.to, 1))
			ft := runFake(func(tb *fakeTB) { discover(tb, "acme", root, "", false) })
			if !ft.fatal || !strings.Contains(ft.errors[0], tc.want) {
				t.Errorf("want a fatal mentioning %q, got %q", tc.want, ft.errors)
			}
		})
	}
	ok := strings.Replace(syntheticMeta, `"notes"`, `"cloned_from": "acme-0", "optional_fields": [{"path": "$.x", "reason": "sometimes"}], "notes"`, 1)
	var cases []Case
	ft := runFake(func(tb *fakeTB) { cases = discover(tb, "acme", fixtureTree(t, ok), "", false) })
	if len(ft.errors) != 0 || cases[0].Meta.ClonedFrom != "acme-0" || len(cases[0].Meta.OptionalFields) != 1 {
		t.Errorf("valid new fields: %q %+v", ft.errors, cases[0].Meta)
	}
}

func TestReadCaseSpec(t *testing.T) {
	dir := t.TempDir()
	s, err := ReadCaseSpec(dir)
	if err != nil || !s.CanRecord() || s.Status != 0 || s.Endpoint != "" {
		t.Errorf("absent case.json = %+v, %v", s, err)
	}
	writeFile(t, filepath.Join(dir, CaseFile), `{"recordable": false, "notes": "429"}`)
	if s, err := ReadCaseSpec(dir); err != nil || s.CanRecord() {
		t.Errorf("not recordable = %+v, %v", s, err)
	}
}

func TestReplayServer_StatusAndHeaders(t *testing.T) {
	c := Case{Body: []byte(`{"error":{}}`), ContentType: "application/json", Status: 429,
		Headers: map[string]string{"retry-after": "3", "anthropic-ratelimit-requests-remaining": "0"}}
	r := ReplayServer(t, c)
	resp, err := http.Post(r.URL+"/v1/messages", "application/json", strings.NewReader(`{}`))
	if err != nil {
		t.Fatal(err)
	}
	body, _ := io.ReadAll(resp.Body)
	_ = resp.Body.Close()
	if resp.StatusCode != 429 || resp.Header.Get("Retry-After") != "3" || resp.Header.Get("Anthropic-Ratelimit-Requests-Remaining") != "0" ||
		resp.Header.Get("Content-Type") != "application/json" || string(body) != `{"error":{}}` {
		t.Errorf("replayed %d %v %q", resp.StatusCode, resp.Header, body)
	}
}
