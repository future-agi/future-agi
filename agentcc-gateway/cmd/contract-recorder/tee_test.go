package main

import (
	"bufio"
	"compress/gzip"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"testing"
	"time"
)

// startTee puts a teeProxy in front of an httptest TLS server and returns an
// http.Client that, like an adapter configured by the recorder, dials the
// proxy for every connection, plus the plain-http base URL to call.
func startTee(t *testing.T, h http.Handler) (*teeProxy, *http.Client, string) {
	t.Helper()
	srv := httptest.NewTLSServer(h)
	t.Cleanup(srv.Close)
	target, err := url.Parse(srv.URL)
	if err != nil {
		t.Fatal(err)
	}
	p := newTeeProxy(target, srv.Client(), 10*time.Second)
	t.Cleanup(func() { _ = p.Close() })
	c := &http.Client{Transport: &http.Transport{DialContext: p.Dial}}
	t.Cleanup(c.CloseIdleConnections)
	return p, c, "http://" + target.Host
}

func TestTeeKeepsWholeStreamWhenAdapterStopsEarly(t *testing.T) {
	release := make(chan struct{})
	first := "event: message_start\ndata: {\"type\":\"message_start\"}\n\n"
	rest := "event: message_stop\ndata: {\"type\":\"message_stop\"}\n\n"
	p, c, base := startTee(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/event-stream")
		_, _ = io.WriteString(w, first)
		w.(http.Flusher).Flush()
		<-release // the client has read the first event and hung up
		_, _ = io.WriteString(w, rest)
	}))

	resp, err := c.Post(base+"/v1/messages", "application/json", strings.NewReader(`{}`))
	if err != nil {
		t.Fatal(err)
	}
	line, err := bufio.NewReader(resp.Body).ReadString('\n')
	if err != nil || line != "event: message_start\n" {
		t.Fatalf("first streamed line = %q, %v: the proxy must stream, not buffer", line, err)
	}
	_ = resp.Body.Close()
	c.CloseIdleConnections()
	close(release)

	ex := p.take()
	if len(ex) != 1 {
		t.Fatalf("exchanges = %d, want 1", len(ex))
	}
	if ex[0].Err != nil || ex[0].Status != 200 || ex[0].ContentType != "text/event-stream" {
		t.Errorf("exchange = %+v", ex[0])
	}
	if got := string(ex[0].Body); got != first+rest {
		t.Errorf("captured %q, want the whole stream %q", got, first+rest)
	}
	if len(p.take()) != 0 {
		t.Error("take must forget what it returned")
	}
}

func TestTeeForwardsRequestVerbatim(t *testing.T) {
	var gotURI, gotKey, gotConn, gotBody string
	p, c, base := startTee(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotURI, gotKey, gotConn = r.RequestURI, r.Header.Get("X-Api-Key"), r.Header.Get("Proxy-Authorization")
		b, _ := io.ReadAll(r.Body)
		gotBody = string(b)
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusTooManyRequests)
		_, _ = io.WriteString(w, `{"error":"slow down"}`)
	}))

	// An escaped ':' (Bedrock model IDs) must reach upstream as sent: SigV4
	// signs the escaped path.
	req, _ := http.NewRequest("POST", base+"/model/us.amazon.nova-2-lite-v1%3A0/invoke?b=2&a=1", strings.NewReader(`{"x":1}`))
	req.Header.Set("X-Api-Key", "sk-test-123456789")
	req.Header.Set("Proxy-Authorization", "hop-by-hop")
	resp, err := c.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	body, _ := io.ReadAll(resp.Body)
	_ = resp.Body.Close()

	if resp.StatusCode != http.StatusTooManyRequests || string(body) != `{"error":"slow down"}` {
		t.Errorf("adapter saw %d %s; the proxy must pass upstream's answer through", resp.StatusCode, body)
	}
	if gotURI != "/model/us.amazon.nova-2-lite-v1%3A0/invoke?b=2&a=1" {
		t.Errorf("upstream request URI = %q", gotURI)
	}
	if gotKey != "sk-test-123456789" || gotConn != "" || gotBody != `{"x":1}` {
		t.Errorf("upstream got key=%q proxy-authorization=%q body=%q", gotKey, gotConn, gotBody)
	}
	ex := p.take()
	if len(ex) != 1 || ex[0].Status != http.StatusTooManyRequests || ex[0].Err != nil {
		t.Errorf("exchange = %+v", ex)
	}
}

func TestTeeRefusesOtherHosts(t *testing.T) {
	called := false
	p, c, _ := startTee(t, http.HandlerFunc(func(http.ResponseWriter, *http.Request) { called = true }))
	resp, err := c.Get("http://evil.example/")
	if err != nil {
		t.Fatal(err)
	}
	_ = resp.Body.Close()
	ex := p.take()
	if called || resp.StatusCode != http.StatusBadGateway || len(ex) != 1 || ex[0].Err == nil {
		t.Errorf("called=%v status=%d exchanges=%+v; want a refusal", called, resp.StatusCode, ex)
	}
}

func TestTeeAppliesPatch(t *testing.T) {
	var got string
	p, c, base := startTee(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		b, _ := io.ReadAll(r.Body)
		got = string(b)
	}))
	p.setPatch([]byte(`{"thinking":{"type":"enabled","budget_tokens":1024},"temperature":null}`))
	resp, err := c.Post(base+"/v1/messages", "application/json", strings.NewReader(`{"model":"m","temperature":0.2,"max_tokens":2048}`))
	if err != nil {
		t.Fatal(err)
	}
	_ = resp.Body.Close()
	p.take()
	if want := `{"max_tokens":2048,"model":"m","thinking":{"budget_tokens":1024,"type":"enabled"}}`; got != want {
		t.Errorf("upstream body = %s, want %s", got, want)
	}
}

func TestTeeCapturesDecodedGzip(t *testing.T) {
	const body = `{"ok":true}`
	p, c, base := startTee(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !strings.Contains(r.Header.Get("Accept-Encoding"), "gzip") {
			_, _ = io.WriteString(w, body)
			return
		}
		w.Header().Set("Content-Encoding", "gzip")
		w.Header().Set("Content-Type", "application/json")
		zw := gzip.NewWriter(w)
		_, _ = io.WriteString(zw, body)
		_ = zw.Close()
	}))
	resp, err := c.Get(base + "/")
	if err != nil {
		t.Fatal(err)
	}
	got, _ := io.ReadAll(resp.Body)
	_ = resp.Body.Close()
	ex := p.take()
	if string(got) != body || len(ex) != 1 || string(ex[0].Body) != body {
		t.Errorf("adapter read %q, captured %+v; both want the decoded body", got, ex)
	}
}

func TestMergePatch(t *testing.T) {
	// RFC 7396 appendix A, a selection.
	cases := []struct{ doc, patch, want string }{
		{`{"a":"b"}`, `{"a":"c"}`, `{"a":"c"}`},
		{`{"a":"b"}`, `{"b":"c"}`, `{"a":"b","b":"c"}`},
		{`{"a":"b"}`, `{"a":null}`, `{}`},
		{`{"a":{"b":"c"}}`, `{"a":{"b":"d","c":null}}`, `{"a":{"b":"d"}}`},
		{`{"a":[{"b":"c"}]}`, `{"a":[1]}`, `{"a":[1]}`},
		{`{"e":null}`, `{"a":1}`, `{"a":1,"e":null}`},
		{`{"n":1.50}`, `{"m":2}`, `{"m":2,"n":1.50}`},
	}
	for _, tc := range cases {
		got, err := applyMergePatch([]byte(tc.doc), []byte(tc.patch))
		if err != nil || string(got) != tc.want {
			t.Errorf("patch %s with %s = %s, %v; want %s", tc.doc, tc.patch, got, err, tc.want)
		}
	}
	if _, err := applyMergePatch([]byte(`not json`), []byte(`{}`)); err == nil {
		t.Error("non-JSON body: want an error")
	}
}

func TestRedact(t *testing.T) {
	got, hit := redact([]byte(`{"echo":"sk-ant-secret-key","n":"short"}`), []string{"sk-ant-secret-key", "short", ""})
	if !hit || string(got) != `{"echo":"REDACTED","n":"short"}` {
		t.Errorf("redact = %s, %v", got, hit)
	}
	if _, hit := redact([]byte(`clean`), []string{"sk-ant-secret-key"}); hit {
		t.Error("nothing to redact, but hit")
	}
}
