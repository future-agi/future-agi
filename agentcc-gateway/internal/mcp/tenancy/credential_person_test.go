package tenancy

import (
	"errors"
	"net/http"
	"reflect"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/mcp"
)

// Identity is necessary but is not an upstream credential. Exercise the actual
// pool boundary used by callClient with a key issued through the sync transport.
func TestPersonKeyPerUserRequiresCredential(t *testing.T) {
	const person = "01234567-89ab-4cde-8fab-0123456789ab"
	for _, tc := range []struct {
		name, user, reason string
		header             http.Header
	}{
		{"person-without-override", person, "no_token", nil},
		{"person-with-empty-override", person, "no_token", http.Header{}},
		{"person-with-nil-authorization", person, "no_token", http.Header{"Authorization": nil}},
		{"person-with-zero-authorization-values", person, "no_token", http.Header{"Authorization": {}}},
		{"person-with-empty-first-authorization", person, "no_token", http.Header{"Authorization": {""}}},
		{"person-with-only-later-authorization", person, "no_token", http.Header{"Authorization": {"", "Bearer synthetic-person"}}},
		{"without-identity", "", "no_user_identity", nil},
		{"override-without-identity", "", "no_user_identity", http.Header{"Authorization": {"Bearer synthetic-person"}}},
		{"person-with-override", person, "", http.Header{"Authorization": {"Bearer synthetic-person"}}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			const raw = "person-key-credential-fixture"
			ks := auth.NewKeyStore(config.AuthConfig{})
			if n := ks.LoadFromHashes([]auth.SyncedKey{{ID: "person-key", KeyHash: auth.HashKey(raw),
				Metadata: map[string]string{"org_id": "org-a", "user_id": tc.user}}}); n != 1 {
				t.Fatal("fixture key did not load")
			}
			key := ks.Authenticate(raw)
			if key == nil {
				t.Fatal("fixture key did not authenticate")
			}
			id := ks.Identity(key)
			if id.UserID != tc.user || id.OrgID != "org-a" {
				t.Fatal("fixture did not resolve the reserved person identity")
			}

			r := newRig(t, Options{})
			u := r.factory.upstream(urlA, "search")
			dials := &countingDialer{}
			r.factory.dialer = dials
			// Deliberately omit credential.state: its default active state cannot
			// authorize a connection without the user's credential override.
			r.push(id.OrgID, 1, `{"enabled":true,"servers":{"mine":{"url":"`+urlA+`","credential":{"mode":"per_user"}}}}`)
			_, pool := r.resolve(id.OrgID)
			c, err := pool.Client("mine", id.UserID, mcp.AuthOverride{Header: tc.header})
			if tc.reason != "" {
				if c != nil {
					t.Error("missing identity or credential returned a client")
				}
				var denial *mcp.Denial
				if !errors.As(err, &denial) || denial.Code != "auth_required" || denial.Type != "auth_required" || !denial.InBand() {
					t.Errorf("want in-band auth_required, got %v", err)
				} else if !reflect.DeepEqual(denial.Metadata, map[string]any{"server_id": "mine", "reason": tc.reason}) {
					t.Error("refusal must carry only the server id and bounded, value-free reason")
				}
				if dials.n.Load() != 0 || r.factory.totalBuilds() != 0 || len(u.called()) != 0 {
					t.Errorf("missing credential reached upstream: dials=%d builds=%d calls=%d", dials.n.Load(), r.factory.totalBuilds(), len(u.called()))
				}
				return
			}
			if err != nil || c == nil {
				t.Fatalf("supplied credential refused: %v", err)
			}
			if err := credentialCall(c); err != nil {
				t.Fatal(err)
			}
			if dials.n.Load() != 1 || r.factory.totalBuilds() != 1 || len(u.called()) != 1 {
				t.Fatal("supplied credential must reach the mocked upstream exactly once")
			}
			u.mu.Lock()
			auths := append([]mcp.AuthConfig(nil), u.auths...)
			u.mu.Unlock()
			if len(auths) != 1 {
				t.Fatal("upstream did not receive exactly one supplied credential")
			}
			requireExactPersonCredential(t, c, auths[0], mcp.AuthConfig{Type: "bearer", Token: "synthetic-person"}, tc.header)
			// A cached user client is not permission to skip credential resolution
			// on a later call. Refusing it must not dial or replace the good client.
			_, err = pool.Client("mine", id.UserID, mcp.AuthOverride{})
			requireCredentialRefusal(t, err)
			if dials.n.Load() != 1 || r.factory.totalBuilds() != 1 || !c.Healthy() {
				t.Fatal("missing override reused or replaced a cached user credential")
			}
			requirePersonCredentialInvalidated(t, r, c, auths[0])
		})
	}
}

func TestPersonKeyPerUserEmptyCredentialAfterCachedClient(t *testing.T) {
	for _, tc := range []struct {
		name   string
		header http.Header
	}{
		{"nil-override", nil},
		{"empty-override", http.Header{}},
		{"nil-authorization", http.Header{"Authorization": nil}},
		{"zero-authorization-values", http.Header{"Authorization": {}}},
		{"empty-first-authorization", http.Header{"Authorization": {""}}},
		{"only-later-authorization", http.Header{"Authorization": {"", "Bearer synthetic-person"}}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			const person = "01234567-89ab-4cde-8fab-0123456789ab"
			r := newRig(t, Options{})
			u := r.factory.upstream(urlA, "search")
			dials := &countingDialer{}
			r.factory.dialer = dials
			r.push("org-a", 1, `{"enabled":true,"servers":{"mine":{"url":"`+urlA+`","credential":{"mode":"per_user"}}}}`)
			_, pool := r.resolve("org-a")
			valid := mcp.AuthOverride{Header: http.Header{"Authorization": {"Bearer synthetic-person"}}}
			cached, err := pool.Client("mine", person, valid)
			if err != nil || cached == nil {
				t.Fatalf("supplied credential refused: %v", err)
			}
			if err := credentialCall(cached); err != nil {
				t.Fatal(err)
			}
			if dials.n.Load() != 1 || r.factory.totalBuilds() != 1 || len(u.called()) != 1 {
				t.Fatal("supplied credential must reach the mocked upstream exactly once")
			}

			client, err := pool.Client("mine", person, mcp.AuthOverride{Header: tc.header})
			if client != nil {
				t.Error("empty credential returned a client after a valid credential was cached")
			}
			var denial *mcp.Denial
			if !errors.As(err, &denial) || denial.Code != "auth_required" || denial.Type != "auth_required" || !denial.InBand() {
				t.Errorf("want in-band auth_required, got %v", err)
			} else if !reflect.DeepEqual(denial.Metadata, map[string]any{"server_id": "mine", "reason": "no_token"}) {
				t.Error("refusal must carry only the server id and bounded, value-free reason")
			}
			if dials.n.Load() != 1 || r.factory.totalBuilds() != 1 || len(u.called()) != 1 || !cached.Healthy() {
				t.Errorf("empty credential touched cached client or upstream: dials=%d builds=%d calls=%d healthy=%t", dials.n.Load(), r.factory.totalBuilds(), len(u.called()), cached.Healthy())
			}

			// The refused request must leave the original credential's cache entry
			// intact, not just avoid making an unauthenticated upstream call.
			again, err := pool.Client("mine", person, valid)
			if err != nil || again == nil {
				t.Fatalf("original credential refused after empty override: %v", err)
			}
			if unwrap(again) != unwrap(cached) || dials.n.Load() != 1 || r.factory.totalBuilds() != 1 {
				t.Error("empty credential evicted or replaced the cached authenticated client")
			}
		})
	}
}

func TestPersonKeyPerUserAPIKeyOverride(t *testing.T) {
	for _, tc := range []struct {
		name   string
		header http.Header
	}{
		{"api-key", http.Header{"X-Api-Key": {"synthetic-person"}}},
		{"api-key-with-nil-authorization", http.Header{"Authorization": nil, "X-Api-Key": {"synthetic-person"}}},
		{"api-key-with-zero-authorization-values", http.Header{"Authorization": {}, "X-Api-Key": {"synthetic-person"}}},
		{"api-key-with-empty-authorization", http.Header{"Authorization": {""}, "X-Api-Key": {"synthetic-person"}}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			r := newRig(t, Options{})
			r.factory.upstream(urlA, "search")
			r.push("org-a", 1, `{"enabled":true,"servers":{"mine":{"url":"`+urlA+`","credential":{"mode":"per_user"}}}}`)
			_, pool := r.resolve("org-a")
			client, err := pool.Client("mine", "01234567-89ab-4cde-8fab-0123456789ab", mcp.AuthOverride{Header: tc.header})
			if err != nil || client == nil {
				t.Fatalf("supplied API key refused: %v", err)
			}
			got := personCredentialClient(t, client).cfg.Auth
			requireExactPersonCredential(t, client, got, mcp.AuthConfig{Type: "api_key", Header: "X-Api-Key", Key: "synthetic-person"}, tc.header)
			requirePersonCredentialInvalidated(t, r, client, got)
		})
	}
}
