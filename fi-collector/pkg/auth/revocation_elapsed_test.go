package auth

import (
	"context"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/alicebob/miniredis/v2"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/redis/go-redis/v9"
)

// Exercise wall-clock expiry, not a rewritten cache timestamp. Independent
// PostgreSQL databases model a primary and a read pool which has not replayed
// the mutation. The stale read pool must still accept the credential at the end.
func TestEligibilityElapsedRevocationBound(t *testing.T) {
	cases := []struct{ kind, name, sql string }{
		{"personal", "key", `UPDATE accounts_orgapikey SET enabled=false`},
		{"personal", "membership", `UPDATE accounts_organization_membership SET is_active=false`},
		{"personal", "workspace-membership", `UPDATE accounts_workspacemembership SET deleted=true`},
		{"sandbox", "grant", `UPDATE accounts_service_grant SET revoked_at=now()`},
		{"sandbox", "membership", `UPDATE accounts_organization_membership SET is_active=false`},
		{"sandbox", "workspace-membership", `UPDATE accounts_workspacemembership SET deleted=true`},
	}
	for _, tc := range cases {
		for _, mode := range []string{"delivered", "missed", "absent", "reconnect"} {
			t.Run(tc.kind+"/"+tc.name+"/"+mode, func(t *testing.T) {
				t.Parallel()
				primary, replica := routingDatabase(t), routingDatabase(t)
				f := newEligibilityFixture()
				api, secret := f.api, f.secret
				var claims *GrantClaims
				if tc.kind == "sandbox" {
					fields := sandboxFields(time.Now())
					api, secret = "fit_"+fields[9], signedGrantFields(fields, goldenPepper)
					var err error
					claims, err = VerifyCollectorGrant(api, secret, mustPeppers(t, goldenPepper), []string{"k1"}, time.Now())
					if err != nil {
						t.Fatal(err)
					}
					f = eligibilityFixture{claims.Org, claims.Workspace, claims.User, "fixture-key", "fixture-secret"}
				}
				for _, pool := range []*pgxpool.Pool{primary, replica} {
					seedEligibility(t, pool, f)
					if claims != nil {
						seedGrant(t, pool, claims)
					}
				}
				var mr *miniredis.Miniredis
				var rdb *redis.Client
				cfg := Config{PGWrite: primary.Config().ConnString(), PGRead: replica.Config().ConnString(), Peppers: mustPeppers(t, goldenPepper)}
				if mode != "absent" {
					mr = miniredis.RunT(t)
					rdb = redis.NewClient(&redis.Options{Addr: mr.Addr()})
					defer rdb.Close()
					cfg.RedisAddr = mr.Addr()
				}
				a, err := New(context.Background(), cfg, rdb, slog.Default())
				if err != nil {
					t.Fatal(err)
				}
				defer a.Close()
				if rdb != nil {
					startFreshnessWatcher(t, a)
				}
				handler := a.HTTPMiddleware(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(http.StatusNoContent) }))
				send := func() int {
					req := httptest.NewRequest(http.MethodPost, "http://127.0.0.1/v1/traces", nil)
					req.Header.Set("X-Api-Key", api)
					req.Header.Set("X-Secret-Key", secret)
					response := httptest.NewRecorder()
					handler.ServeHTTP(response, req)
					return response.Code
				}
				if code := send(); code != http.StatusNoContent {
					t.Fatalf("positive control status %d", code)
				}
				// Leave polling margin by committing after the cache is populated,
				// without changing its age or relaxing the 30-second deadline.
				time.Sleep(100 * time.Millisecond)
				if mode == "reconnect" {
					mr.Close()
				}
				// Start before autocommit: this conservatively includes SQL and
				// the commit acknowledgement, never starts after a delayed notice.
				commitStarted := time.Now()
				routingFixture{t, primary}.exec(tc.sql)
				if mode == "delivered" {
					notice := RevocationPrefix + APIKeyDigest(api)
					if claims != nil {
						notice = "grant:" + claims.ID
					}
					if err := rdb.Publish(context.Background(), revocationChannel, notice).Err(); err != nil {
						t.Fatal(err)
					}
					awaitCondition(t, func() bool { _, state := a.cache.get(CacheKey(api, secret)); return state == "miss" })
				}
				if mode == "reconnect" {
					if err := mr.Restart(); err != nil {
						t.Fatal(err)
					}
				}
				code := send()
				for code == http.StatusNoContent && time.Since(commitStarted) < 31*time.Second {
					time.Sleep(5 * time.Millisecond)
					code = send()
				}
				elapsed := time.Since(commitStarted)
				if code != http.StatusUnauthorized || elapsed > 30*time.Second {
					t.Fatalf("revocation status=%d elapsed=%s, require 401 within 30s", code, elapsed)
				}
				if mode == "delivered" && elapsed > time.Second {
					t.Fatalf("delivered notice did not accelerate refusal: %s", elapsed)
				}
				stale := &PGResolver{read: replica, write: replica, peppers: cfg.Peppers}
				var result *ResolveResult
				if claims != nil {
					result, err = stale.ValidateGrant(context.Background(), claims)
				} else {
					result, err = stale.ValidateKey(context.Background(), api, secret)
				}
				if err != nil || result == nil {
					t.Fatalf("lagging read pool must remain eligible: %v", err)
				}
				t.Logf("HTTP 401 after commit-start: %s; read pool still eligible", elapsed)
			})
		}
	}
}
