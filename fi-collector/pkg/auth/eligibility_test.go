package auth

import (
	"context"
	"errors"
	"log/slog"
	"testing"
	"time"

	"github.com/alicebob/miniredis/v2"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/redis/go-redis/v9"
)

// Independent databases model a primary and a replica which has not replayed
// a committed eligibility change. The replica deliberately remains eligible.
const eligibilitySchema = `
CREATE TABLE IF NOT EXISTS accounts_organization (id uuid PRIMARY KEY);
CREATE TABLE IF NOT EXISTS accounts_user (id uuid PRIMARY KEY, is_active boolean NOT NULL);
CREATE TABLE IF NOT EXISTS accounts_organization_membership (user_id uuid, organization_id uuid, is_active boolean, deleted boolean, role varchar(20) NOT NULL, level smallint);
CREATE TABLE IF NOT EXISTS accounts_workspacemembership (user_id uuid, workspace_id uuid, is_active boolean, deleted boolean, role varchar(20) NOT NULL, level smallint);
`

type eligibilityFixture struct{ org, ws, user, api, secret string }

func seedEligibility(t *testing.T, pool *pgxpool.Pool, f eligibilityFixture) {
	t.Helper()
	exec := routingFixture{t, pool}.exec
	exec(eligibilitySchema)
	exec(`INSERT INTO accounts_organization (id) VALUES ($1)`, f.org)
	exec(`INSERT INTO accounts_user VALUES ($1, true)`, f.user)
	exec(`INSERT INTO accounts_workspace VALUES ($1, 'default', '', '', $2, true, true, $3, now(), now(), false, NULL)`, f.ws, f.org, f.user)
	exec(`INSERT INTO accounts_organization_membership VALUES ($1, $2, true, false, 'Member', 3)`, f.user, f.org)
	exec(`INSERT INTO accounts_workspacemembership VALUES ($1, $2, true, false, 'workspace_member', 3)`, f.user, f.ws)
	exec(`INSERT INTO accounts_orgapikey VALUES ($1, 'personal', $2, $3, $4, 'user', true, $5, $6, now(), now(), false, NULL, NULL)`, uuid.NewString(), f.api, f.secret, f.org, f.user, f.ws)
}

func newEligibilityFixture() eligibilityFixture {
	return eligibilityFixture{uuid.NewString(), uuid.NewString(), uuid.NewString(), uuid.NewString(), "synthetic-personal-secret"}
}

func TestEligibilityUsesPrimaryAfterCommittedChange(t *testing.T) {
	changes := map[string]string{
		"key":                  `UPDATE accounts_orgapikey SET enabled=false`,
		"membership":           `UPDATE accounts_organization_membership SET is_active=false`,
		"workspace_membership": `UPDATE accounts_workspacemembership SET deleted=true`,
		"person":               `UPDATE accounts_user SET is_active=false`,
	}
	for name, change := range changes {
		for _, delivered := range []bool{true, false} {
			label := name + "/missed"
			if delivered {
				label = name + "/delivered"
			}
			t.Run(label, func(t *testing.T) {
				primary, replica := routingDatabase(t), routingDatabase(t)
				f := newEligibilityFixture()
				seedEligibility(t, primary, f)
				seedEligibility(t, replica, f)
				mr := miniredis.RunT(t)
				rdb := redis.NewClient(&redis.Options{Addr: mr.Addr()})
				defer rdb.Close()
				cfg := Config{PGWrite: primary.Config().ConnString(), PGRead: replica.Config().ConnString(), Peppers: mustPeppers(t, goldenPepper), RedisAddr: mr.Addr()}
				a, err := New(context.Background(), cfg, rdb, slog.Default())
				if err != nil {
					t.Fatal(err)
				}
				defer a.Close()
				startFreshnessWatcher(t, a)
				if _, err := a.Authenticate(context.Background(), f.api, f.secret); err != nil {
					t.Fatal(err)
				}
				// Exec has committed before the revocation notice (or its absence).
				routingFixture{t, primary}.exec(change)
				ck := CacheKey(f.api, f.secret)
				if delivered {
					before := a.cache.generation.Load()
					if err := rdb.Publish(context.Background(), revocationChannel, RevocationPrefix+APIKeyDigest(f.api)).Err(); err != nil {
						t.Fatal(err)
					}
					awaitCondition(t, func() bool {
						_, status := a.cache.get(ck)
						return a.cache.generation.Load() > before && status == "miss"
					})
				} else {
					// Advance the entry's age to the bound, not to the old five-minute TTL.
					e, _ := a.cache.get(ck)
					if e == nil {
						t.Fatal("expected cached result")
					}
					e.fetchedAt = time.Now().Add(-30 * time.Second)
				}
				if _, err := a.Authenticate(context.Background(), f.api, f.secret); !errors.Is(err, ErrUnauthenticated) {
					t.Fatalf("committed %s change with replica lag: expected refusal, got %v", name, err)
				}
				r := &PGResolver{read: replica, write: replica, peppers: cfg.Peppers}
				if res, err := r.ValidateKey(context.Background(), f.api, f.secret); err != nil || res == nil {
					t.Fatalf("replica must still be eligible: %v", err)
				}
			})
		}
	}
}

func TestEligibilityOnlyPersonalMembers(t *testing.T) {
	pool := routingDatabase(t)
	f := newEligibilityFixture()
	seedEligibility(t, pool, f)
	r := &PGResolver{read: pool, write: pool, peppers: mustPeppers(t, goldenPepper)}
	exec := routingFixture{t, pool}.exec
	expect := func(want bool) {
		t.Helper()
		res, err := r.ValidateKey(context.Background(), f.api, f.secret)
		if err != nil || (res != nil) != want {
			t.Fatalf("accepted=%v want=%v err=%v", res != nil, want, err)
		}
	}
	expect(true)
	for _, kind := range []string{"system", "mcp", "ingest"} {
		exec(`UPDATE accounts_orgapikey SET type=$1`, kind)
		expect(false)
	}
	exec(`UPDATE accounts_orgapikey SET type='user', user_id=NULL`)
	expect(false)
	exec(`UPDATE accounts_orgapikey SET user_id=$1`, f.user)
	exec(`ALTER TABLE accounts_orgapikey ADD COLUMN retired_at timestamptz NULL`)
	exec(`UPDATE accounts_orgapikey SET retired_at=now()`)
	expect(false)
	exec(`UPDATE accounts_orgapikey SET retired_at=NULL`)
	expect(true)
}

func TestEligibilityNoWarmWindow(t *testing.T) {
	for _, redis := range []string{"", "configured"} {
		cfg := Config{RedisAddr: redis, CacheTTL: 5 * time.Minute, WarmTTL: time.Hour}
		cfg.defaults()
		if cfg.CacheTTL > 30*time.Second || cfg.WarmTTL != 0 {
			t.Errorf("TTL must be bounded independent of Redis: fresh=%s warm=%s", cfg.CacheTTL, cfg.WarmTTL)
		}
		c := newCache(cfg.CacheTTL, cfg.WarmTTL)
		c.putPositive("key", &ResolveResult{})
		e, _ := c.get("key")
		e.fetchedAt = time.Now().Add(-30 * time.Second)
		if _, status := c.get("key"); status != "miss" {
			t.Errorf("expired entry served as %s", status)
		}
	}
}
