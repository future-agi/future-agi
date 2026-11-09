package credtemplate_test

import (
	"context"
	"encoding/json"
	"net"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/cmd/agentcc-mcpmock/mcpmock"
	"github.com/futureagi/agentcc-gateway/internal/mcp"
	"github.com/futureagi/agentcc-gateway/internal/mcp/credtemplate"
	"github.com/futureagi/agentcc-gateway/internal/mcp/egress"
)

// mcpFixtures are the MCP mock's common tools (echo, get_weather,
// delete_file), resources and prompts.
const mcpFixtures = "../../../scripts/scenarios/fixtures/mcp/common"

// seen is one request as an upstream received it.
type seen struct {
	method   string
	path     string // the escaped path
	rawQuery string
	header   http.Header
}

// recorder keeps every request the handler it wraps receives.
type recorder struct {
	mu   sync.Mutex
	reqs []seen
}

func (r *recorder) wrap(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {
		r.mu.Lock()
		r.reqs = append(r.reqs, seen{req.Method, req.URL.EscapedPath(), req.URL.RawQuery, req.Header.Clone()})
		r.mu.Unlock()
		next.ServeHTTP(w, req)
	})
}

func (r *recorder) all() []seen {
	r.mu.Lock()
	defer r.mu.Unlock()
	return append([]seen(nil), r.reqs...)
}

// mcpMock is the MCP mock upstream (M4) for era, its Streamable HTTP
// endpoint at /mcp and, for 2024-11-05, its stream at /sse.
func mcpMock(t *testing.T, era string) *mcpmock.Mock {
	t.Helper()
	fix, err := mcpmock.LoadFixtures(mcpFixtures)
	if err != nil {
		t.Fatal(err)
	}
	e, err := mcpmock.ParseEra(era)
	if err != nil {
		t.Fatal(err)
	}
	m, err := mcpmock.New(mcpmock.Config{Era: e, Auth: "none", Path: "/mcp", ServerName: "cn7-" + era}, fix)
	if err != nil {
		t.Fatal(err)
	}
	return m
}

// serve starts h behind a recorder.
func serve(t *testing.T, h http.Handler) (*httptest.Server, *recorder) {
	t.Helper()
	rec := &recorder{}
	srv := httptest.NewServer(rec.wrap(h))
	t.Cleanup(srv.Close)
	return srv, rec
}

// queryKeyVendor stands for a vendor that takes its key in the query
// parameter name, as Browserbase takes browserbaseApiKey: a request without
// exactly one name=want answers 401; otherwise the parameter is removed and
// next serves the request.
func queryKeyVendor(name, want string, next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		q := r.URL.Query()
		if got := q[name]; len(got) != 1 || got[0] != want {
			http.Error(w, "missing or wrong key", http.StatusUnauthorized)
			return
		}
		q.Del(name)
		r.URL.RawQuery = q.Encode()
		next.ServeHTTP(w, r)
	})
}

// pathKeyVendor stands for a vendor whose endpoint URL holds the secret as
// one path segment, /mcp/<secret>: any other path answers 404; otherwise
// next serves the request at /mcp.
func pathKeyVendor(want string, next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.EscapedPath() != "/mcp/"+url.PathEscape(want) {
			http.NotFound(w, r)
			return
		}
		r.URL.Path, r.URL.RawPath = "/mcp", ""
		next.ServeHTTP(w, r)
	})
}

// guard is the SSRF guard (M17) with the loopback test servers allowed.
func guard(t *testing.T) *egress.Dialer {
	t.Helper()
	g, err := egress.New(egress.Options{PrivateAllow: []string{"127.0.0.1"}})
	if err != nil {
		t.Fatal(err)
	}
	return g
}

// templated builds a templated upstream's HTTP client the way the CW1 call
// site does (mcp.upstreamHTTPClient with mcp.AuthConfig.Template set;
// INTEGRATOR-REQUESTS.md IR-CN7-1): the egress guard's client, the
// template's header names added to the headers dropped on a cross-origin
// redirect, the guard transport's response-header timeout cleared (a tool
// may take minutes before its headers come), and the template wrapping
// that transport. As an mcp.Dialer with an HTTPClient method it is the
// client mcp's HTTP and SSE transports use, so the tests drive the real
// mcp.Client through it before the call site exists.
type templated struct {
	*egress.Dialer
	tpl *credtemplate.Template
}

func (d templated) HTTPClient(timeout time.Duration, sensitive ...string) *http.Client {
	c := d.Dialer.HTTPClient(timeout, append(sensitive, d.tpl.HeaderNames()...)...)
	if tr, ok := c.Transport.(*http.Transport); ok {
		tr.ResponseHeaderTimeout = 0
	}
	c.Transport = d.tpl.RoundTripper(c.Transport)
	return c
}

// connect connects a real mcp.Client to rawURL through dialer d.
func connect(t *testing.T, serverID, rawURL, transport string, d mcp.Dialer) *mcp.Client {
	t.Helper()
	c, err := tryConnect(serverID, rawURL, transport, d)
	if err != nil {
		t.Fatalf("connect %s: %v", serverID, err)
	}
	t.Cleanup(func() { c.Close() })
	return c
}

func tryConnect(serverID, rawURL, transport string, d mcp.Dialer) (*mcp.Client, error) {
	c := mcp.NewClient(mcp.ClientConfig{ServerID: serverID, URL: rawURL, TransportType: transport, Dialer: d})
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	if err := c.Connect(ctx); err != nil {
		c.Close()
		return nil, err
	}
	return c, nil
}

// echo calls the mock's echo tool and checks its answer.
func echo(t *testing.T, c *mcp.Client) {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	res, err := c.CallTool(ctx, "echo", map[string]interface{}{"text": "cn7-hello"})
	if err != nil {
		t.Fatalf("tools/call echo: %v", err)
	}
	if res.IsError || len(res.Content) == 0 || !strings.Contains(res.Content[0].Text, "cn7-hello") {
		t.Fatalf("tools/call echo answered %+v", res)
	}
}

// mustTemplate compiles a pushed secret for rawURL.
func mustTemplate(t *testing.T, rawURL string, sec credtemplate.Secret) *credtemplate.Template {
	t.Helper()
	tpl, err := credtemplate.ForSecret(rawURL, sec)
	if err != nil {
		t.Fatalf("ForSecret(%s in %q): %v", sec.Name, sec.In, err)
	}
	return tpl
}

// get sends one GET through rt and returns the response, closed.
func get(t *testing.T, rt http.RoundTripper, rawURL string) (*http.Request, *http.Response) {
	t.Helper()
	req, err := http.NewRequest(http.MethodGet, rawURL, nil)
	if err != nil {
		t.Fatal(err)
	}
	resp, err := (&http.Client{Transport: rt}).Do(req)
	if err != nil {
		t.Fatalf("GET: %v", err)
	}
	resp.Body.Close()
	return req, resp
}

// secretForms are the spellings of a secret a leak could take: as written,
// as a query value, as a path segment and inside a JSON string.
func secretForms(secret string) []string {
	j, _ := json.Marshal(secret)
	forms := []string{secret, url.QueryEscape(secret), url.PathEscape(secret), string(j[1 : len(j)-1])}
	out := forms[:0]
	for _, f := range forms {
		if f != "" && !contains(out, f) {
			out = append(out, f)
		}
	}
	return out
}

func contains(list []string, s string) bool {
	for _, x := range list {
		if x == s {
			return true
		}
	}
	return false
}

// leaks returns the forms of secret that text carries.
func leaks(text, secret string) []string {
	var out []string
	for _, f := range secretForms(secret) {
		if strings.Contains(text, f) {
			out = append(out, f)
		}
	}
	return out
}

// closedAddr is a loopback address nothing listens on.
func closedAddr(t *testing.T) string {
	t.Helper()
	l, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	addr := l.Addr().String()
	l.Close()
	return addr
}

// headerCarrying names the headers of h whose values contain s.
func headerCarrying(h http.Header, s string) []string {
	var out []string
	for k, vs := range h {
		for _, v := range vs {
			if strings.Contains(v, s) {
				out = append(out, k)
			}
		}
	}
	return out
}
