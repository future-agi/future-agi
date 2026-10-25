package mcp

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/cmd/agentcc-mcpmock/mcpmock"
	xmock "github.com/futureagi/agentcc-gateway/cmd/agentcc-mockup/mock"
	gatewayadmin "github.com/futureagi/agentcc-gateway/internal/contracts/generated"
	"github.com/futureagi/agentcc-gateway/internal/mcp/egress"
)

// commonToolsHash is ToolsHash of scripts/scenarios/fixtures/mcp/common's
// tools, cross-checked in Python:
//
//	keys = ["name", "title", "description", "inputSchema", "outputSchema", "annotations"]
//	proj = sorted(({k: t[k] for k in keys if k in t} for t in tools), key=lambda t: t.get("name", ""))
//	hashlib.sha256(json.dumps(proj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
//
// Python agrees on this fixture only: none of the hashed fields holds a
// number literal Python would rewrite (1.50, 1e3) or a U+2028/U+2029, where
// the two forms differ (ToolsHash's doc; "the canonical form's exact rules"
// below pins them).
const commonToolsHash = "de7881ed4177c8802be096d89d0d7ec27a9eebf68b90430a93dcfeb29774ab97"

const mcpFixtures = "../../scripts/scenarios/fixtures/mcp/common"

// probeServer is an MCP server whose dialer is the egress guard with allow
// as the operator's private_allow.
func probeServer(t *testing.T, allow ...string) *Server {
	t.Helper()
	s := NewServer(ServerConfig{})
	t.Cleanup(s.Close)
	d, err := egress.New(egress.Options{PrivateAllow: allow})
	if err != nil {
		t.Fatal(err)
	}
	s.SetDeps(Deps{Dialer: d})
	return s
}

// upstream starts an agentcc-mcpmock of era on loopback.
func upstream(t *testing.T, era string, edit func(*mcpmock.Config)) (*httptest.Server, *mcpmock.Mock) {
	t.Helper()
	fix, err := mcpmock.LoadFixtures(mcpFixtures)
	if err != nil {
		t.Fatal(err)
	}
	e, err := mcpmock.ParseEra(era)
	if err != nil {
		t.Fatal(err)
	}
	cfg := mcpmock.Config{Era: e, Auth: "none", Path: "/mcp", ServerName: "probe-mock-" + era}
	if edit != nil {
		edit(&cfg)
	}
	m, err := mcpmock.New(cfg, fix)
	if err != nil {
		t.Fatal(err)
	}
	srv := httptest.NewServer(m.Handler())
	t.Cleanup(srv.Close)
	m.SetPublicURL(srv.URL)
	return srv, m
}

func hostOf(t *testing.T, raw string) string {
	t.Helper()
	u, err := url.Parse(raw)
	if err != nil {
		t.Fatal(err)
	}
	return u.Host
}

// probe posts body to HandleProbe and returns the status, the raw response
// and the decoded result.
func probe(t *testing.T, s *Server, body string) (int, string, ProbeResult) {
	t.Helper()
	rec := httptest.NewRecorder()
	s.HandleProbe(rec, httptest.NewRequest(http.MethodPost, "/-/mcp/probe", strings.NewReader(body)))
	var res ProbeResult
	if rec.Code == http.StatusOK {
		if err := json.Unmarshal(rec.Body.Bytes(), &res); err != nil {
			t.Fatalf("decode %s: %v", rec.Body.String(), err)
		}
	}
	return rec.Code, rec.Body.String(), res
}

func probeBody(fields map[string]any) string {
	b, _ := json.Marshal(fields)
	return string(b)
}

func errorCode(r ProbeResult) string {
	if r.Error == nil {
		return ""
	}
	return *r.Error
}

// TestProbeReportsEraToolsHash: the probe detects each era the gateway
// speaks, lists every tool (following pages, JSON or SSE answers) and
// reports the server, the count and the canonical tools hash; it stores
// nothing and ends the legacy session it opened.
func TestProbeReportsEraToolsHash(t *testing.T) {
	for _, c := range []struct {
		era, path, transport, want string
		behavior                   mcpmock.Behavior
	}{
		{era: "2026", path: "/mcp", want: "2026-07-28"},
		{era: "2025-11-25", path: "/mcp", want: "2025-11-25"},
		{era: "2025-06-18", path: "/mcp", want: "2025-06-18"},
		{era: "2025-03-26", path: "/mcp", want: "2025-03-26"},
		{era: "2024-11-05", path: "/sse", want: "2024-11-05"},
		{era: "dual", path: "/mcp", want: "2026-07-28"},
		{era: "dual", path: "/mcp", transport: "http", want: "2026-07-28"},
		{era: "dual", path: "/sse", transport: "sse", want: "2024-11-05"},
		{era: "2026", path: "/mcp", want: "2026-07-28", behavior: mcpmock.Behavior{SSE: true, PageSize: 1}},
		{era: "2025-06-18", path: "/mcp", want: "2025-06-18", behavior: mcpmock.Behavior{SSE: true, PageSize: 2}},
		{era: "2024-11-05", path: "/sse", want: "2024-11-05", behavior: mcpmock.Behavior{PageSize: 1}},
	} {
		name := fmt.Sprintf("%s%s transport=%q sse=%v page=%d", c.era, c.path, c.transport, c.behavior.SSE, c.behavior.PageSize)
		t.Run(name, func(t *testing.T) {
			srv, m := upstream(t, c.era, nil)
			m.State().SetBehavior(c.behavior)
			s := probeServer(t, hostOf(t, srv.URL))
			fields := map[string]any{"org_id": "org-a", "url": srv.URL + c.path}
			if c.transport != "" {
				fields["transport"] = c.transport
			}
			code, raw, res := probe(t, s, probeBody(fields))
			if code != http.StatusOK || !res.OK {
				t.Fatalf("status %d, body %s; want ok", code, raw)
			}
			if res.Era != c.want {
				t.Errorf("era = %q, want %q", res.Era, c.want)
			}
			if res.ToolCount == nil || *res.ToolCount != 3 {
				t.Errorf("tool_count = %v, want 3", res.ToolCount)
			}
			if res.ToolsHash != commonToolsHash {
				t.Errorf("tools_hash = %s, want %s", res.ToolsHash, commonToolsHash)
			}
			if res.ServerInfo == nil || res.ServerInfo.Name != "probe-mock-"+c.era {
				t.Errorf("server_info = %+v, want the mock's name", res.ServerInfo)
			}
			if !strings.Contains(raw, `"error":null`) || res.LatencyMS < 0 {
				t.Errorf("body %s: want error null and a latency", raw)
			}
			if len(s.ClientStatuses()) != 0 || s.Registry().ToolCount() != 0 {
				t.Error("the probe registered an upstream; it must store nothing")
			}
			if strings.HasPrefix(c.want, "2025") {
				deleted := false
				for _, r := range m.State().ReceivedLast(0, "") {
					deleted = deleted || r.HTTPMethod == http.MethodDelete
				}
				if !deleted {
					t.Error("the probe left its legacy session open (no DELETE)")
				}
			}
		})
	}

	t.Run("a 2024-11-05 upstream is not Streamable HTTP", func(t *testing.T) {
		srv, _ := upstream(t, "2024-11-05", nil)
		s := probeServer(t, hostOf(t, srv.URL))
		_, raw, res := probe(t, s, probeBody(map[string]any{"org_id": "org-a", "url": srv.URL + "/sse", "transport": "http"}))
		if res.OK || errorCode(res) != ProbeErrNotMCP {
			t.Fatalf("transport http on an HTTP+SSE upstream: %s, want not_mcp", raw)
		}
	})

	t.Run("the hash is canonical", func(t *testing.T) {
		a := []json.RawMessage{
			json.RawMessage(`{"name":"b","description":"B","inputSchema":{"type":"object","properties":{"x":{"type":"number","maximum":1.50}}}}`),
			json.RawMessage(`{"name":"a","title":"A","annotations":{"readOnlyHint":true},"inputSchema":{"type":"object"}}`),
		}
		b := []json.RawMessage{
			json.RawMessage(`{"inputSchema":{"type":"object"},"annotations":{"readOnlyHint":true},"title":"A","name":"a","_meta":{"x":1},"execution":{"taskSupport":"optional"}}`),
			json.RawMessage("{\n  \"inputSchema\": {\"properties\": {\"x\": {\"maximum\": 1.50, \"type\": \"number\"}}, \"type\": \"object\"},\n  \"description\": \"B\", \"name\": \"b\"}"),
		}
		ha, err := ToolsHash(a)
		if err != nil {
			t.Fatal(err)
		}
		hb, _ := ToolsHash(b)
		if ha != hb {
			t.Fatalf("order, key order, whitespace and other fields changed the hash: %s != %s", ha, hb)
		}
		want := sha256.Sum256([]byte(`[{"annotations":{"readOnlyHint":true},"inputSchema":{"type":"object"},"name":"a","title":"A"},` +
			`{"description":"B","inputSchema":{"properties":{"x":{"maximum":1.50,"type":"number"}},"type":"object"},"name":"b"}]`))
		if ha != hex.EncodeToString(want[:]) {
			t.Fatalf("hash %s is not the sha256 of the canonical form", ha)
		}
		changed := []json.RawMessage{a[0], json.RawMessage(`{"name":"a","title":"A","annotations":{"readOnlyHint":false},"inputSchema":{"type":"object"}}`)}
		if hc, _ := ToolsHash(changed); hc == ha {
			t.Fatal("an annotation change did not change the hash")
		}
		html := []json.RawMessage{json.RawMessage(`{"name":"t","description":"a <b> & c"}`)}
		hh, _ := ToolsHash(html)
		raw := sha256.Sum256([]byte(`[{"description":"a <b> & c","name":"t"}]`))
		if hh != hex.EncodeToString(raw[:]) {
			t.Fatal("the canonical form escapes HTML characters; it must keep them as sent")
		}
	})

	// The rules ToolsHash's doc states, byte for byte: number literals as
	// written, keys in UTF-8 byte order, and encoding/json's string escapes
	// (short \b \f, lowercase \u00xx, U+2028/U+2029 escaped, a lone
	// surrogate escape or invalid UTF-8 as U+FFFD). Any recompute outside Go
	// depends on exactly this form.
	t.Run("the canonical form's exact rules", func(t *testing.T) {
		for _, c := range []struct{ name, tool, canon string }{
			{
				"numbers as written, keys by UTF-8 bytes (U+E000 before U+1F600, unlike JCS's UTF-16 order)",
				`{"name":"n","inputSchema":{"\ud83d\ude00":4,"\ue000":3,"\u00e9":2,"z":1,"a":1.50,"b":1e3,"c":-0,"d":12345678901234567890,"e":1.0}}`,
				"{\"inputSchema\":{\"a\":1.50,\"b\":1e3,\"c\":-0,\"d\":12345678901234567890,\"e\":1.0,\"z\":1,\"\u00e9\":2,\"\ue000\":3,\"\U0001F600\":4},\"name\":\"n\"}",
			},
			{
				"string escapes",
				`{"name":"s","description":"a\u2028b\u2029c\bd\fe\u0001f\"g\\h\ni\rj\tk/\u00e9"}`,
				`{"description":"a\u2028b\u2029c\bd\fe\u0001f\"g\\h\ni\rj\tk/` + "\u00e9" + `","name":"s"}`,
			},
			{
				"a lone surrogate escape and invalid UTF-8",
				"{\"name\":\"u\",\"title\":\"x\\ud800y\",\"description\":\"p\xffq\"}",
				"{\"description\":\"p\ufffdq\",\"name\":\"u\",\"title\":\"x\ufffdy\"}",
			},
		} {
			got, err := ToolsHash([]json.RawMessage{json.RawMessage(c.tool)})
			if err != nil {
				t.Fatalf("%s: %v", c.name, err)
			}
			want := sha256.Sum256([]byte("[" + c.canon + "]"))
			if got != hex.EncodeToString(want[:]) {
				t.Errorf("%s: the hash is not the sha256 of [%s]", c.name, c.canon)
			}
		}
	})
}

// TestProbeNotMCP: an HTTP server that does not speak MCP, in any era, is
// reported as not_mcp, with no era, count or hash.
func TestProbeNotMCP(t *testing.T) {
	fix, err := xmock.LoadFixtures("../../scripts/scenarios/fixtures/common")
	if err != nil {
		t.Fatal(err)
	}
	providerMock := httptest.NewServer(xmock.New(fix))
	defer providerMock.Close()

	handlers := map[string]http.HandlerFunc{
		"html page": func(w http.ResponseWriter, _ *http.Request) {
			w.Header().Set("Content-Type", "text/html")
			io.WriteString(w, "<html><body>hello</body></html>")
		},
		"json that is not JSON-RPC": func(w http.ResponseWriter, _ *http.Request) {
			w.Header().Set("Content-Type", "application/json")
			io.WriteString(w, `{"ok":true}`)
		},
		"JSON-RPC errors only": func(w http.ResponseWriter, r *http.Request) {
			if r.Method != http.MethodPost {
				w.WriteHeader(http.StatusMethodNotAllowed)
				return
			}
			w.Header().Set("Content-Type", "application/json")
			io.WriteString(w, `{"jsonrpc":"2.0","id":1,"error":{"code":-32601,"message":"method not found"}}`)
		},
		"an event stream without an endpoint": func(w http.ResponseWriter, r *http.Request) {
			if r.Method != http.MethodGet {
				w.WriteHeader(http.StatusMethodNotAllowed)
				return
			}
			w.Header().Set("Content-Type", "text/event-stream")
			io.WriteString(w, "event: message\ndata: {}\n\n")
		},
		"an endpoint on another origin": func(w http.ResponseWriter, r *http.Request) {
			if r.Method != http.MethodGet {
				w.WriteHeader(http.StatusNotFound)
				return
			}
			w.Header().Set("Content-Type", "text/event-stream")
			io.WriteString(w, "event: endpoint\ndata: https://elsewhere.example/messages?sessionId=1\n\n")
		},
	}
	urls := map[string]string{"the provider mock": providerMock.URL + "/", "the provider mock's chat route": providerMock.URL + "/v1/chat/completions"}
	for name, h := range handlers {
		srv := httptest.NewServer(h)
		defer srv.Close()
		urls[name] = srv.URL + "/mcp"
	}
	for name, u := range urls {
		t.Run(name, func(t *testing.T) {
			s := probeServer(t, hostOf(t, u))
			code, raw, res := probe(t, s, probeBody(map[string]any{"org_id": "org-a", "url": u}))
			if code != http.StatusOK || res.OK || errorCode(res) != ProbeErrNotMCP {
				t.Fatalf("status %d, body %s; want ok false, error not_mcp", code, raw)
			}
			if res.Era != "" || res.ToolCount != nil || res.ToolsHash != "" || res.ServerInfo != nil {
				t.Fatalf("a failed probe reported details: %s", raw)
			}
		})
	}
}

// lockedBuffer is a log sink safe for concurrent writes.
type lockedBuffer struct {
	mu sync.Mutex
	b  bytes.Buffer
}

func (l *lockedBuffer) Write(p []byte) (int, error) {
	l.mu.Lock()
	defer l.mu.Unlock()
	return l.b.Write(p)
}

func (l *lockedBuffer) String() string {
	l.mu.Lock()
	defer l.mu.Unlock()
	return l.b.String()
}

// TestProbeInlineAuthNeverLogged: an inline bearer reaches the upstream on
// the probe's own connection, and neither the gateway log (at debug) nor
// the response carries it, on success or failure.
func TestProbeInlineAuthNeverLogged(t *testing.T) {
	const secret = "probe-inline-secret-7f3a9c41"
	logs := &lockedBuffer{}
	old := slog.Default()
	slog.SetDefault(slog.New(slog.NewTextHandler(logs, &slog.HandlerOptions{Level: slog.LevelDebug})))
	defer slog.SetDefault(old)

	for _, era := range []string{"dual", "2025-06-18"} {
		srv, m := upstream(t, era, func(c *mcpmock.Config) { c.Auth = "bearer:" + secret })
		s := probeServer(t, hostOf(t, srv.URL))
		body := probeBody(map[string]any{"org_id": "org-a", "url": srv.URL + "/mcp",
			"auth": map[string]any{"type": "bearer", "header": "Authorization", "value": secret}})
		code, raw, res := probe(t, s, body)
		if code != http.StatusOK || !res.OK {
			t.Fatalf("%s: status %d, body %s; want ok with the inline bearer", era, code, raw)
		}
		want := sha256.Sum256([]byte("Bearer " + secret))
		received := m.State().ReceivedLast(0, "")
		if len(received) == 0 {
			t.Fatalf("%s: the upstream received nothing", era)
		}
		for _, r := range received {
			if !r.Auth.Present || r.Auth.SHA256 != hex.EncodeToString(want[:]) {
				t.Fatalf("%s: %s %s reached the upstream without the inline bearer: %+v", era, r.HTTPMethod, r.Method, r.Auth)
			}
		}
		if strings.Contains(raw, secret) {
			t.Fatalf("%s: the response carries the secret: %s", era, raw)
		}

		wrong := "probe-wrong-secret-0b12cd"
		_, raw, res = probe(t, s, probeBody(map[string]any{"org_id": "org-a", "url": srv.URL + "/mcp",
			"auth": map[string]any{"type": "bearer", "value": wrong}}))
		if res.OK || errorCode(res) != ProbeErrAuthRequired || strings.Contains(raw, wrong) {
			t.Fatalf("%s: a wrong bearer: %s, want auth_required without the secret", era, raw)
		}
		_, raw, res = probe(t, s, probeBody(map[string]any{"org_id": "org-a", "url": srv.URL + "/mcp"}))
		if res.OK || errorCode(res) != ProbeErrAuthRequired {
			t.Fatalf("%s: no credential: %s, want auth_required", era, raw)
		}
	}

	t.Run("an api key in its own header", func(t *testing.T) {
		var mu sync.Mutex
		var seen []string
		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			mu.Lock()
			seen = append(seen, r.Header.Get("X-Api-Key"))
			mu.Unlock()
			w.WriteHeader(http.StatusNotFound)
		}))
		defer srv.Close()
		s := probeServer(t, hostOf(t, srv.URL))
		_, raw, _ := probe(t, s, probeBody(map[string]any{"org_id": "org-a", "url": srv.URL + "/mcp",
			"auth": map[string]any{"type": "api_key", "header": "X-Api-Key", "value": secret}}))
		mu.Lock()
		defer mu.Unlock()
		if len(seen) == 0 || strings.Contains(raw, secret) {
			t.Fatalf("api_key auth: the upstream saw %q; response %s", seen, raw)
		}
		for _, v := range seen {
			if v != secret {
				t.Fatalf("a probe request reached the upstream without the api key: %q", seen)
			}
		}
	})

	req, err := DecodeProbeRequest(strings.NewReader(probeBody(map[string]any{"org_id": "o", "url": "https://x",
		"auth": map[string]any{"type": "bearer", "value": secret}})))
	if err != nil {
		t.Fatal(err)
	}
	if printed := fmt.Sprintf("%v %+v %#v %s", req, req, req, req.Auth); strings.Contains(printed, secret) {
		t.Fatalf("printing the request shows the secret: %s", printed)
	}
	if !strings.Contains(logs.String(), "mcp probe") {
		t.Fatal("the probe logged nothing; the check would pass vacuously")
	}
	if strings.Contains(logs.String(), secret) || strings.Contains(logs.String(), "probe-wrong-secret") {
		t.Fatalf("the gateway log carries an inline secret:\n%s", logs.String())
	}
}

// TestOrgConfigCannotOpenPrivate: only the operator list opens private egress.
// A probe names an org, but neither its body nor the org's config can mark an
// upstream private: such keys are refused, and an unlisted loopback upstream
// stays refused and unreached.
func TestOrgConfigCannotOpenPrivate(t *testing.T) {
	srv, m := upstream(t, "dual", nil)
	s := probeServer(t) // the operator listed nothing
	base := map[string]any{"org_id": "org-a", "url": srv.URL + "/mcp"}

	code, raw, res := probe(t, s, probeBody(base))
	if code != http.StatusOK || res.OK || errorCode(res) != ProbeErrEgressRefused {
		t.Fatalf("unlisted loopback upstream: status %d, body %s; want egress_refused", code, raw)
	}
	for _, key := range []string{"private_network", "private_allow", "allow_private", "egress"} {
		fields := map[string]any{"org_id": "org-a", "url": srv.URL + "/mcp"}
		fields[key] = map[string]any{"private_network": true, "egress": map[string]any{"private_allow": []string{"127.0.0.0/8"}},
			"private_allow": []string{"127.0.0.0/8"}, "allow_private": true}[key]
		if code, raw, _ := probe(t, s, probeBody(fields)); code != http.StatusBadRequest {
			t.Errorf("a probe body carrying %s: status %d (%s), want 400", key, code, raw)
		}
	}
	if n := len(m.State().ReceivedLast(0, "")); n != 0 {
		t.Fatalf("the unlisted upstream was reached %d times", n)
	}

	for name, cfg := range map[string]string{
		"an org egress list":       `{"mcp": {"enabled": true, "egress": {"private_allow": ["127.0.0.0/8"]}}}`,
		"a private_network server": `{"mcp": {"servers": {"s": {"url": "` + srv.URL + `/mcp", "private_network": true}}}}`,
	} {
		var contract gatewayadmin.OrgConfig
		dec := json.NewDecoder(strings.NewReader(cfg))
		dec.DisallowUnknownFields()
		if err := dec.Decode(&contract); err == nil {
			t.Errorf("%s: the org-config contract accepted it", name)
		}
	}
}

// TestProbeRequestRefusals: what the probe answers 400 to, and what it
// answers as a failed probe.
func TestProbeRequestRefusals(t *testing.T) {
	s := probeServer(t)
	for name, body := range map[string]string{
		"not JSON":                 `{"org_id":`,
		"no org_id":                `{"url":"https://mcp.example.com/mcp"}`,
		"no url":                   `{"org_id":"org-a"}`,
		"credential_id":            `{"org_id":"org-a","url":"https://mcp.example.com/mcp","credential_id":"cred_1"}`,
		"credential_id null":       `{"org_id":"org-a","url":"https://mcp.example.com/mcp","credential_id":null}`,
		"unknown transport":        `{"org_id":"org-a","url":"https://mcp.example.com/mcp","transport":"stdio"}`,
		"unknown auth type":        `{"org_id":"org-a","url":"https://mcp.example.com/mcp","auth":{"type":"basic","value":"x"}}`,
		"api_key without header":   `{"org_id":"org-a","url":"https://mcp.example.com/mcp","auth":{"type":"api_key","value":"x"}}`,
		"auth on a gateway header": `{"org_id":"org-a","url":"https://mcp.example.com/mcp","auth":{"type":"api_key","header":"Mcp-Session-Id","value":"x"}}`,
		"empty auth value":         `{"org_id":"org-a","url":"https://mcp.example.com/mcp","auth":{"type":"bearer","value":""}}`,
		"two objects":              `{"org_id":"org-a","url":"https://mcp.example.com/mcp"}{}`,
		"too large":                `{"org_id":"org-a","url":"https://mcp.example.com/mcp","transport":"` + strings.Repeat("x", 70<<10) + `"}`,
	} {
		if code, raw, _ := probe(t, s, body); code != http.StatusBadRequest || !strings.Contains(raw, "invalid_probe_request") {
			t.Errorf("%s: status %d (%s), want 400 invalid_probe_request", name, code, raw)
		}
	}
	for _, u := range []string{"ftp://mcp.example.com/mcp", "mcp.example.com/mcp", "https://user:pw@mcp.example.com/mcp", "https:///mcp", "://x"} {
		_, raw, res := probe(t, s, probeBody(map[string]any{"org_id": "org-a", "url": u}))
		if res.OK || errorCode(res) != ProbeErrInvalidURL || strings.Contains(raw, "pw@") {
			t.Errorf("url %q: %s, want invalid_url", u, raw)
		}
	}
	_, raw, res := probe(t, s, probeBody(map[string]any{"org_id": "org-a", "url": "http://192.0.2.10:9/mcp"}))
	if res.OK || errorCode(res) != ProbeErrHTTPSRequired {
		t.Errorf("plaintext to a public address: %s, want https_required", raw)
	}
	_, raw, res = probe(t, s, probeBody(map[string]any{"org_id": "org-a", "url": "http://169.254.169.254/"}))
	if res.OK || errorCode(res) != ProbeErrEgressRefused {
		t.Errorf("the metadata service: %s, want egress_refused", raw)
	}

	t.Run("without the egress guard installed, the probe still dials through one", func(t *testing.T) {
		srv, m := upstream(t, "dual", nil)
		bare := NewServer(ServerConfig{}) // Deps.Dialer is the default net.Dialer
		defer bare.Close()
		_, raw, res := probe(t, bare, probeBody(map[string]any{"org_id": "org-a", "url": srv.URL + "/mcp"}))
		if res.OK || errorCode(res) != ProbeErrEgressRefused || len(m.State().ReceivedLast(0, "")) != 0 {
			t.Fatalf("an unguarded server probed loopback: %s", raw)
		}
	})
}

// TestProbeResultMatchesContract: results decode strictly into the generated
// mcp-probe-v1 types, and so does the golden request.
func TestProbeResultMatchesContract(t *testing.T) {
	srv, _ := upstream(t, "dual", nil)
	s := probeServer(t, hostOf(t, srv.URL))
	for _, u := range []string{srv.URL + "/mcp", "http://169.254.169.254/"} {
		_, raw, _ := probe(t, s, probeBody(map[string]any{"org_id": "org-a", "url": u}))
		var got gatewayadmin.MCPProbeResult
		dec := json.NewDecoder(strings.NewReader(raw))
		dec.DisallowUnknownFields()
		if err := dec.Decode(&got); err != nil {
			t.Fatalf("%s does not match MCPProbeResult: %v", raw, err)
		}
		if got.Ok && (got.Era == nil || got.ToolCount == nil || got.ToolsHash == nil || got.ServerInfo == nil || got.Error != nil) {
			t.Fatalf("an ok result is missing a field: %s", raw)
		}
		if !got.Ok && (got.Error == nil || got.Era != nil || got.ToolsHash != nil) {
			t.Fatalf("a failed result is malformed: %s", raw)
		}
	}
}

// stateless2026 is a minimal 2026-07-28 upstream: server/discover, and
// tools/list in pages pages of one tool each, every description padded to
// pad bytes. connState, if set, watches its connections.
func stateless2026(t *testing.T, pages, pad int, connState func(net.Conn, http.ConnState)) *httptest.Server {
	t.Helper()
	srv := httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var msg struct {
			ID     int    `json:"id"`
			Method string `json:"method"`
			Params struct {
				Cursor string `json:"cursor"`
			} `json:"params"`
		}
		if r.Method != http.MethodPost || json.NewDecoder(r.Body).Decode(&msg) != nil {
			w.WriteHeader(http.StatusMethodNotAllowed)
			return
		}
		var result map[string]any
		switch msg.Method {
		case "server/discover":
			result = map[string]any{"supportedVersions": []string{"2026-07-28"}, "serverInfo": map[string]string{"name": "paged"}}
		case "tools/list":
			page := 0
			if msg.Params.Cursor != "" {
				page, _ = strconv.Atoi(msg.Params.Cursor)
			}
			result = map[string]any{"tools": []map[string]any{{"name": fmt.Sprintf("t%03d", page), "description": strings.Repeat("d", pad)}}}
			if page+1 < pages {
				result["nextCursor"] = fmt.Sprint(page + 1)
			}
		default:
			w.WriteHeader(http.StatusNotFound)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{"jsonrpc": "2.0", "id": msg.ID, "result": result})
	}))
	srv.Config.ConnState = connState
	srv.Start()
	t.Cleanup(srv.Close)
	return srv
}

// TestProbeBoundsTheListingAndItsConnections: a listing larger than the cap
// fails as upstream_error instead of being held, and no connection of the
// probe outlives it.
func TestProbeBoundsTheListingAndItsConnections(t *testing.T) {
	var mu sync.Mutex
	open := 0
	small := stateless2026(t, 7, 10, func(_ net.Conn, st http.ConnState) {
		mu.Lock()
		defer mu.Unlock()
		switch st {
		case http.StateNew:
			open++
		case http.StateClosed, http.StateHijacked:
			open--
		}
	})
	s := probeServer(t, hostOf(t, small.URL))
	_, raw, res := probe(t, s, probeBody(map[string]any{"org_id": "org-a", "url": small.URL + "/mcp"}))
	if !res.OK || res.ToolCount == nil || *res.ToolCount != 7 {
		t.Fatalf("seven one-tool pages: %s, want ok with 7 tools", raw)
	}
	deadline := time.Now().Add(2 * time.Second)
	for {
		mu.Lock()
		n := open
		mu.Unlock()
		if n == 0 {
			break
		}
		if time.Now().After(deadline) {
			t.Fatalf("%d connection(s) of the probe are still open after it returned", n)
		}
		time.Sleep(10 * time.Millisecond)
	}

	big := stateless2026(t, 4, 3<<20, nil) // 4 pages of 3 MiB: over the 8 MiB cap
	s = probeServer(t, hostOf(t, big.URL))
	_, raw, res = probe(t, s, probeBody(map[string]any{"org_id": "org-a", "url": big.URL + "/mcp"}))
	if res.OK || errorCode(res) != ProbeErrUpstream {
		t.Fatalf("a 12 MiB listing: %.200s, want upstream_error", raw)
	}
}

// TestProbeFailureCodes: each way an upstream can fail after the guard let
// the probe through has its own code.
func TestProbeFailureCodes(t *testing.T) {
	// rpc answers each POSTed request with answer's status and result or
	// error member, echoing the request's id.
	rpc := func(answer func(method string) (status int, member string)) *httptest.Server {
		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			var msg struct {
				ID     json.RawMessage `json:"id"`
				Method string          `json:"method"`
			}
			if r.Method != http.MethodPost || json.NewDecoder(r.Body).Decode(&msg) != nil {
				w.WriteHeader(http.StatusMethodNotAllowed)
				return
			}
			status, member := answer(msg.Method)
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(status)
			fmt.Fprintf(w, `{"jsonrpc":"2.0","id":%s,%s}`, msg.ID, member)
		}))
		t.Cleanup(srv.Close)
		return srv
	}
	discover := `"result":{"supportedVersions":["2026-07-28"]}`
	closed := httptest.NewServer(http.NotFoundHandler())
	closedURL := closed.URL
	closed.Close()
	var pages atomic.Int32
	for name, c := range map[string]struct {
		url, want string
	}{
		"a listed port nobody listens on": {closedURL + "/mcp", ProbeErrUnreachable},
		"an unknown protocol revision": {rpc(func(m string) (int, string) {
			if m == "initialize" {
				return 200, `"result":{"protocolVersion":"2099-01-01","serverInfo":{"name":"x"}}`
			}
			return 400, `"error":{"code":-32600,"message":"no session"}`
		}).URL + "/mcp", ProbeErrUnsupportedVersion},
		"a tools/list error": {rpc(func(m string) (int, string) {
			if m == "server/discover" {
				return 200, discover
			}
			return 200, `"error":{"code":-32603,"message":"boom"}`
		}).URL + "/mcp", ProbeErrUpstream},
		"a cursor that loops": {rpc(func(m string) (int, string) {
			if m == "server/discover" {
				return 200, discover
			}
			pages.Add(1)
			return 200, `"result":{"tools":[{"name":"t"}],"nextCursor":"again"}`
		}).URL + "/mcp", ProbeErrUpstream},
	} {
		t.Run(name, func(t *testing.T) {
			s := probeServer(t, hostOf(t, c.url))
			_, raw, res := probe(t, s, probeBody(map[string]any{"org_id": "org-a", "url": c.url, "transport": "http"}))
			if res.OK || errorCode(res) != c.want {
				t.Fatalf("%s, want %s", raw, c.want)
			}
		})
	}
	if n := pages.Load(); n != 2 {
		t.Fatalf("the looping cursor was followed %d times, want 2 (the repeat is detected)", n)
	}
}

// structuredParams reports whether a JSON-RPC message's params member is
// absent or a structured value (an object or an array), as JSON-RPC 2.0
// section 4.2 requires; "params":null is neither.
func structuredParams(raw []byte) bool {
	var msg map[string]json.RawMessage
	if json.Unmarshal(raw, &msg) != nil {
		return false
	}
	p, present := msg["params"]
	if !present {
		return true
	}
	p = bytes.TrimSpace(p)
	return len(p) > 0 && (p[0] == '{' || p[0] == '[')
}

// strictRecorder keeps every JSON-RPC body a strict upstream received.
type strictRecorder struct {
	mu     sync.Mutex
	bodies [][]byte
}

func (s *strictRecorder) add(b []byte) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.bodies = append(s.bodies, b)
}

// toolsListParams is the params member of each tools/list the upstream
// received, in order ("" when the member was absent).
func (s *strictRecorder) toolsListParams(t *testing.T) []string {
	t.Helper()
	s.mu.Lock()
	defer s.mu.Unlock()
	var out []string
	for _, b := range s.bodies {
		if !structuredParams(b) {
			t.Errorf("the probe sent params that are not a structured value: %s", b)
		}
		var msg struct {
			Method string          `json:"method"`
			Params json.RawMessage `json:"params"`
		}
		if json.Unmarshal(b, &msg) == nil && msg.Method == "tools/list" {
			out = append(out, string(msg.Params))
		}
	}
	return out
}

// strictTools answers tools/list in two pages, one tool each.
func strictTools(cursor string) map[string]any {
	if cursor == "" {
		return map[string]any{"tools": []map[string]any{{"name": "a", "inputSchema": map[string]any{"type": "object"}}}, "nextCursor": "p2"}
	}
	return map[string]any{"tools": []map[string]any{{"name": "b", "inputSchema": map[string]any{"type": "object"}}}}
}

// strictMessage decodes one recorded body, or reports why a strict server
// refuses it (non-structured params: 400 -32600, as schema-validating
// servers answer "params":null).
func strictMessage(raw []byte) (id json.RawMessage, method, cursor string, ok bool) {
	var msg struct {
		ID     json.RawMessage `json:"id"`
		Method string          `json:"method"`
		Params struct {
			Cursor string `json:"cursor"`
		} `json:"params"`
	}
	if !structuredParams(raw) || json.Unmarshal(raw, &msg) != nil {
		return nil, "", "", false
	}
	return msg.ID, msg.Method, msg.Params.Cursor, true
}

const strictRefusal = `{"jsonrpc":"2.0","id":null,"error":{"code":-32600,"message":"Invalid Request: params must be an object or an array"}}`

// strictStreamable is a legacy Streamable HTTP server of version that
// refuses any message whose params is not a structured value.
func strictStreamable(t *testing.T, version string, rec *strictRecorder) *httptest.Server {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method == http.MethodDelete {
			return
		}
		raw, _ := io.ReadAll(r.Body)
		rec.add(raw)
		w.Header().Set("Content-Type", "application/json")
		id, method, cursor, ok := strictMessage(raw)
		if !ok {
			w.WriteHeader(http.StatusBadRequest)
			io.WriteString(w, strictRefusal)
			return
		}
		var result any
		switch method {
		case "initialize":
			w.Header().Set("Mcp-Session-Id", "s1")
			result = map[string]any{"protocolVersion": version, "capabilities": map[string]any{}, "serverInfo": map[string]string{"name": "strict-" + version}}
		case "notifications/initialized":
			w.WriteHeader(http.StatusAccepted)
			return
		case "tools/list":
			if r.Header.Get("Mcp-Session-Id") != "s1" {
				w.WriteHeader(http.StatusBadRequest)
				io.WriteString(w, `{"jsonrpc":"2.0","id":null,"error":{"code":-32600,"message":"no session"}}`)
				return
			}
			result = strictTools(cursor)
		default: // server/discover and anything else: a legacy server has no session for it
			w.WriteHeader(http.StatusBadRequest)
			io.WriteString(w, `{"jsonrpc":"2.0","id":null,"error":{"code":-32600,"message":"no session"}}`)
			return
		}
		_ = json.NewEncoder(w).Encode(map[string]any{"jsonrpc": "2.0", "id": id, "result": result})
	}))
	t.Cleanup(srv.Close)
	return srv
}

// strictHTTPSSE is a 2024-11-05 HTTP+SSE server (GET /sse, POST /messages)
// that refuses any message whose params is not a structured value.
func strictHTTPSSE(t *testing.T, rec *strictRecorder) *httptest.Server {
	t.Helper()
	answers := make(chan []byte, 16)
	mux := http.NewServeMux()
	mux.HandleFunc("GET /sse", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/event-stream")
		io.WriteString(w, "event: endpoint\ndata: /messages?sessionId=s1\n\n")
		w.(http.Flusher).Flush()
		for {
			select {
			case a := <-answers:
				fmt.Fprintf(w, "event: message\ndata: %s\n\n", a)
				w.(http.Flusher).Flush()
			case <-r.Context().Done():
				return
			}
		}
	})
	mux.HandleFunc("POST /", func(w http.ResponseWriter, r *http.Request) {
		raw, _ := io.ReadAll(r.Body)
		rec.add(raw)
		id, method, cursor, ok := strictMessage(raw)
		switch {
		case !ok:
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusBadRequest)
			io.WriteString(w, strictRefusal)
			return
		case r.URL.Path != "/messages" || r.URL.Query().Get("sessionId") != "s1":
			w.WriteHeader(http.StatusMethodNotAllowed)
			return
		}
		var result any
		switch method {
		case "initialize":
			result = map[string]any{"protocolVersion": "2024-11-05", "capabilities": map[string]any{}, "serverInfo": map[string]string{"name": "strict-2024-11-05"}}
		case "tools/list":
			result = strictTools(cursor)
		default: // notifications/initialized
			w.WriteHeader(http.StatusAccepted)
			return
		}
		a, _ := json.Marshal(map[string]any{"jsonrpc": "2.0", "id": id, "result": result})
		select {
		case answers <- a:
			w.WriteHeader(http.StatusAccepted)
		case <-r.Context().Done():
		}
	})
	srv := httptest.NewServer(mux)
	t.Cleanup(srv.Close)
	return srv
}

// TestProbeParamsStructuredOrOmitted: every message the probe sends carries
// params as an object or not at all (JSON-RPC 2.0 section 4.2), the way the
// gateway's own upstream client sends tools/list, so a strict server of
// each legacy era lists through the probe; the first tools/list page has no
// params member and the next one carries the cursor.
func TestProbeParamsStructuredOrOmitted(t *testing.T) {
	type era struct {
		name, path, want string
		start            func(*testing.T, *strictRecorder) *httptest.Server
	}
	eras := []era{{name: "2024-11-05 HTTP+SSE", path: "/sse", want: "2024-11-05", start: strictHTTPSSE}}
	for _, v := range []string{"2025-03-26", "2025-06-18", "2025-11-25"} {
		eras = append(eras, era{name: v + " Streamable HTTP", path: "/mcp", want: v,
			start: func(t *testing.T, rec *strictRecorder) *httptest.Server { return strictStreamable(t, v, rec) }})
	}
	for _, c := range eras {
		t.Run(c.name, func(t *testing.T) {
			rec := &strictRecorder{}
			srv := c.start(t, rec)
			s := probeServer(t, hostOf(t, srv.URL))
			_, raw, res := probe(t, s, probeBody(map[string]any{"org_id": "org-a", "url": srv.URL + c.path}))
			if !res.OK || res.Era != c.want || res.ToolCount == nil || *res.ToolCount != 2 {
				t.Errorf("a strict %s server: %s, want ok, era %s, 2 tools", c.name, raw, c.want)
			}
			got := rec.toolsListParams(t)
			if len(got) != 2 || got[0] != "" || got[1] != `{"cursor":"p2"}` {
				t.Errorf("tools/list params sent = %q, want [\"\" (no member), {\"cursor\":\"p2\"}]", got)
			}
		})
	}
}
