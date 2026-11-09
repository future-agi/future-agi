package auth

import (
	"context"
	"encoding/json"
	"errors"
	"log/slog"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/alicebob/miniredis/v2"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/redis/go-redis/v9"
)

const grantSchema = `CREATE TABLE accounts_service_grant (
 jti varchar(32) PRIMARY KEY, kind text NOT NULL, kid text NOT NULL, aud text NOT NULL,
 org uuid NOT NULL, workspace uuid, "user" uuid, via text NOT NULL, sub text NOT NULL,
 scope text NOT NULL, iat bigint NOT NULL, exp bigint NOT NULL, revoked_at timestamptz,
 revoked_reason text NOT NULL, agent_id uuid, attempt_id uuid, batch_ref uuid
)`

func seedGrant(t *testing.T, pool *pgxpool.Pool, c *GrantClaims) {
	t.Helper()
	exec := routingFixture{t, pool}.exec
	exec(grantSchema)
	exec(`CREATE TABLE simulate_hosted_harness_job (id uuid PRIMARY KEY, organization_id uuid, workspace_id uuid, deleted boolean, cancel_requested_at timestamptz, terminal_at timestamptz)`)
	exec(`CREATE TABLE simulate_hosted_harness_attempt (id uuid PRIMARY KEY, job_id uuid, deleted boolean, state text, expires_at timestamptz)`)
	sub := strings.Split(c.Sub, ":")
	exec(`INSERT INTO simulate_hosted_harness_job VALUES ($1,$2,$3,false,NULL,NULL)`, sub[2], c.Org, c.Workspace)
	exec(`INSERT INTO simulate_hosted_harness_attempt VALUES ($1,$1,false,'running',to_timestamp($2))`, sub[2], c.Exp)
	exec(`INSERT INTO simulate_agent_definition VALUES ($1,$2,$3,false,now(),now())`, sub[1], c.Org, c.Workspace)
	exec(`INSERT INTO accounts_service_grant VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,NULL,'',$13,$14,NULL)`, c.ID, c.Kind, c.Kid, c.Aud, c.Org, c.Workspace, c.User, c.Via, c.Sub, c.Scope, c.Iat, c.Exp, sub[1], sub[2])
}

func TestGrantRegistryEveryFieldMustMatch(t *testing.T) {
	f := sandboxFields(time.Now())
	p := mustPeppers(t, goldenPepper)
	c, err := VerifyCollectorGrant("fit_"+f[9], signedGrantFields(f, goldenPepper), p, []string{"k1"}, time.Now())
	if err != nil {
		t.Fatal(err)
	}
	sub := strings.Split(c.Sub, ":")
	row := grantRecord{GrantClaims: *c, AgentID: &sub[1], AttemptID: &sub[2]}
	if !row.matches(c) {
		t.Fatal("matching registry row refused")
	}
	typ := reflect.TypeOf(*c)
	for i := 0; i < typ.NumField(); i++ {
		t.Run(typ.Field(i).Name, func(t *testing.T) {
			bad := row
			field := reflect.ValueOf(&bad.GrantClaims).Elem().Field(i)
			if field.Kind() == reflect.String {
				field.SetString(field.String() + "x")
			} else {
				field.SetInt(field.Int() + 1)
			}
			if bad.matches(c) {
				t.Fatal("mismatched registry field accepted")
			}
		})
	}
	revoked := "2026-01-01T00:00:00Z"
	bad := row
	bad.RevokedAt = &revoked
	if bad.matches(c) {
		t.Fatal("revoked row accepted")
	}
	bad = row
	bad.AgentID = &c.Org
	if bad.matches(c) {
		t.Fatal("agent mismatch accepted")
	}
	bad = row
	bad.AttemptID = &c.Org
	if bad.matches(c) {
		t.Fatal("attempt mismatch accepted")
	}
	bad = row
	bad.BatchRef = &c.Org
	if bad.matches(c) {
		t.Fatal("unexpected batch binding accepted")
	}
}

func TestGrantRegistryAndMembershipUsePrimary(t *testing.T) {
	for _, change := range []string{
		`UPDATE accounts_service_grant SET revoked_at=now()`,
		`UPDATE accounts_organization_membership SET is_active=false`,
		`UPDATE accounts_workspacemembership SET is_active=false`,
		`UPDATE accounts_user SET is_active=false`,
		`UPDATE simulate_agent_definition SET deleted=true`,
		`UPDATE simulate_agent_definition SET workspace_id=NULL`,
		`DELETE FROM accounts_service_grant`,
		`UPDATE simulate_hosted_harness_attempt SET state='canceled'`,
		`UPDATE simulate_hosted_harness_job SET cancel_requested_at=now()`,
		`UPDATE simulate_hosted_harness_attempt SET deleted=true`,
	} {
		for _, delivered := range []bool{true, false} {
			t.Run(change+"/"+map[bool]string{true: "delivered", false: "missed"}[delivered], func(t *testing.T) {
				primary, replica := routingDatabase(t), routingDatabase(t)
				now := time.Now()
				fields := sandboxFields(now)
				api := "fit_" + fields[9]
				token := signedGrantFields(fields, goldenPepper)
				p := mustPeppers(t, goldenPepper)
				claims, err := VerifyCollectorGrant(api, token, p, []string{"k1"}, now)
				if err != nil {
					t.Fatal(err)
				}
				f := eligibilityFixture{org: claims.Org, ws: claims.Workspace, user: claims.User, api: "synthetic-key", secret: "synthetic-secret"}
				for _, pool := range []*pgxpool.Pool{primary, replica} {
					seedEligibility(t, pool, f)
					seedGrant(t, pool, claims)
				}
				mr := miniredis.RunT(t)
				rdb := redis.NewClient(&redis.Options{Addr: mr.Addr()})
				defer rdb.Close()
				a, err := New(context.Background(), Config{PGWrite: primary.Config().ConnString(), PGRead: replica.Config().ConnString(), Peppers: p, RedisAddr: mr.Addr()}, rdb, slog.Default())
				if err != nil {
					t.Fatal(err)
				}
				defer a.Close()
				startFreshnessWatcher(t, a)
				res, err := a.Authenticate(context.Background(), api, token)
				if err != nil || res == nil || res.Principal != PrincipalSandbox || res.SimulationAgent.ID != routeAgent {
					t.Fatalf("sandbox principal not resolved: %v", err)
				}
				routingFixture{t, primary}.exec(change)
				if delivered {
					if err := rdb.Publish(context.Background(), revocationChannel, "grant:"+claims.ID).Err(); err != nil {
						t.Fatal(err)
					}
					awaitCondition(t, func() bool { _, s := a.cache.get(CacheKey(api, token)); return s == "miss" })
				} else {
					e, _ := a.cache.get(CacheKey(api, token))
					e.fetchedAt = time.Now().Add(-30 * time.Second)
				}
				if _, err := a.Authenticate(context.Background(), api, token); !errors.Is(err, ErrUnauthenticated) {
					t.Fatalf("changed primary eligibility was not refused: %v", err)
				}
				stale := &PGResolver{read: replica, write: replica}
				if res, err := stale.ValidateGrant(context.Background(), claims); err != nil || res == nil {
					t.Fatalf("replica did not remain eligible: %v", err)
				}
			})
		}
	}
}

func TestGrantRegistryColumnContract(t *testing.T) {
	pool := routingDatabase(t)
	f := sandboxFields(time.Now())
	c, err := VerifyCollectorGrant("fit_"+f[9], signedGrantFields(f, goldenPepper), mustPeppers(t, goldenPepper), []string{"k1"}, time.Now())
	if err != nil {
		t.Fatal(err)
	}
	seedEligibility(t, pool, eligibilityFixture{c.Org, c.Workspace, c.User, "synthetic-key", "synthetic-secret"})
	seedGrant(t, pool, c)
	var raw []byte
	if err := pool.QueryRow(context.Background(), `SELECT to_jsonb(g) FROM accounts_service_grant g`).Scan(&raw); err != nil {
		t.Fatal(err)
	}
	var row grantRecord
	if err := json.Unmarshal(raw, &row); err != nil {
		t.Fatal(err)
	}
	if !row.matches(c) {
		t.Fatal("database column adapter differs from canonical claims")
	}
	// This asserts the DESIGN wire/schema adapter. The issuer migration and
	// published cross-language vectors must also be pinned before enabling grants.
}
