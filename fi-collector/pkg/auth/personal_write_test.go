package auth

import (
	"context"
	"errors"
	"log/slog"
	"testing"
)

func TestPersonalWriteRoleAndLevelPrecedence(t *testing.T) {
	pool := routingDatabase(t)
	f := newEligibilityFixture()
	seedEligibility(t, pool, f)
	r := &PGResolver{read: pool, write: pool, peppers: mustPeppers(t, goldenPepper)}
	exec := routingFixture{t, pool}.exec
	for _, tc := range []struct {
		name, orgRole, wsRole string
		orgLevel, wsLevel     any
		want                  bool
	}{
		{"viewer", "Viewer", "workspace_viewer", nil, nil, false},
		{"member_read_only_workspace", "Member", "workspace_viewer", 3, 1, false},
		{"writer", "Member", "workspace_member", 3, 3, true},
		{"legacy_writer", "Member", "workspace_member", nil, nil, true},
		{"workspace_admin", "Viewer", "workspace_admin", 1, 8, true},
		{"org_owner", "Owner", "workspace_viewer", 15, 1, true},
		{"org_admin", "Admin", "workspace_viewer", 8, 1, true},
		{"org_level_demotes_legacy_admin", "Admin", "workspace_viewer", 1, 1, false},
		{"org_level_promotes_legacy_viewer", "Viewer", "workspace_viewer", 8, 1, true},
		{"workspace_level_demotes_legacy_writer", "Member", "workspace_admin", 3, 1, false},
		{"workspace_level_promotes_legacy_viewer", "Viewer", "workspace_viewer", 1, 3, true},
		{"unknown_role", "Viewer", "not_a_role", 1, nil, false},
		{"unknown_level_is_not_a_writer", "Member", "workspace_admin", 3, 2, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			exec(`UPDATE accounts_organization_membership SET role=$1, level=$2`, tc.orgRole, tc.orgLevel)
			exec(`UPDATE accounts_workspacemembership SET role=$1, level=$2`, tc.wsRole, tc.wsLevel)
			for _, pinned := range []bool{true, false} {
				var workspace any
				if pinned {
					workspace = f.ws
				}
				exec(`UPDATE accounts_orgapikey SET workspace_id=$1`, workspace)
				result, err := r.ValidateKey(context.Background(), f.api, f.secret)
				if err != nil || (result != nil) != tc.want {
					t.Fatalf("pinned=%v accepted=%v want=%v err=%v", pinned, result != nil, tc.want, err)
				}
				if result != nil && result.WorkspaceID != f.ws {
					t.Fatal("writer admitted in a different workspace")
				}
			}
		})
	}
}

// Depends on SOL-18's cached PRIMARY revalidation, not just cold-cache SQL.
func TestPersonalWriteRemovedOrDemotedWithFreshCache(t *testing.T) {
	for _, change := range []string{
		`UPDATE accounts_organization_membership SET is_active=false`,
		`UPDATE accounts_organization_membership SET deleted=true`,
		`DELETE FROM accounts_organization_membership`,
		`UPDATE accounts_workspacemembership SET is_active=false`,
		`UPDATE accounts_workspacemembership SET level=1`,
		`UPDATE accounts_user SET is_active=false`,
	} {
		t.Run(change, func(t *testing.T) {
			primary, replica := routingDatabase(t), routingDatabase(t)
			f := newEligibilityFixture()
			seedEligibility(t, primary, f)
			seedEligibility(t, replica, f)
			peppers := mustPeppers(t, goldenPepper)
			a, err := New(context.Background(), Config{PGWrite: primary.Config().ConnString(), PGRead: replica.Config().ConnString(), Peppers: peppers}, nil, slog.Default())
			if err != nil {
				t.Fatal(err)
			}
			defer a.Close()
			if result, err := a.Authenticate(context.Background(), f.api, f.secret); result == nil || err != nil {
				t.Fatalf("writer control failed: %v", err)
			}
			routingFixture{t, primary}.exec(change)
			if _, status := a.cache.peek(CacheKey(f.api, f.secret)); status != "fresh" {
				t.Fatal("test must exercise a fresh entry, without a notice or age manipulation")
			}
			if result, err := a.Authenticate(context.Background(), f.api, f.secret); result != nil || !errors.Is(err, ErrUnauthenticated) {
				t.Fatalf("committed removal/demotion admitted: %v", err)
			}
			stale := &PGResolver{read: replica, write: replica, peppers: peppers}
			if result, err := stale.ValidateKey(context.Background(), f.api, f.secret); result == nil || err != nil {
				t.Fatalf("replica control must still be writable: %v", err)
			}
		})
	}
}

func TestPersonalWriteDefaultWorkspaceRequiresMembership(t *testing.T) {
	pool := routingDatabase(t)
	f := newEligibilityFixture()
	seedEligibility(t, pool, f)
	exec := routingFixture{t, pool}.exec
	r := &PGResolver{read: pool, write: pool, peppers: mustPeppers(t, goldenPepper)}
	exec(`UPDATE accounts_orgapikey SET workspace_id=NULL`)
	exec(`DELETE FROM accounts_workspacemembership`)
	if result, err := r.ValidateKey(context.Background(), f.api, f.secret); result != nil || err != nil {
		t.Fatalf("unassigned member admitted to default workspace: %v", err)
	}
	exec(`UPDATE accounts_organization_membership SET role='Admin', level=8`)
	if result, err := r.ValidateKey(context.Background(), f.api, f.secret); result == nil || err != nil || result.WorkspaceID != f.ws {
		t.Fatalf("global admin's unpinned workspace control failed: %v", err)
	}
	// Preserve L2/canonical personal admission's stricter exact pin requirement.
	exec(`UPDATE accounts_orgapikey SET workspace_id=$1`, f.ws)
	if result, err := r.ValidateKey(context.Background(), f.api, f.secret); result != nil || err != nil {
		t.Fatalf("pinned key lost its explicit workspace membership: %v", err)
	}
}
