package capmock

import (
	"bytes"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"sync"
	"testing"
	"time"
)

type clock struct {
	mu sync.Mutex
	t  time.Time
}

func (c *clock) Now() time.Time {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.t
}

func (c *clock) Advance(d time.Duration) {
	c.mu.Lock()
	c.t = c.t.Add(d)
	c.mu.Unlock()
}

// start serves a mock on a test server, with a manual clock.
func start(t *testing.T) (*Mock, *httptest.Server, *clock) {
	t.Helper()
	m := New("http://placeholder")
	clk := &clock{t: time.Date(2026, 10, 17, 12, 0, 0, 0, time.UTC)}
	m.now = clk.Now
	srv := httptest.NewServer(m.Handler())
	t.Cleanup(srv.Close)
	m.SetBase(srv.URL)
	return m, srv, clk
}

type resp struct {
	status int
	header http.Header
	body   []byte
}

func (r resp) json(t *testing.T) map[string]any {
	t.Helper()
	var v map[string]any
	if err := json.Unmarshal(r.body, &v); err != nil {
		t.Fatalf("status %d body %q is not a JSON object: %v", r.status, r.body, err)
	}
	return v
}

// do sends a request; body may be nil, a string or a value to marshal.
func do(t *testing.T, method, url string, body any, headers ...string) resp {
	t.Helper()
	var rd io.Reader
	switch b := body.(type) {
	case nil:
	case string:
		rd = bytes.NewBufferString(b)
	default:
		raw, err := json.Marshal(b)
		if err != nil {
			t.Fatal(err)
		}
		rd = bytes.NewReader(raw)
	}
	req, err := http.NewRequest(method, url, rd)
	if err != nil {
		t.Fatal(err)
	}
	for i := 0; i+1 < len(headers); i += 2 {
		req.Header.Set(headers[i], headers[i+1])
	}
	res, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("%s %s: %v", method, url, err)
	}
	defer res.Body.Close()
	raw, _ := io.ReadAll(res.Body)
	return resp{res.StatusCode, res.Header, raw}
}

// path reads a dotted path of object keys and list indexes.
func path(t *testing.T, doc any, p string) any {
	t.Helper()
	cur := doc
	for _, k := range bytes.Split([]byte(p), []byte(".")) {
		switch v := cur.(type) {
		case map[string]any:
			cur = v[string(k)]
		case []any:
			var i int
			if err := json.Unmarshal(k, &i); err != nil || i >= len(v) {
				t.Fatalf("%s: no index %s", p, k)
			}
			cur = v[i]
		default:
			t.Fatalf("%s: %s is not inside an object or list", p, k)
		}
	}
	return cur
}
