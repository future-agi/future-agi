package server

// An internal key that locks its request to one provider
// (x-agentcc-provider-lock) is served only a model that provider lists, that
// the gateway's catalogue has for it, or that model_map sends to it, as the
// same key is without the lock. Any other model answers 404 model_not_found
// and nothing is called. The lock still picks the provider.
//
// Same gateway as internal_key_known_models_test.go: "openai" lists m-listed;
// "router" lists anthropic/claude-literal and its catalogue has vendor/known;
// model_map sends mapped-alias to openai.

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func ikmPostLocked(srv *Server, lock, body string) *httptest.ResponseRecorder {
	req := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+ikmInternalKey)
	req.Header.Set("x-agentcc-provider-lock", lock)
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)
	return w
}

func TestInternalKeyProviderLockServesOnlyModelsTheLockedProviderKnows(t *testing.T) {
	for _, tc := range []struct {
		name, lock, model string
		served            bool
	}{
		{"a model the locked provider lists", "openai", "m-listed", true},
		{"the same model named with the locked provider's prefix", "openai", "openai/m-listed", true},
		{"a model the locked provider does not list", "openai", "m-not-listed", false},
		{"a model only another provider lists", "router", "m-listed", false},
		{"a model in the locked provider's catalogue", "router", "vendor/known", true},
		{"a model not in the locked provider's catalogue", "router", "vendor/not-known", false},
		{"a literal prefixed id the locked provider lists", "router", "anthropic/claude-literal", true},
		{"a model_map entry for the locked provider", "openai", "mapped-alias", true},
		{"a model_map entry for another provider", "router", "mapped-alias", false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			up := newIKMUpstream(t)
			srv := ikmGateway(t, up, false)
			w := ikmPostLocked(srv, tc.lock, ikmChat(tc.model, ""))
			if tc.served {
				ikmWantServed(t, w, up)
				if got := w.Header().Get("x-agentcc-provider-locked"); got != tc.lock {
					t.Errorf("x-agentcc-provider-locked = %q, want %q", got, tc.lock)
				}
			} else {
				ikmWantNotFound(t, w, up)
			}
		})
	}
}

// The only configured provider, locked: the same rule.
func TestInternalKeyProviderLockSoleProviderServesOnlyKnownModels(t *testing.T) {
	for _, tc := range []struct {
		model  string
		served bool
	}{
		{"solo-listed", true},
		{"not-listed-anywhere", false},
	} {
		t.Run(tc.model, func(t *testing.T) {
			up := newIKMUpstream(t)
			srv := ikmGateway(t, up, true)
			w := ikmPostLocked(srv, "solo", ikmChat(tc.model, ""))
			if tc.served {
				ikmWantServed(t, w, up)
			} else {
				ikmWantNotFound(t, w, up)
			}
		})
	}
}

// A lock to a provider the gateway does not have is refused as before.
func TestInternalKeyProviderLockToAnUnknownProviderIsNotFound(t *testing.T) {
	up := newIKMUpstream(t)
	srv := ikmGateway(t, up, false)
	w := ikmPostLocked(srv, "nowhere", ikmChat("m-listed", ""))
	if w.Code != http.StatusNotFound || r7Code(w) != "provider_not_found" {
		t.Fatalf("-> %d %s %s, want 404 provider_not_found", w.Code, r7Code(w), w.Body.String())
	}
	if n := up.count(); n != 0 {
		t.Fatalf("upstream calls = %d, want none", n)
	}
}
