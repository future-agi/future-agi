package main

import (
	"io"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"testing"
)

// The extension hook is wired in: a directive that neither the mock nor
// package ext knows fails loudly with 400 unknown_mock_directive, and nothing
// is charged. The extension directives (stream_tools and the rest) are tested in ext.
func TestExtStubRejectsUnknownDirective(t *testing.T) {
	srv, err := newServer(filepath.Join("..", "..", "scripts", "scenarios", "fixtures", "common"))
	if err != nil {
		t.Fatal(err)
	}
	ts := httptest.NewServer(srv)
	defer ts.Close()

	for _, path := range []string{"/v1/chat/completions", "/v1/messages", "/v1/responses"} {
		req, _ := http.NewRequest("POST", ts.URL+path, strings.NewReader(`{"model":"anthropic/claude-sonnet-5.5","messages":[]}`))
		req.Header.Set("x-mock-behavior", "tools,no_such_directive:2")
		resp, err := http.DefaultClient.Do(req)
		if err != nil {
			t.Fatal(err)
		}
		body, _ := io.ReadAll(resp.Body)
		resp.Body.Close()
		if resp.StatusCode != http.StatusBadRequest || !strings.Contains(string(body), `"code":"unknown_mock_directive"`) ||
			!strings.Contains(string(body), "no_such_directive") {
			t.Fatalf("%s: status %d body %s", path, resp.StatusCode, body)
		}
	}
	if total, n := srv.State().TotalChargeMicros(); n != 0 || total != 0 {
		t.Fatalf("refused calls were charged: %d micros over %d", total, n)
	}
}

func TestNewServerRejectsAMissingFixturesDir(t *testing.T) {
	if _, err := newServer(filepath.Join("no", "such", "dir")); err == nil {
		t.Fatal("a mistyped fixtures dir must fail, not fall back to an empty catalog")
	}
}
