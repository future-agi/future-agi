package contracttest

import (
	"bytes"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"sort"
	"strings"
	"sync"
	"testing"
)

// Replay is an upstream stand-in that answers every request with one case's
// raw body and records what the adapter sent.
type Replay struct {
	// URL is the server's base URL; configure the provider's BaseURL with it.
	URL string

	mu   sync.Mutex
	reqs []RecordedRequest
}

// RecordedRequest is one outbound request as the upstream saw it, in the
// form upstream_request.golden.json pins.
type RecordedRequest struct {
	Method string `json:"method"`
	Path   string `json:"path"`
	// Query is re-encoded in sorted order, credential parameters (key,
	// api_key, access_token) replaced with "REDACTED".
	Query string `json:"query,omitempty"`
	// Headers are lower-cased and comma-joined. Credentials are replaced with
	// "REDACTED" (so the golden still proves one was sent), per-request
	// signing dates with "VOLATILE", and transport headers the adapter does
	// not choose (user-agent, accept-encoding, content-length) are dropped.
	Headers map[string]string `json:"headers"`
	// Body is the decoded JSON body, or the raw string if it is not JSON.
	Body any `json:"body"`
}

// Header names whose values are credentials.
var redactedHeaders = map[string]bool{
	"authorization":        true,
	"proxy-authorization":  true,
	"x-api-key":            true,
	"api-key":              true,
	"x-goog-api-key":       true,
	"cookie":               true,
	"x-amz-security-token": true,
}

// Query parameter names whose values are credentials.
var redactedParams = map[string]bool{
	"key":          true,
	"api_key":      true,
	"access_token": true,
}

// Header names whose values change on every request.
var volatileHeaders = map[string]bool{
	"x-amz-date": true,
	"date":       true,
}

// Header names the HTTP client sets on its own.
var transportHeaders = map[string]bool{
	"user-agent":      true,
	"accept-encoding": true,
	"content-length":  true,
	"connection":      true,
}

// ReplayServer starts a server, closed when t ends, that answers every
// request with c.Body as a 200 with c.ContentType: application/json,
// text/event-stream or application/vnd.amazon.eventstream by response file.
func ReplayServer(t testing.TB, c Case) *Replay {
	t.Helper()
	r := &Replay{}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {
		body, err := io.ReadAll(req.Body)
		if err != nil {
			t.Errorf("contracttest: reading outbound request body: %v", err)
		}
		r.record(req, body)

		w.Header().Set("Content-Type", c.ContentType)
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write(c.Body)
		if f, ok := w.(http.Flusher); ok {
			f.Flush()
		}
	}))
	t.Cleanup(srv.Close)
	r.URL = srv.URL
	return r
}

func (r *Replay) record(req *http.Request, body []byte) {
	rec := RecordedRequest{
		Method:  req.Method,
		Path:    req.URL.Path,
		Query:   redactQuery(req.URL.Query()),
		Headers: map[string]string{},
	}
	names := make([]string, 0, len(req.Header))
	for name := range req.Header {
		names = append(names, name)
	}
	sort.Strings(names)
	for _, name := range names {
		lower := strings.ToLower(name)
		switch {
		case transportHeaders[lower]:
			continue
		case redactedHeaders[lower]:
			rec.Headers[lower] = "REDACTED"
		case volatileHeaders[lower]:
			rec.Headers[lower] = "VOLATILE"
		default:
			rec.Headers[lower] = strings.Join(req.Header.Values(name), ", ")
		}
	}

	dec := json.NewDecoder(bytes.NewReader(body))
	dec.UseNumber()
	var decoded any
	if err := dec.Decode(&decoded); err == nil {
		rec.Body = decoded
	} else {
		rec.Body = string(body)
	}

	r.mu.Lock()
	r.reqs = append(r.reqs, rec)
	r.mu.Unlock()
}

// redactQuery re-encodes q, sorted, with credential values replaced.
func redactQuery(q url.Values) string {
	for name, vals := range q {
		if redactedParams[strings.ToLower(name)] {
			for i := range vals {
				vals[i] = "REDACTED"
			}
		}
	}
	return q.Encode()
}

// Requests returns every request received so far.
func (r *Replay) Requests() []RecordedRequest {
	r.mu.Lock()
	defer r.mu.Unlock()
	return append([]RecordedRequest(nil), r.reqs...)
}

// Request returns the single request received, failing t unless exactly one
// arrived.
func (r *Replay) Request(t testing.TB) RecordedRequest {
	t.Helper()
	reqs := r.Requests()
	if len(reqs) != 1 {
		t.Fatalf("contracttest: upstream received %d requests, want exactly 1", len(reqs))
	}
	return reqs[0]
}
