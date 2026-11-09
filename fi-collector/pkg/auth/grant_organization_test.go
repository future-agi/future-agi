package auth

import (
	"context"
	"os"
	"os/exec"
	"strconv"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgxpool"
)

// Replace only the organization fixture with the real Django schema operations.
// Other grant dependencies deliberately remain the collector's small fixtures.
func migrateGrantOrganization(t *testing.T, pool *pgxpool.Pool, org string) {
	t.Helper()
	routingFixture{t, pool}.exec(`DROP TABLE accounts_organization`)
	python := os.Getenv("FI_TEST_MIGRATION_PYTHON")
	if python == "" {
		python = "python3"
	}
	ctx, cancel := context.WithTimeout(context.Background(), time.Minute)
	defer cancel()
	cmd := exec.CommandContext(ctx, python, "-I", "testdata/migrate_organization.py")
	cmd.Env = append(os.Environ(), "FI_TEST_ORGANIZATION_DSN="+pool.Config().ConnString(), "FI_TEST_ORGANIZATION_ID="+org, "FUTURE_AGI_LICENSE_URL=http://127.0.0.1:9")
	out, err := cmd.CombinedOutput()
	if err != nil {
		t.Fatalf("migrate organization (Django and psycopg required): %v\n%s", err, out)
	}
	t.Logf("organization migration receipt:\n%s", out)
	var hasDeleted bool
	var columns int
	err = pool.QueryRow(ctx, `SELECT count(*), COALESCE(bool_or(column_name='deleted'), false)
		FROM information_schema.columns WHERE table_schema='public' AND table_name='accounts_organization'`).Scan(&columns, &hasDeleted)
	if err != nil || columns != 10 || hasDeleted {
		t.Fatalf("unexpected migrated organization schema: columns=%d deleted=%v err=%v", columns, hasDeleted, err)
	}
}

func TestGrantOrganizationMigratedSchema(t *testing.T) {
	for _, scenario := range []string{"sandbox", "platform-workspace", "platform-default-workspace"} {
		t.Run(scenario, func(t *testing.T) {
			pool := routingDatabase(t)
			now := time.Now()
			fields := sandboxFields(now)
			wantPrincipal := PrincipalSandbox
			if scenario != "sandbox" {
				fields[4], fields[8], fields[10] = "collector-emit", strconv.FormatInt(now.Unix()+60, 10), "platform-emit"
				fields[13], fields[14], fields[15] = "-", "svc", "emit:sim"
				wantPrincipal = PrincipalPlatform
			}
			if scenario == "platform-default-workspace" {
				fields[12] = "-"
			}
			claims, err := VerifyCollectorGrant("fit_"+fields[9], signedGrantFields(fields, goldenPepper), mustPeppers(t, goldenPepper), []string{"k1"}, now)
			if err != nil {
				t.Fatal(err)
			}
			seedEligibility(t, pool, eligibilityFixture{claims.Org, routeWorkspace, "33333333-3333-4333-8333-333333333333", "synthetic-key", "synthetic-secret"})
			if scenario == "sandbox" {
				seedGrant(t, pool, claims)
			}
			migrateGrantOrganization(t, pool, claims.Org)
			r := &PGResolver{read: pool, write: pool}
			expect := func(t *testing.T, accepted bool) {
				t.Helper()
				res, err := r.ValidateGrant(context.Background(), claims)
				if err != nil || (res != nil) != accepted {
					t.Fatalf("grant accepted=%v want=%v err=%v", res != nil, accepted, err)
				}
				if accepted && (res.Principal != wantPrincipal || res.OrgID != claims.Org || res.WorkspaceID != routeWorkspace) {
					t.Fatalf("unexpected resolved principal: %s org=%s workspace=%s", res.Principal, res.OrgID, res.WorkspaceID)
				}
			}
			t.Run("existing-organization", func(t *testing.T) { expect(t, true) })
			fixture := routingFixture{t, pool}
			if claims.Workspace != "-" {
				for _, change := range []struct{ name, update, restore string }{
					{"inactive-workspace", `UPDATE accounts_workspace SET is_active=false`, `UPDATE accounts_workspace SET is_active=true`},
					{"deleted-workspace", `UPDATE accounts_workspace SET deleted=true`, `UPDATE accounts_workspace SET deleted=false`},
				} {
					t.Run(change.name, func(t *testing.T) {
						fixture.exec(change.update)
						t.Cleanup(func() { fixture.exec(change.restore) })
						expect(t, false)
					})
				}
				t.Run("foreign-workspace", func(t *testing.T) {
					fixture.exec(`UPDATE accounts_workspace SET organization_id=$1`, uuid.NewString())
					t.Cleanup(func() { fixture.exec(`UPDATE accounts_workspace SET organization_id=$1`, claims.Org) })
					expect(t, false)
				})
				t.Run("missing-workspace", func(t *testing.T) {
					fixture.exec(`UPDATE accounts_workspace SET id=$1`, uuid.NewString())
					t.Cleanup(func() { fixture.exec(`UPDATE accounts_workspace SET id=$1`, routeWorkspace) })
					expect(t, false)
				})
			}
			t.Run("missing-organization", func(t *testing.T) {
				fixture.exec(`DELETE FROM accounts_organization WHERE id=$1`, claims.Org)
				expect(t, false)
			})
		})
	}
}
