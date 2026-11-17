package auth

import (
	"context"
	"fmt"
	"os"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgxpool"
)

// TestValidateKeyAgainstPostgresBeforeAndAfterTheMigration runs ValidateKey on a
// real Postgres, in a throwaway schema shaped like accounts_orgapikey:
//  1. before Django's migration 0026 (no secret_key_hash column, plaintext
//     secret): the collector can roll out first;
//  2. after 0026 adds the column and before 0027 seals the row (or while 0027
//     is still running): the plaintext verifies against the empty hash column;
//  3. after 0027 (hash set, plaintext empty): Django's golden hash verifies;
//  4. while the backend rolls: a previous-release pod saving the sealed key writes
//     a random plaintext beside the hash (the hash still decides), and one creating
//     a key leaves the new column to its empty default (the plaintext verifies).
//
// Set FI_COLLECTOR_TEST_PG_URL (e.g. postgres://test_user:test_password@127.0.0.1:15499/test_tfc).
func TestValidateKeyAgainstPostgresBeforeAndAfterTheMigration(t *testing.T) {
	dsn := os.Getenv("FI_COLLECTOR_TEST_PG_URL")
	if dsn == "" {
		t.Skip("FI_COLLECTOR_TEST_PG_URL not set")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	schema := "fi_secret_test_" + uuid.NewString()[:8]
	admin, err := pgxpool.New(ctx, dsn)
	if err != nil {
		t.Fatal(err)
	}
	defer admin.Close()
	exec := func(sql string, args ...any) {
		t.Helper()
		if _, err := admin.Exec(ctx, sql, args...); err != nil {
			t.Fatalf("%s: %v", sql, err)
		}
	}
	exec("CREATE SCHEMA " + schema)
	defer func() { _, _ = admin.Exec(context.Background(), "DROP SCHEMA "+schema+" CASCADE") }()
	exec(fmt.Sprintf(`CREATE TABLE %s.accounts_workspace (
		id uuid PRIMARY KEY, organization_id uuid NOT NULL,
		is_default boolean NOT NULL, is_active boolean NOT NULL, deleted boolean NOT NULL)`, schema))
	exec(fmt.Sprintf(`CREATE TABLE %s.accounts_orgapikey (
		id uuid PRIMARY KEY, api_key varchar(255) UNIQUE NOT NULL, secret_key varchar(255) NOT NULL,
		organization_id uuid NOT NULL, workspace_id uuid, user_id uuid, type varchar(50) NOT NULL,
		enabled boolean NOT NULL, deleted boolean NOT NULL)`, schema))
	org, ws, user := uuid.NewString(), uuid.NewString(), uuid.NewString()
	exec(fmt.Sprintf(`CREATE TABLE %s.accounts_user (id uuid PRIMARY KEY, is_active boolean NOT NULL)`, schema))
	exec(fmt.Sprintf(`CREATE TABLE %s.accounts_organization_membership (user_id uuid, organization_id uuid, is_active boolean, deleted boolean, role varchar(20) NOT NULL, level smallint)`, schema))
	exec(fmt.Sprintf(`CREATE TABLE %s.accounts_workspacemembership (user_id uuid, workspace_id uuid, is_active boolean, deleted boolean, role varchar(20) NOT NULL, level smallint)`, schema))
	exec(fmt.Sprintf(`INSERT INTO %s.accounts_user VALUES ($1, true)`, schema), user)
	exec(fmt.Sprintf(`INSERT INTO %s.accounts_organization_membership VALUES ($1, $2, true, false, 'Member', 3)`, schema), user, org)
	exec(fmt.Sprintf(`INSERT INTO %s.accounts_workspacemembership VALUES ($1, $2, true, false, 'workspace_member', 3)`, schema), user, ws)
	exec(fmt.Sprintf(`INSERT INTO %s.accounts_workspace VALUES ($1, $2, true, true, false)`, schema), ws, org)
	exec(fmt.Sprintf(`INSERT INTO %s.accounts_orgapikey VALUES ($1, $2, $3, $4, NULL, $5, 'user', true, false)`, schema),
		uuid.NewString(), goldenAPIKey, "legacy-plaintext-secret", org, user)

	cfg, err := pgxpool.ParseConfig(dsn)
	if err != nil {
		t.Fatal(err)
	}
	cfg.ConnConfig.RuntimeParams["search_path"] = schema
	pool, err := pgxpool.NewWithConfig(ctx, cfg)
	if err != nil {
		t.Fatal(err)
	}
	defer pool.Close()
	r := &PGResolver{read: pool, write: pool, peppers: mustPeppers(t, goldenPepper)}

	expect := func(stage, secret string, want bool) {
		t.Helper()
		res, err := r.ValidateKey(ctx, goldenAPIKey, secret)
		if err != nil {
			t.Fatalf("%s: %v", stage, err)
		}
		if (res != nil) != want {
			t.Fatalf("%s: accepted=%v, want %v", stage, res != nil, want)
		}
		if res != nil && (res.OrgID != org || res.WorkspaceID != ws || res.APIKeyDigest != APIKeyDigest(goldenAPIKey)) {
			t.Fatalf("%s: wrong scope %+v", stage, res)
		}
	}
	expect("before 0026, right plaintext", "legacy-plaintext-secret", true)
	expect("before 0026, wrong secret", "legacy-plaintext-secreT", false)
	if res, err := r.ValidateKey(ctx, "no-such-key", "x"); err != nil || res != nil {
		t.Fatalf("unknown key: %v %v", res, err)
	}

	exec(fmt.Sprintf(`ALTER TABLE %s.accounts_orgapikey ADD COLUMN secret_key_hash varchar(128) NOT NULL DEFAULT ''`, schema))
	expect("after 0026, before 0027 seals it, right plaintext", "legacy-plaintext-secret", true)
	expect("after 0026, before 0027 seals it, wrong secret", "legacy-plaintext-secreT", false)
	exec(fmt.Sprintf(`UPDATE %s.accounts_orgapikey SET secret_key = '', secret_key_hash = $1`, schema), goldenHash)
	expect("after 0027, Django's golden hash", goldenSecret, true)
	expect("after 0027, the old plaintext", "legacy-plaintext-secret", false)
	expect("after 0027, wrong secret", goldenSecret+"x", false)
	expect("after 0027, empty secret", "", false)

	exec(fmt.Sprintf(`UPDATE %s.accounts_orgapikey SET secret_key = 'stale-value-an-old-pod-wrote'`, schema))
	expect("stale plaintext beside the hash, the real secret", goldenSecret, true)
	expect("stale plaintext beside the hash, the stale value", "stale-value-an-old-pod-wrote", false)
	oldPodKey := "old-pod-key-" + uuid.NewString()[:8]
	exec(fmt.Sprintf(`INSERT INTO %s.accounts_orgapikey
		(id, api_key, secret_key, organization_id, user_id, type, enabled, deleted)
		VALUES ($1, $2, 'old-pod-plaintext', $3, $4, 'user', true, false)`, schema), uuid.NewString(), oldPodKey, org, user)
	for secret, want := range map[string]bool{"old-pod-plaintext": true, "old-pod-plaintexT": false} {
		res, err := r.ValidateKey(ctx, oldPodKey, secret)
		if err != nil || (res != nil) != want || (res != nil && res.OrgID != org) {
			t.Fatalf("key an old pod created after 0026, secret %q: %+v %v, want accepted=%v", secret, res, err, want)
		}
	}

	exec(fmt.Sprintf(`UPDATE %s.accounts_orgapikey SET enabled = false`, schema))
	expect("after 0027, disabled", goldenSecret, false)
}
