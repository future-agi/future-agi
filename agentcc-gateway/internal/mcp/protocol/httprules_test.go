package protocol

import (
	"net/http"
	"testing"
)

func TestHTTPRules(t *testing.T) {
	acc := func(v string) http.Header {
		h := http.Header{}
		if v != "" {
			h.Set("Accept", v)
		}
		return h
	}
	for v, want := range map[string][3]bool{ // AcceptOK legacy, AcceptOK stateless, AcceptsSSE
		"":                                    {true, false, false},
		"application/json, text/event-stream": {true, true, true},
		"text/event-stream;q=0.5, application/json;q=1": {true, true, true},
		"*/*":              {true, true, true},
		"application/json": {false, false, false},
		"application/json, text/event-stream;q=0": {false, false, false},
		"text/*": {false, false, true},
	} {
		if got := [3]bool{AcceptOK(acc(v), false), AcceptOK(acc(v), true), AcceptsSSE(acc(v))}; got != want {
			t.Errorf("Accept %q = %v, want %v", v, got, want)
		}
	}
	ct := func(v string) http.Header { h := http.Header{}; h.Set("Content-Type", v); return h }
	if !ContentTypeOK(ct("Application/JSON; charset=utf-8")) || ContentTypeOK(ct("application/jsonx")) || ContentTypeOK(http.Header{}) {
		t.Error("Content-Type rule")
	}
	allowed := []string{"https://app.example"}
	own := []string{"gw.example"}
	for _, tc := range []struct {
		origin, host string
		want         bool
	}{
		{"", "gw.example", true},
		{"https://app.example", "gw.example", true},
		{"HTTPS://APP.EXAMPLE", "gw.example", true},
		{"https://evil.example", "gw.example", false},
		{"https://gw.example", "gw.example", true},    // the configured own host
		{"https://gw.example", "other.example", true}, // whatever Host says
		{"http://gw.example:8080", "gw.example:8080", false},
		{"http://127.0.0.1:8080", "127.0.0.1:8080", true}, // same host, an IP literal
		{"http://localhost:8080", "localhost:8080", true},
		{"http://[::1]:9000", "[::1]:9000", true},
		{"http://127.0.0.1:8081", "127.0.0.1:8080", false},
		{"http://rebind.example:8080", "rebind.example:8080", false}, // DNS rebinding
		{"null", "127.0.0.1", false},
		{"file:///x", "127.0.0.1", false},
	} {
		if got := OriginOK(tc.origin, tc.host, allowed, own); got != tc.want {
			t.Errorf("Origin %q at %q = %v, want %v", tc.origin, tc.host, got, tc.want)
		}
	}
	if !OriginOK("https://anything.example", "gw", []string{"*"}, nil) {
		t.Error("wildcard allow list")
	}
}
