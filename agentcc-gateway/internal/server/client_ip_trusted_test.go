package server

import (
	"net/http/httptest"
	"testing"
)

func TestTrustedClientIP(t *testing.T) {
	for _, tc := range []struct {
		name   string
		peer   string
		xff    []string
		hops   int
		expect string
	}{
		{"no trust uses peer", "192.0.2.1:5000", []string{"10.0.0.2"}, 0, "192.0.2.1"},
		{"negative trust uses peer", "192.0.2.1:5000", []string{"10.0.0.2"}, -1, "192.0.2.1"},
		{"peer without port", "192.0.2.1", nil, 0, "192.0.2.1"},
		{"ipv6 peer", "[2001:db8::7]:443", nil, 1, "2001:db8::7"},
		{"one hop takes rightmost", "10.200.0.1:1", []string{"203.0.113.9"}, 1, "203.0.113.9"},
		{"one hop ignores caller prefix", "10.200.0.1:1", []string{"10.0.0.2, 203.0.113.9"}, 1, "203.0.113.9"},
		{"gcp lb two hops", "35.191.0.10:1", []string{"10.0.0.2, 203.0.113.9, 34.120.0.1"}, 2, "203.0.113.9"},
		{"header lines are one list", "35.191.0.10:1", []string{"10.0.0.2", "203.0.113.9, 34.120.0.1"}, 2, "203.0.113.9"},
		{"absent header uses peer", "192.0.2.1:1", nil, 1, "192.0.2.1"},
		{"empty header uses peer", "192.0.2.1:1", []string{" , "}, 1, "192.0.2.1"},
		{"short chain uses peer", "192.0.2.1:1", []string{"10.0.0.2"}, 2, "192.0.2.1"},
		{"entry with port", "10.200.0.1:1", []string{"203.0.113.9:8443"}, 1, "203.0.113.9"},
		{"bracketed ipv6 entry", "10.200.0.1:1", []string{"[2001:db8::1]"}, 1, "2001:db8::1"},
		{"bracketed ipv6 entry with port", "10.200.0.1:1", []string{"[2001:db8::1]:443"}, 1, "2001:db8::1"},
		{"bare ipv6 entry", "10.200.0.1:1", []string{"2001:db8::1"}, 1, "2001:db8::1"},
		{"unparseable entry refused", "10.200.0.1:1", []string{"unknown"}, 1, ""},
	} {
		t.Run(tc.name, func(t *testing.T) {
			r := httptest.NewRequest("GET", "/v1/videos", nil)
			r.RemoteAddr = tc.peer
			for _, v := range tc.xff {
				r.Header.Add("X-Forwarded-For", v)
			}
			if got := trustedClientIP(r, tc.hops); got != tc.expect {
				t.Fatalf("got %q want %q", got, tc.expect)
			}
		})
	}
}
