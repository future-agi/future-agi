package common

import (
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestHeaderDefaultsPerCallPrecedence(t *testing.T) {
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		for name, want := range map[string]string{
			"X-Forwarded-Fixture": "per-call", "X-Empty-Fixture": "",
			"X-Config-Fixture": "config-default", "Authorization": "Bearer fixture",
			"Api-Key": "fixture", "X-Agentcc-Org-Id": "gateway-org",
		} {
			if r.Header.Get(name) != want {
				t.Errorf("header precedence changed for %s", name)
			}
		}
		w.WriteHeader(http.StatusNoContent)
	}))
	defer upstream.Close()
	headers, err := NewHeaderDefaults(map[string]string{
		"X-Forwarded-Fixture": "config-must-not-win", "X-Empty-Fixture": "config-must-not-win",
		"X-Config-Fixture": "config-default", "AUTHORIZATION": "config-must-not-win",
		"API-KEY": "config-must-not-win", "X-AGENTCC-ORG-ID": "config-must-not-win",
	})
	if err != nil {
		t.Fatal(err)
	}
	req, err := http.NewRequestWithContext(context.Background(), "GET", upstream.URL, nil)
	if err != nil {
		t.Fatal(err)
	}
	// Include noncanonical names and explicitly empty forwarded values.
	req.Header = http.Header{
		"x-forwarded-fixture": {"per-call"}, "x-empty-fixture": {""},
		"Authorization": {"Bearer fixture"}, "Api-Key": {"fixture"}, "X-Agentcc-Org-Id": {"gateway-org"},
	}
	headers.Apply(req)
	resp, err := upstream.Client().Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	_, _ = io.Copy(io.Discard, resp.Body)
}

func TestHeaderDefaultsValidationAndReservedNames(t *testing.T) {
	for _, name := range []string{"Host", "Content-Length", "Connection", "Proxy-Connection", "Keep-Alive", "Transfer-Encoding", "TE", "Trailer", "Upgrade", "x-omega-step"} {
		headers, err := NewHeaderDefaults(map[string]string{name: "fixture"})
		if err != nil {
			t.Fatal(err)
		}
		req := &http.Request{}
		headers.Apply(req)
		if len(req.Header) != 0 {
			t.Errorf("reserved header %s applied", name)
		}
	}
	// All RFC token punctuation is allowed; non-ASCII header values are valid.
	headers, err := NewHeaderDefaults(map[string]string{"X-!#$%&'*+-.^_`|~": "\tfixture café"})
	if err != nil {
		t.Fatal(err)
	}
	headers.Apply(&http.Request{})
	for _, value := range []string{"fixture\n", "fixture\r", "fixture\x01", "fixture\x7f"} {
		_, err := NewHeaderDefaults(map[string]string{"X-Fixture": value})
		if err == nil {
			t.Error("invalid value accepted")
		} else if strings.Contains(err.Error(), "fixture") {
			t.Error("header value exposed")
		}
	}
}
