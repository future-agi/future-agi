package auth

import (
	"context"
	"testing"
)

// Run with the existing isolated OBS_TEST_PG_DSN test database. This exercises
// the actual landing SQL; the service-free companion covers ValidateKey flow.
func TestPersonalWorkspaceLandingPostgres(t *testing.T) {
	pool := routingDatabase(t)
	person := newEligibilityFixture()
	seedEligibility(t, pool, person)
	fixture := routingFixture{t, pool}
	fixture.exec(`ALTER TABLE accounts_user ADD COLUMN config jsonb`)
	fixture.exec(`ALTER TABLE accounts_workspacemembership ADD COLUMN created_at timestamptz DEFAULT now()`)
	research := fixture.workspace(person.org, false, true, false)
	other := fixture.workspace(person.org, false, true, false)
	fixture.exec(`UPDATE accounts_workspacemembership SET workspace_id=$1, created_at='2026-01-01'`, research)
	fixture.exec(`INSERT INTO accounts_workspacemembership VALUES ($1, $2, true, false, 'workspace_member', 3, '2026-02-01')`, person.user, other)
	fixture.exec(`UPDATE accounts_orgapikey SET workspace_id=NULL`)
	r := &PGResolver{write: pool, read: pool, peppers: mustPeppers(t, goldenPepper)}
	expect := func(want string) {
		t.Helper()
		result, err := r.ValidateKey(context.Background(), person.api, person.secret)
		if err != nil {
			t.Fatal(err)
		}
		if want == "" {
			if result != nil {
				t.Fatal("expected refusal")
			}
		} else if result == nil || result.WorkspaceID != want || result.UserID != person.user {
			t.Fatalf("expected key owner's writable workspace %s", want)
		}
	}
	t.Run("unpinned_earliest_writable_membership", func(t *testing.T) { expect(research) })
	t.Run("persisted_per_org_preference", func(t *testing.T) {
		fixture.exec(`UPDATE accounts_user SET config=jsonb_build_object('orgWorkspaceMap', jsonb_build_object($1::text, $2::text), 'currentWorkspaceId', $3::text)`, person.org, other, research)
		expect(other)
	})
	t.Run("readonly_preference_is_skipped", func(t *testing.T) {
		fixture.exec(`UPDATE accounts_workspacemembership SET level=1 WHERE workspace_id=$1`, other)
		expect(research)
	})
	t.Run("legacy_preference", func(t *testing.T) {
		fixture.exec(`UPDATE accounts_user SET config=jsonb_build_object('currentWorkspaceId', $1::text)`, research)
		expect(research)
	})
	t.Run("configured_default_preference", func(t *testing.T) {
		fixture.exec(`UPDATE accounts_workspacemembership SET level=3 WHERE workspace_id=$1`, other)
		fixture.exec(`UPDATE accounts_user SET config=jsonb_build_object('defaultWorkspaceId', $1::text)`, other)
		expect(other)
	})
	t.Run("explicit_pin_wins_over_preferences", func(t *testing.T) {
		fixture.exec(`UPDATE accounts_orgapikey SET workspace_id=$1`, research)
		expect(research)
	})
	t.Run("inaccessible_pin_does_not_fall_back", func(t *testing.T) {
		fixture.exec(`UPDATE accounts_orgapikey SET workspace_id=$1`, person.ws)
		expect("")
	})
	t.Run("deleted_or_inactive_workspaces_are_skipped", func(t *testing.T) {
		fixture.exec(`UPDATE accounts_orgapikey SET workspace_id=NULL`)
		fixture.exec(`UPDATE accounts_workspace SET deleted=true WHERE id=$1`, other)
		expect(research)
		fixture.exec(`UPDATE accounts_workspace SET is_active=false WHERE id=$1`, research)
		expect("")
	})
	t.Run("global_admin_retains_default_without_membership", func(t *testing.T) {
		fixture.exec(`UPDATE accounts_user SET config='{}'::jsonb`)
		fixture.exec(`UPDATE accounts_organization_membership SET role='Admin', level=8`)
		expect(person.ws)
	})
	t.Run("inactive_org_membership_fails_closed", func(t *testing.T) {
		fixture.exec(`UPDATE accounts_organization_membership SET is_active=false`)
		expect("")
	})
}
