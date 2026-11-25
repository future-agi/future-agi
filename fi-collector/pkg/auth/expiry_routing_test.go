package auth

import (
	"context"
	"errors"
	"fmt"
	"testing"
	"time"

	"github.com/google/uuid"
	"google.golang.org/grpc/metadata"
)

// Both query shapes must return the same expiry verdict. Simulation routing
// selects the wider query only after its schema check has completed.
func TestValidateKeyExpiryAcrossRoutingQueries(t *testing.T) {
	pool := routingDatabase(t)
	f := routingFixture{t, pool}
	org := uuid.NewString()
	ws := f.workspace(org, true, true, false)
	future := time.Now().Add(time.Hour)
	past := time.Now().Add(-time.Hour)
	for _, state := range []columnState{columnsUnknown, columnsPresent, columnsMissing} {
		for _, tc := range []struct {
			name    string
			expiry  *time.Time
			enabled bool
			expired bool
		}{
			{"no-expiry", nil, true, false},
			{"future", &future, true, false},
			{"expired", &past, true, true},
			{"auto-disabled-expired", &past, false, true},
			{"revoked", &future, false, false},
		} {
			t.Run(fmt.Sprintf("%d/%s", state, tc.name), func(t *testing.T) {
				api, secret := f.key("ingest", org, &ws, nil)
				f.exec(`UPDATE accounts_orgapikey SET expires_at=$1, enabled=$2 WHERE api_key=$3`, tc.expiry, tc.enabled, api)
				result, err := routingResolver(pool, state).ValidateKey(context.Background(), api, secret)
				if tc.expired {
					if result != nil || !errors.Is(err, ErrKeyExpired) {
						t.Fatalf("expired key: result=%v err=%v", result, err)
					}
					return
				}
				if err != nil || (result != nil) != tc.enabled {
					t.Fatalf("result=%v err=%v enabled=%v", result, err, tc.enabled)
				}
				if result != nil && ((result.ExpiresAt == nil) != (tc.expiry == nil)) {
					t.Fatal("cache expiry does not match persisted expiry")
				}
			})
		}
	}
}

func TestKnownGRPCCredentialsRefusesExpiredCache(t *testing.T) {
	a := newExpiryTestAuthenticator("synthetic-api", "synthetic-secret", timePtr(time.Now().Add(-time.Second)))
	md := metadata.New(map[string]string{"x-api-key": "synthetic-api", "x-secret-key": "synthetic-secret"})
	if a.KnownGRPCCredentials(md) {
		t.Fatal("gRPC preflight must not treat an expired cached key as authenticated")
	}
}

func TestAuthenticateRejectsExpiredResolverResult(t *testing.T) {
	a := newExpiryTestAuthenticator("unused", "unused", nil)
	a.keys = &fakeKeyValidator{result: &ResolveResult{ExpiresAt: timePtr(time.Now().Add(-time.Second))}}
	result, err := a.Authenticate(context.Background(), "uncached-api", "uncached-secret")
	if result != nil || !errors.Is(err, ErrKeyExpired) {
		t.Fatalf("expired resolver result: result=%v err=%v", result, err)
	}
}
