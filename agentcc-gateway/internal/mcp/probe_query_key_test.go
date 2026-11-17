package mcp

import (
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"sync"
	"testing"

	"github.com/futureagi/agentcc-gateway/cmd/agentcc-mcpmock/mcpmock"
	gatewayadmin "github.com/futureagi/agentcc-gateway/internal/contracts/generated"
)

// A vendor that takes its API key as a query parameter of its MCP URL
// (Tavily's tavilyApiKey, Browserbase's browserbaseApiKey) is listed by the
// probe, when an organization adds it, with the key in that parameter, as
// the pool sends it on every later call. The key is synthetic, and holds
// characters a query must escape, so an unescaped or doubled parameter
// shows.
const (
	probeQueryName = "tavilyApiKey"
	probeQueryKey  = "test-query-key/7+=&?not-real"
)

// probeQueryLeaks reports whether s holds the key as written, query- or
// path-escaped, or inside a JSON string.
func probeQueryLeaks(s string) bool {
	j, _ := json.Marshal(probeQueryKey)
	for _, form := range []string{probeQueryKey, url.QueryEscape(probeQueryKey), url.PathEscape(probeQueryKey), string(j[1 : len(j)-1])} {
		if strings.Contains(s, form) {
			return true
		}
	}
	return false
}

// probeQueryGate is an upstream's front door: it records each request and
// passes on, without the key, only one that carries the key exactly once
// as the query parameter and nowhere else (no header, not the path).
type probeQueryGate struct {
	mu   sync.Mutex
	seen []probeGateRequest
}

// probeGateRequest is one request a gate saw.
type probeGateRequest struct {
	method, path string
	keys         []string // the values of probeQueryName in its query
	elsewhere    bool     // the key in a header or in the path
}

func (g *probeQueryGate) ok(r probeGateRequest) bool {
	return len(r.keys) == 1 && r.keys[0] == probeQueryKey && !r.elsewhere
}

func (g *probeQueryGate) wrap(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		q := r.URL.Query()
		seen := probeGateRequest{method: r.Method, path: r.URL.Path, keys: q[probeQueryName]}
		seen.elsewhere = probeQueryLeaks(r.URL.EscapedPath())
		for _, values := range r.Header {
			for _, v := range values {
				seen.elsewhere = seen.elsewhere || probeQueryLeaks(v)
			}
		}
		g.mu.Lock()
		g.seen = append(g.seen, seen)
		g.mu.Unlock()
		if !g.ok(seen) {
			http.Error(w, "missing or wrong key", http.StatusUnauthorized)
			return
		}
		q.Del(probeQueryName)
		r.URL.RawQuery = q.Encode()
		next.ServeHTTP(w, r)
	})
}

func (g *probeQueryGate) requests() []probeGateRequest {
	g.mu.Lock()
	defer g.mu.Unlock()
	return append([]probeGateRequest(nil), g.seen...)
}

// probeQueryMock is an agentcc-mcpmock of era with the common fixtures.
func probeQueryMock(t *testing.T, era string) *mcpmock.Mock {
	t.Helper()
	fix, err := mcpmock.LoadFixtures(mcpFixtures)
	if err != nil {
		t.Fatal(err)
	}
	e, err := mcpmock.ParseEra(era)
	if err != nil {
		t.Fatal(err)
	}
	m, err := mcpmock.New(mcpmock.Config{Era: e, Auth: "none", Path: "/mcp", ServerName: "probe-query-" + era}, fix)
	if err != nil {
		t.Fatal(err)
	}
	return m
}

// gatedProbeUpstream starts an agentcc-mcpmock of era on loopback behind
// gate.
func gatedProbeUpstream(t *testing.T, era string, gate *probeQueryGate) (*httptest.Server, *mcpmock.Mock) {
	t.Helper()
	m := probeQueryMock(t, era)
	srv := httptest.NewServer(gate.wrap(m.Handler()))
	t.Cleanup(srv.Close)
	m.SetPublicURL(srv.URL)
	return srv, m
}

// probeQueryBody is a probe of target with key as the query parameter, the
// auth Django sends for such a provider (its vault secret's push form).
func probeQueryBody(target, key string) string {
	return probeBody(map[string]any{"org_id": "org-a", "url": target,
		"auth": map[string]any{"type": "api_key", "in": "query", "name": probeQueryName, "value": key}})
}

// TestProbeQueryKeyOnEveryRequest: a query-parameter key reaches the
// upstream on every request the probe sends, in each era (the stateless
// discover; a legacy session's initialize, notification, tools/list pages
// and closing DELETE, answered as JSON or as SSE; HTTP+SSE's GET stream and
// message POSTs), URL-encoded, exactly once and in no header, and the
// listing is the one the key unlocks.
func TestProbeQueryKeyOnEveryRequest(t *testing.T) {
	for _, c := range []struct {
		era, path, want string
		behavior        mcpmock.Behavior
		methods         []string // HTTP methods the upstream must have seen
	}{
		{era: "2026", path: "/mcp", want: "2026-07-28", methods: []string{http.MethodPost}},
		{era: "2025-06-18", path: "/mcp", want: "2025-06-18", methods: []string{http.MethodPost, http.MethodDelete}},
		{era: "2025-06-18", path: "/mcp", want: "2025-06-18", behavior: mcpmock.Behavior{SSE: true, PageSize: 1},
			methods: []string{http.MethodPost, http.MethodDelete}},
		{era: "2024-11-05", path: "/sse", want: "2024-11-05", behavior: mcpmock.Behavior{PageSize: 1},
			methods: []string{http.MethodGet, http.MethodPost}},
	} {
		t.Run(fmt.Sprintf("%s sse=%v page=%d", c.era, c.behavior.SSE, c.behavior.PageSize), func(t *testing.T) {
			gate := &probeQueryGate{}
			srv, m := gatedProbeUpstream(t, c.era, gate)
			m.State().SetBehavior(c.behavior)
			s := probeServer(t, hostOf(t, srv.URL))
			code, raw, res := probe(t, s, probeQueryBody(srv.URL+c.path, probeQueryKey))
			if code != http.StatusOK || !res.OK || res.Era != c.want {
				t.Fatalf("status %d, body %s; want ok in era %s with the key in the query", code, raw, c.want)
			}
			if res.ToolCount == nil || *res.ToolCount != 3 || res.ToolsHash != commonToolsHash {
				t.Errorf("tool_count %v, tools_hash %s; want the fixture's 3 tools", res.ToolCount, res.ToolsHash)
			}
			if probeQueryLeaks(raw) {
				t.Fatalf("the probe's answer carries the key: %s", raw)
			}
			methods := map[string]bool{}
			for _, r := range gate.requests() {
				methods[r.method] = true
				if !gate.ok(r) {
					t.Errorf("%s %s reached the upstream with %d values of %s (want the key exactly once), elsewhere=%v",
						r.method, r.path, len(r.keys), probeQueryName, r.elsewhere)
				}
			}
			for _, method := range c.methods {
				if !methods[method] {
					t.Errorf("the upstream saw no %s; it saw %v", method, methods)
				}
			}
		})
	}
}

// TestProbeQueryKeyReplacesAnEchoedParameter: an HTTP+SSE upstream whose
// endpoint event names the message URL with the key's parameter in it (the
// key echoed, and a stale value) gets every message POST with the
// parameter exactly once, holding the key: the probe replaces it and never
// sends a second one.
func TestProbeQueryKeyReplacesAnEchoedParameter(t *testing.T) {
	gate := &probeQueryGate{}
	answers := make(chan []byte, 16)
	mux := http.NewServeMux()
	mux.HandleFunc("GET /sse", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/event-stream")
		fmt.Fprintf(w, "event: endpoint\ndata: /messages?sessionId=s1&%s=%s&%s=stale\n\n",
			probeQueryName, url.QueryEscape(probeQueryKey), probeQueryName)
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
	mux.HandleFunc("POST /messages", func(w http.ResponseWriter, r *http.Request) {
		raw, _ := io.ReadAll(r.Body)
		id, method, cursor, ok := strictMessage(raw)
		if !ok || r.URL.Query().Get("sessionId") != "s1" {
			w.WriteHeader(http.StatusBadRequest)
			return
		}
		var result any
		switch method {
		case "initialize":
			result = map[string]any{"protocolVersion": "2024-11-05", "capabilities": map[string]any{},
				"serverInfo": map[string]string{"name": "echoing-2024-11-05"}}
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
	srv := httptest.NewServer(gate.wrap(mux))
	t.Cleanup(srv.Close)
	s := probeServer(t, hostOf(t, srv.URL))
	_, raw, res := probe(t, s, probeQueryBody(srv.URL+"/sse", probeQueryKey))
	if !res.OK || res.Era != "2024-11-05" || res.ToolCount == nil || *res.ToolCount != 2 {
		t.Fatalf("an upstream that echoes the key in its endpoint: %s, want ok in 2024-11-05 with 2 tools", raw)
	}
	if probeQueryLeaks(raw) {
		t.Fatalf("the probe's answer carries the key: %s", raw)
	}
	posts := 0
	for _, r := range gate.requests() {
		if r.method != http.MethodPost || r.path != "/messages" {
			continue
		}
		posts++
		if !gate.ok(r) {
			t.Errorf("a message POST carried %d values of %s, want the key exactly once", len(r.keys), probeQueryName)
		}
	}
	if posts < 3 {
		t.Fatalf("the upstream got %d message POSTs, want initialize, its notification and tools/list", posts)
	}
}

// TestProbeQueryKeyStaysOnItsOrigin: a redirect within the URL's origin
// keeps the key on the next hop; a redirect to another origin drops it,
// even when the upstream echoes the key in the Location, so the other
// server never sees it.
func TestProbeQueryKeyStaysOnItsOrigin(t *testing.T) {
	t.Run("within the origin", func(t *testing.T) {
		gate := &probeQueryGate{}
		inner := probeQueryMock(t, "2025-06-18").Handler()
		srv := httptest.NewServer(gate.wrap(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			if r.URL.Path == "/old" {
				http.Redirect(w, r, "/mcp", http.StatusTemporaryRedirect)
				return
			}
			inner.ServeHTTP(w, r)
		})))
		t.Cleanup(srv.Close)
		s := probeServer(t, hostOf(t, srv.URL))
		_, raw, res := probe(t, s, probeQueryBody(srv.URL+"/old", probeQueryKey))
		if !res.OK || probeQueryLeaks(raw) {
			t.Fatalf("a redirect within the origin: %s, want ok without the key", raw)
		}
		paths := map[string]bool{}
		for _, r := range gate.requests() {
			paths[r.path] = true
			if !gate.ok(r) {
				t.Errorf("%s %s reached the upstream without the key exactly once", r.method, r.path)
			}
		}
		if !paths["/old"] || !paths["/mcp"] {
			t.Fatalf("the upstream saw %v, want the redirect and its target", paths)
		}
	})

	t.Run("to another origin", func(t *testing.T) {
		var mu sync.Mutex
		var reached []string
		otherMock := probeQueryMock(t, "2025-06-18").Handler()
		other := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			seen := r.Method + " " + r.RequestURI
			for name, values := range r.Header {
				seen += " " + name + "=" + strings.Join(values, ",")
			}
			mu.Lock()
			reached = append(reached, seen)
			mu.Unlock()
			otherMock.ServeHTTP(w, r)
		}))
		t.Cleanup(other.Close)
		gate := &probeQueryGate{}
		origin := httptest.NewServer(gate.wrap(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			// A careless vendor that hands the key back in the Location.
			target := other.URL + r.URL.Path + "?" + probeQueryName + "=" + url.QueryEscape(probeQueryKey) + "&next=1"
			http.Redirect(w, r, target, http.StatusTemporaryRedirect)
		})))
		t.Cleanup(origin.Close)
		s := probeServer(t, hostOf(t, origin.URL), hostOf(t, other.URL))
		_, raw, _ := probe(t, s, probeQueryBody(origin.URL+"/mcp", probeQueryKey))
		if probeQueryLeaks(raw) {
			t.Fatalf("the probe's answer carries the key: %s", raw)
		}
		mu.Lock()
		defer mu.Unlock()
		if len(gate.requests()) == 0 || len(reached) == 0 {
			t.Fatalf("the origin got %d requests and the other server %d; the check would pass vacuously",
				len(gate.requests()), len(reached))
		}
		for _, r := range reached {
			if probeQueryLeaks(r) {
				t.Fatalf("the other origin received the key: %s", r)
			}
		}
	})
}

// TestProbeQueryKeyNeverLogged: neither the gateway log (at debug), nor the
// probe's answer, nor the decoded request printed or encoded carries the
// key, whether the probe lists, the upstream refuses the key or the
// upstream cannot be reached; the printed request still names where the key
// goes.
func TestProbeQueryKeyNeverLogged(t *testing.T) {
	logs := &lockedBuffer{}
	old := slog.Default()
	slog.SetDefault(slog.New(slog.NewTextHandler(logs, &slog.HandlerOptions{Level: slog.LevelDebug})))
	defer slog.SetDefault(old)

	gate := &probeQueryGate{}
	srv, _ := gatedProbeUpstream(t, "2025-06-18", gate)
	closed := httptest.NewServer(http.NotFoundHandler())
	closedURL := closed.URL
	closed.Close()
	s := probeServer(t, hostOf(t, srv.URL), hostOf(t, closedURL))

	_, raw, res := probe(t, s, probeQueryBody(srv.URL+"/mcp", probeQueryKey))
	if !res.OK || probeQueryLeaks(raw) {
		t.Fatalf("the right key: %s, want ok without the key", raw)
	}
	const wrong = "test-wrong-query-key-0b12cd"
	_, raw, res = probe(t, s, probeQueryBody(srv.URL+"/mcp", wrong))
	if res.OK || errorCode(res) != ProbeErrAuthRequired || strings.Contains(raw, wrong) {
		t.Fatalf("a key the upstream refuses: %s, want auth_required without the key", raw)
	}
	_, raw, res = probe(t, s, probeQueryBody(closedURL+"/mcp", probeQueryKey))
	if res.OK || probeQueryLeaks(raw) {
		t.Fatalf("an upstream that cannot be reached: %s, want a failed probe without the key", raw)
	}

	req, err := DecodeProbeRequest(strings.NewReader(probeQueryBody("https://mcp.tavily.com/mcp/", probeQueryKey)))
	if err != nil {
		t.Fatal(err)
	}
	encoded, _ := json.Marshal(req)
	printed := fmt.Sprintf("%v %+v %#v %s %s", req, req, req, req.Auth, encoded)
	if probeQueryLeaks(printed) {
		t.Fatalf("printing or encoding the request shows the key: %s", printed)
	}
	if !strings.Contains(fmt.Sprint(req.Auth), "query") || !strings.Contains(string(encoded), probeQueryName) {
		t.Errorf("the printed request does not say where the key goes: %s", printed)
	}
	if !strings.Contains(logs.String(), "mcp probe") {
		t.Fatal("the probe logged nothing; the check would pass vacuously")
	}
	if probeQueryLeaks(logs.String()) || strings.Contains(logs.String(), wrong) {
		t.Fatalf("the gateway log carries a query key:\n%s", logs.String())
	}
}

// TestProbeQueryKeyRequestRefusals: a query-parameter auth is an api_key
// that names its parameter and no header, on a URL that does not set that
// parameter already; anything else is refused with 400 before any request
// is sent, and the refusal never quotes the value.
func TestProbeQueryKeyRequestRefusals(t *testing.T) {
	s := probeServer(t)
	const value = "test-refused-query-key-5e1f"
	const target = "https://mcp.example.com/mcp"
	for name, c := range map[string]struct {
		url  string
		auth map[string]any
	}{
		"a bearer":                 {target, map[string]any{"type": "bearer", "in": "query", "name": "k"}},
		"no name":                  {target, map[string]any{"type": "api_key", "in": "query"}},
		"a header as well":         {target, map[string]any{"type": "api_key", "in": "query", "name": "k", "header": "X-K"}},
		"a name no query takes":    {target, map[string]any{"type": "api_key", "in": "query", "name": "api key"}},
		"a parameter the URL sets": {target + "?k=1", map[string]any{"type": "api_key", "in": "query", "name": "k"}},
		"a URL variable":           {target, map[string]any{"type": "api_key", "in": "url_variable", "name": "k"}},
		"in header":                {target, map[string]any{"type": "api_key", "in": "header", "name": "X-K", "header": "X-K"}},
		"a name without in":        {target, map[string]any{"type": "api_key", "header": "X-K", "name": "X-K"}},
	} {
		c.auth["value"] = value
		code, raw, _ := probe(t, s, probeBody(map[string]any{"org_id": "org-a", "url": c.url, "auth": c.auth}))
		if code != http.StatusBadRequest || !strings.Contains(raw, "invalid_probe_request") || strings.Contains(raw, value) {
			t.Errorf("%s: status %d (%s), want 400 invalid_probe_request without the value", name, code, raw)
		}
	}
}

// TestProbeQueryKeyRequestMatchesContract: the probe request Django sends
// for a query-parameter key decodes strictly into the generated
// mcp-probe-v1 request type, keeping where the key goes, and the probe
// takes it.
func TestProbeQueryKeyRequestMatchesContract(t *testing.T) {
	body := probeBody(map[string]any{"org_id": "org-a", "url": "https://mcp.tavily.com/mcp/", "transport": "http",
		"auth": map[string]any{"type": "api_key", "in": "query", "name": probeQueryName, "value": probeQueryKey}})
	var contract gatewayadmin.MCPProbeRequest
	dec := json.NewDecoder(strings.NewReader(body))
	dec.DisallowUnknownFields()
	if err := dec.Decode(&contract); err != nil {
		t.Fatalf("a query-parameter probe request does not match MCPProbeRequest: %v", err)
	}
	again, err := json.Marshal(contract)
	if err != nil {
		t.Fatal(err)
	}
	var round struct {
		Auth map[string]any `json:"auth"`
	}
	if err := json.Unmarshal(again, &round); err != nil {
		t.Fatal(err)
	}
	if round.Auth["in"] != "query" || round.Auth["name"] != probeQueryName {
		t.Fatalf("the contract type dropped where the key goes: auth in=%v name=%v", round.Auth["in"], round.Auth["name"])
	}
	if _, err := DecodeProbeRequest(strings.NewReader(body)); err != nil {
		t.Fatalf("the probe refuses the contract's request: %v", err)
	}
}
