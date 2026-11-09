package credtemplate_test

import (
	"context"
	"errors"
	"fmt"
	"net"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"sync"
	"sync/atomic"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/mcp/credtemplate"
	"github.com/futureagi/agentcc-gateway/internal/mcp/egress"
)

// capture is an inner transport that keeps the request it is given and
// answers 200, or a redirect to location when it is set.
type capture struct {
	mu       sync.Mutex
	reqs     []*http.Request
	location string
}

func (c *capture) RoundTrip(r *http.Request) (*http.Response, error) {
	c.mu.Lock()
	c.reqs = append(c.reqs, r)
	c.mu.Unlock()
	resp := &http.Response{StatusCode: http.StatusOK, Header: http.Header{}, Body: http.NoBody, Request: r}
	if c.location != "" {
		resp.StatusCode = http.StatusFound
		resp.Header.Set("Location", c.location)
	}
	return resp, nil
}

func (c *capture) last() *http.Request {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.reqs[len(c.reqs)-1]
}

func roundTrip(t *testing.T, rt http.RoundTripper, method, raw string) (*http.Request, *http.Response) {
	t.Helper()
	req, err := http.NewRequest(method, raw, nil)
	if err != nil {
		t.Fatal(err)
	}
	resp, err := rt.RoundTrip(req)
	if err != nil {
		t.Fatalf("RoundTrip: %v", err)
	}
	return req, resp
}

// TestRoundTripperOriginScope: parts go only on requests to the origin the
// template was compiled for (scheme, host compared case-insensitively, and
// port, the default one spelled or not); any other request goes through
// unchanged, as the same *http.Request.
func TestRoundTripperOriginScope(t *testing.T) {
	inner := &capture{}
	tpl, err := credtemplate.Compile("https://Mcp.Example.com/mcp", credtemplate.Part{In: credtemplate.InQuery, Name: "k", Value: "v1"})
	if err != nil {
		t.Fatal(err)
	}
	rt := tpl.RoundTripper(inner)
	for _, raw := range []string{"https://mcp.example.com/mcp", "https://MCP.EXAMPLE.COM:443/other", "https://mcp.example.com:443/mcp?x=1"} {
		roundTrip(t, rt, http.MethodPost, raw)
		if got := inner.last().URL.Query().Get("k"); got != "v1" {
			t.Errorf("%s: the key was not placed", raw)
		}
	}
	for _, raw := range []string{"http://mcp.example.com/mcp", "https://mcp.example.com:8443/mcp", "https://evil.example.com/mcp", "https://mcp.example.com.evil.example/mcp"} {
		req, _ := roundTrip(t, rt, http.MethodPost, raw)
		if inner.last() != req || req.URL.RawQuery != "" && strings.Contains(req.URL.RawQuery, "k=") {
			t.Errorf("%s: another origin's request was changed", raw)
		}
	}
}

// TestRoundTripperDoesNotTouchTheCallersRequest: the caller's request keeps
// its URL and headers; the inner transport gets a copy with the parts, and
// the response names the caller's request.
func TestRoundTripperDoesNotTouchTheCallersRequest(t *testing.T) {
	inner := &capture{}
	tpl, err := credtemplate.Compile("https://h.example.com/m/{t}",
		credtemplate.Part{In: credtemplate.InURLVariable, Name: "t", Value: "s1"},
		credtemplate.Part{In: credtemplate.InHeader, Name: "X-Key", Value: "s2"},
		credtemplate.Part{In: credtemplate.InQuery, Name: "q", Value: "s3"})
	if err != nil {
		t.Fatal(err)
	}
	req, resp := roundTrip(t, tpl.RoundTripper(inner), http.MethodGet, "https://h.example.com/m/{t}?a=1")
	sent := inner.last()
	if sent == req {
		t.Fatal("the inner transport got the caller's request itself")
	}
	if sent.URL.EscapedPath() != "/m/s1" || sent.URL.RawQuery != "a=1&q=s3" || sent.Header.Get("X-Key") != "s2" {
		t.Fatalf("the copy was not templated: %s?%s", sent.URL.EscapedPath(), sent.URL.RawQuery)
	}
	if req.URL.String() != "https://h.example.com/m/%7Bt%7D?a=1" || req.Header.Get("X-Key") != "" {
		t.Fatalf("the caller's request changed: %s", req.URL.String())
	}
	if resp.Request != req {
		t.Fatal("resp.Request is not the caller's request")
	}
}

// TestRoundTripperRewritesAnEchoingLocation: a redirect whose Location
// echoes a placed secret names the template form instead (the query part
// dropped, the variable's placeholder restored); the client then follows
// it and the template places the secret on the next hop again.
func TestRoundTripperRewritesAnEchoingLocation(t *testing.T) {
	tpl, err := credtemplate.Compile("https://h.example.com/m/{t}",
		credtemplate.Part{In: credtemplate.InURLVariable, Name: "t", Value: "s/1"},
		credtemplate.Part{In: credtemplate.InQuery, Name: "q", Value: "s 3"})
	if err != nil {
		t.Fatal(err)
	}
	for _, tc := range []struct{ location, want string }{
		{"/m/s%2F1/next?q=s+3&x=1", "/m/{t}/next?x=1"},
		{"https://h.example.com/m/s%2F1?q=s+3", "https://h.example.com/m/{t}"},
		{"/plain?y=2", "/plain?y=2"},
	} {
		inner := &capture{location: tc.location}
		_, resp := roundTrip(t, tpl.RoundTripper(inner), http.MethodGet, "https://h.example.com/m/{t}")
		if got := resp.Header.Get("Location"); got != tc.want {
			t.Errorf("Location %s became %s, want %s", tc.location, got, tc.want)
		}
	}
}

// TestRoundTripperKeepsInnerErrorsClassified: an inner error that carries
// a placed secret is scrubbed but still unwraps to its cause, and a
// timeout stays a timeout to http.Client's *url.Error.
func TestRoundTripperKeepsInnerErrorsClassified(t *testing.T) {
	tpl, err := credtemplate.Compile("https://h.example.com/mcp", credtemplate.Part{In: credtemplate.InQuery, Name: "k", Value: "s3cr3t-v"})
	if err != nil {
		t.Fatal(err)
	}
	timeout := &net.OpError{Op: "dial", Net: "tcp", Err: timeoutErr{}}
	inner := roundTripFunc(func(r *http.Request) (*http.Response, error) {
		return nil, fmt.Errorf("to %s: %w", r.URL.RawQuery, timeout)
	})
	_, err = (&http.Client{Transport: tpl.RoundTripper(inner)}).Get("https://h.example.com/mcp")
	var ue *url.Error
	if !errors.As(err, &ue) || !ue.Timeout() {
		t.Fatalf("a timeout is no longer one: %v", err)
	}
	if strings.Contains(err.Error(), "s3cr3t-v") || !errors.Is(err, timeout) {
		t.Fatalf("want the error scrubbed and still wrapping its cause: %v", err)
	}
	// An error that carries no secret is returned as it is.
	plain := errors.New("connection reset")
	_, err = tpl.RoundTripper(roundTripFunc(func(*http.Request) (*http.Response, error) { return nil, plain })).
		RoundTrip(httptest.NewRequest(http.MethodGet, "https://h.example.com/mcp", nil).WithContext(context.Background()))
	if err != plain {
		t.Fatalf("an error without a secret was replaced: %v", err)
	}
}

type timeoutErr struct{}

func (timeoutErr) Error() string   { return "i/o timeout" }
func (timeoutErr) Timeout() bool   { return true }
func (timeoutErr) Temporary() bool { return true }

// TestRoundTripperClosesIdleConnections: http.Client.CloseIdleConnections
// (HTTPTransport.Close) reaches the inner transport through the template.
func TestRoundTripperClosesIdleConnections(t *testing.T) {
	inner := &idleCounter{}
	tpl, err := credtemplate.Compile("https://h.example.com/mcp", credtemplate.Part{In: credtemplate.InQuery, Name: "k", Value: "v"})
	if err != nil {
		t.Fatal(err)
	}
	(&http.Client{Transport: tpl.RoundTripper(inner)}).CloseIdleConnections()
	if inner.closed.Load() != 1 {
		t.Fatalf("CloseIdleConnections reached the inner transport %d times, want 1", inner.closed.Load())
	}
}

type idleCounter struct {
	capture
	closed atomic.Int32
}

func (c *idleCounter) CloseIdleConnections() { c.closed.Add(1) }

// TestRoundTripperComposesWithTheEgressGuard: wrapped by the template, the
// guard still refuses a private address it does not allow, with its own
// error.
func TestRoundTripperComposesWithTheEgressGuard(t *testing.T) {
	strict, err := egress.New(egress.Options{})
	if err != nil {
		t.Fatal(err)
	}
	srv, rec := serve(t, http.HandlerFunc(func(http.ResponseWriter, *http.Request) {}))
	tpl, err := credtemplate.Compile(srv.URL+"/mcp", credtemplate.Part{In: credtemplate.InQuery, Name: "k", Value: "guarded-v"})
	if err != nil {
		t.Fatal(err)
	}
	_, err = templated{strict, tpl}.HTTPClient(0).Get(srv.URL + "/mcp")
	var refused *egress.RefusedError
	if !errors.As(err, &refused) {
		t.Fatalf("want the guard's refusal, got %v", err)
	}
	if strings.Contains(err.Error(), "guarded-v") || len(rec.all()) != 0 {
		t.Fatal("a refused request leaked the key or reached the server")
	}
}

// TestRoundTripperConcurrentUse: one template serves many requests at once
// (go test -race), each getting its own copy with the parts.
func TestRoundTripperConcurrentUse(t *testing.T) {
	srv, rec := serve(t, http.HandlerFunc(func(http.ResponseWriter, *http.Request) {}))
	tpl, err := credtemplate.Compile(srv.URL+"/m/{t}",
		credtemplate.Part{In: credtemplate.InURLVariable, Name: "t", Value: "seg"},
		credtemplate.Part{In: credtemplate.InQuery, Name: "k", Value: "v"})
	if err != nil {
		t.Fatal(err)
	}
	c := &http.Client{Transport: tpl.RoundTripper(http.DefaultTransport)}
	var wg sync.WaitGroup
	for i := 0; i < 32; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			resp, err := c.Get(fmt.Sprintf("%s/m/{t}?i=%d", srv.URL, i))
			if err != nil {
				t.Error(err)
				return
			}
			resp.Body.Close()
		}(i)
	}
	wg.Wait()
	reqs := rec.all()
	if len(reqs) != 32 {
		t.Fatalf("%d requests arrived, want 32", len(reqs))
	}
	for _, r := range reqs {
		if r.path != "/m/seg" || !strings.HasSuffix(r.rawQuery, "&k=v") {
			t.Fatalf("a concurrent request arrived as %s?%s", r.path, r.rawQuery)
		}
	}
}

// TestScrubRemovesEverySpelling: Scrub removes each secret part's value as
// written, query-escaped, path-escaped and JSON-escaped, longest first.
func TestScrubRemovesEverySpelling(t *testing.T) {
	const v = `a/b c+"d"`
	tpl, err := credtemplate.Compile("https://h.example.com/mcp", credtemplate.Part{In: credtemplate.InHeader, Name: "X-Key", Value: v})
	if err != nil {
		t.Fatal(err)
	}
	for _, form := range secretForms(v) {
		if got := tpl.Scrub("x " + form + " y"); got != "x [redacted] y" {
			t.Errorf("Scrub left a spelling: %q", got)
		}
	}
	if got := tpl.Scrub("nothing here"); got != "nothing here" {
		t.Errorf("Scrub changed text with no secret: %q", got)
	}
}
