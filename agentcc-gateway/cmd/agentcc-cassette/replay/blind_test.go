package replay

import (
	"encoding/json"
	"io"
	"net/http"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/cmd/agentcc-cassette/cassette"
)

// recordedCredential and liveCredential stand for the credential a
// recording used and another one a replaying client holds.
const (
	recordedCredential = "recorded-Qz8vK3vK3vK3vK3vK3"
	liveCredential     = "live-Zx9vK3vK3vK3vK3vK3"
)

// TestReplayBlindsEveryScrubbedCredentialLocation: at every location a rule
// names directly (a query or form parameter, widened names included, a
// form body with no Content-Type, a JSON member, an OAuth code in a JSON
// token request, a number under a credential name), a live request
// carrying another credential there still matches. Data that only looks
// like it (?code=US, issueKey) still decides the match. A credential inside
// a URL inside a value is scrubbed but not blinded: a replaying client
// sends the placeholder the cassette holds there, as for ids and emails.
func TestReplayBlindsEveryScrubbedCredentialLocation(t *testing.T) {
	t.Run("http", func(t *testing.T) {
		jsonCT := http.Header{"Content-Type": {"application/json"}}
		resp := func(body string) cassette.HTTPResponse {
			return cassette.HTTPResponse{Status: 200, Headers: jsonCT, Body: &cassette.Body{JSON: json.RawMessage(body)}}
		}
		c := saved(t, &cassette.Cassette{Kind: cassette.KindHTTP, HTTP: &cassette.HTTPRecording{Exchanges: []cassette.HTTPExchange{
			{Request: cassette.HTTPRequest{Method: "GET", Path: "/api/v4/projects", Query: "private_token=" + recordedCredential + "&per_page=20"},
				Response: resp(`{"r":"projects"}`)},
			{Request: cassette.HTTPRequest{Method: "POST", Path: "/api/notify", Body: &cassette.Body{Text: "token=" + recordedCredential + "&channel=general"}},
				Response: resp(`{"r":"notified"}`)},
			{Request: cassette.HTTPRequest{Method: "POST", Path: "/v1/oauth/token", Headers: jsonCT, Body: &cassette.Body{JSON: json.RawMessage(
				`{"grant_type":"authorization_code","code":"` + recordedCredential + `","redirect_uri":"https://app.example.test/cb"}`)}},
				Response: resp(`{"r":"token"}`)},
			{Request: cassette.HTTPRequest{Method: "POST", Path: "/v2/meetings", Headers: jsonCT,
				Body: &cassette.Body{JSON: json.RawMessage(`{"topic":"standup","password":87654321}`)}}, Response: resp(`{"r":"meeting"}`)},
			{Request: cassette.HTTPRequest{Method: "GET", Path: "/v1/countries", Query: "code=US"}, Response: resp(`{"r":"US"}`)},
		}}})
		_, srv, _ := serve(t, c)
		for _, tc := range []struct{ method, path, ct, body, want string }{
			{"GET", "/api/v4/projects?per_page=20&private_token=" + liveCredential, "", "", `{"r":"projects"}`},
			{"POST", "/api/notify", "", "token=" + liveCredential + "&channel=general", `{"r":"notified"}`},
			{"POST", "/v1/oauth/token", "application/json",
				`{"redirect_uri":"https://app.example.test/cb","code":"` + liveCredential + `","grant_type":"authorization_code"}`, `{"r":"token"}`},
			{"POST", "/v2/meetings", "application/json", `{"password":11112222,"topic":"standup"}`, `{"r":"meeting"}`},
			{"GET", "/v1/countries?code=US", "", "", `{"r":"US"}`},
		} {
			code, got, miss := doCT(t, tc.method, srv.URL+tc.path, tc.ct, tc.body)
			if code != 200 || miss || compact(t, got) != tc.want {
				t.Errorf("%s %s = %d %s (miss %v), want %s", tc.method, tc.path, code, got, miss, tc.want)
			}
		}
		for _, tc := range []struct{ method, path, ct, body string }{
			{"GET", "/v1/countries?code=CA", "", ""},
			{"POST", "/api/notify", "", "token=" + liveCredential + "&channel=random"},
		} {
			if code, _, miss := doCT(t, tc.method, srv.URL+tc.path, tc.ct, tc.body); code != http.StatusNotImplemented || !miss {
				t.Errorf("%s %s %q = %d (miss %v), want a loud miss", tc.method, tc.path, tc.body, code, miss)
			}
		}
	})
	t.Run("mcp", func(t *testing.T) {
		c := saved(t, &cassette.Cassette{Kind: cassette.KindMCP, MCP: &cassette.MCPRecording{ProtocolVersion: "2025-11-25",
			Exchanges: []cassette.MCPExchange{
				toolExchange("post_message", `{"channel":"general","app_token":"`+recordedCredential+`"}`, "posted"),
				toolExchange("join", `{"meeting":"standup","password":87654321}`, "joined"),
				toolExchange("get_issue", `{"issueKey":"PROJ-1"}`, "issue one"),
				toolExchange("get_issue", `{"issueKey":"PROJ-2"}`, "issue two"),
			}}})
		_, srv, _ := serve(t, c)
		for _, tc := range []struct{ name, args, want string }{
			{"post_message", `{"app_token":"` + liveCredential + `","channel":"general"}`, "posted"},
			{"join", `{"password":11112222,"meeting":"standup"}`, "joined"},
			{"get_issue", `{"issueKey":"PROJ-2"}`, "issue two"},
			{"get_issue", `{"issueKey":"PROJ-1"}`, "issue one"},
		} {
			if got := textOf(t, callTool(t, srv.URL, tc.name, tc.args)); got != tc.want {
				t.Errorf("%s %s = %q, want %q", tc.name, tc.args, got, tc.want)
			}
		}
	})
}

// doCT sends one request with the given Content-Type ("" for none).
func doCT(t *testing.T, method, url, ct, body string) (int, string, bool) {
	t.Helper()
	req, err := http.NewRequest(method, url, strings.NewReader(body))
	if err != nil {
		t.Fatal(err)
	}
	if ct != "" {
		req.Header.Set("Content-Type", ct)
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	data, _ := io.ReadAll(resp.Body)
	return resp.StatusCode, string(data), resp.Header.Get(MissHeader) == "miss"
}

// TestReplayHonoursRecordedRules: credential names a recording added
// (--secret-param, --secret-key) travel in the cassette, and replay blinds
// them for every cassette it serves, so a live credential there still
// matches while the rest of the request still decides.
func TestReplayHonoursRecordedRules(t *testing.T) {
	cfg := cassette.ScrubConfig{SecretParams: []string{"vendor_cred"}, SecretKeys: []string{"opaqueHandle"}}
	jsonCT := http.Header{"Content-Type": {"application/json"}}
	resp := func(body string) cassette.HTTPResponse {
		return cassette.HTTPResponse{Status: 200, Headers: jsonCT, Body: &cassette.Body{JSON: json.RawMessage(body)}}
	}
	withRules := savedWith(t, &cassette.Cassette{Kind: cassette.KindHTTP, HTTP: &cassette.HTTPRecording{Exchanges: []cassette.HTTPExchange{
		{Request: cassette.HTTPRequest{Method: "GET", Path: "/v1/run", Query: "vendor_cred=" + recordedCredential + "&page=2"},
			Response: resp(`{"r":"page 2"}`)},
		{Request: cassette.HTTPRequest{Method: "POST", Path: "/v1/run", Headers: jsonCT,
			Body: &cassette.Body{JSON: json.RawMessage(`{"opaqueHandle":"` + recordedCredential + `","q":"x"}`)}}, Response: resp(`{"r":"ran"}`)},
	}}}, cfg)
	other := saved(t, &cassette.Cassette{Kind: cassette.KindHTTP, HTTP: &cassette.HTTPRecording{Exchanges: []cassette.HTTPExchange{
		{Request: cassette.HTTPRequest{Method: "GET", Path: "/v1/status", Query: "vendor_cred=" + recordedCredential},
			Response: resp(`{"r":"status"}`)},
	}}})
	_, srv, _ := serve(t, withRules, other)
	for _, tc := range []struct{ method, path, ct, body, want string }{
		{"GET", "/v1/run?page=2&vendor_cred=" + liveCredential, "", "", `{"r":"page 2"}`},
		{"POST", "/v1/run", "application/json", `{"q":"x","opaqueHandle":"` + liveCredential + `"}`, `{"r":"ran"}`},
		{"GET", "/v1/status?vendor_cred=" + liveCredential, "", "", `{"r":"status"}`},
	} {
		code, got, miss := doCT(t, tc.method, srv.URL+tc.path, tc.ct, tc.body)
		if code != 200 || miss || compact(t, got) != tc.want {
			t.Errorf("%s %s = %d %s (miss %v), want %s", tc.method, tc.path, code, got, miss, tc.want)
		}
	}
	if code, _, miss := doCT(t, "GET", srv.URL+"/v1/run?page=3&vendor_cred="+liveCredential, "", ""); code != http.StatusNotImplemented || !miss {
		t.Errorf("page 3 = %d (miss %v), want a loud miss", code, miss)
	}
	m := savedWith(t, &cassette.Cassette{Kind: cassette.KindMCP, MCP: &cassette.MCPRecording{ProtocolVersion: "2025-11-25",
		Exchanges: []cassette.MCPExchange{toolExchange("run", `{"opaqueHandle":"`+recordedCredential+`","q":"x"}`, "ran")}}}, cfg)
	_, msrv, _ := serve(t, m)
	if got := textOf(t, callTool(t, msrv.URL, "run", `{"q":"x","opaqueHandle":"`+liveCredential+`"}`)); got != "ran" {
		t.Errorf("run = %q", got)
	}
}
