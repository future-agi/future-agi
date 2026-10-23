package auth

import (
	"context"
	"errors"
	"net/http"
	"testing"

	authpkg "github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	rbacplugin "github.com/futureagi/agentcc-gateway/internal/plugins/rbac"
	"github.com/futureagi/agentcc-gateway/internal/rbac"
)

// The RBAC plugin reads key_team as the key's team (its role, member roles and
// model list), and the budget plugin as the team budget its spend counts
// against. Key metadata "team" is authority: the key's own, never changed on
// an existing key, never copied as a label.
func TestPipeline_MetadataTeamCannotChangeAKeysRole(t *testing.T) {
	const raw = "sk-agentcc-team-escalation"
	ks := authpkg.NewKeyStore(config.AuthConfig{Enabled: true, Keys: []config.AuthKeyConfig{{
		Name: "tenant-key", Key: raw, Owner: "tenant", KeyType: "byok",
		Metadata: map[string]string{"org_id": "org-a", "env": "prod"},
	}}})
	roles := rbac.NewStore(config.RBACConfig{
		Enabled:     true,
		DefaultRole: "viewer",
		Roles: map[string]config.RBACRoleConfig{
			"admin":  {Permissions: []string{"*"}},
			"viewer": {Permissions: []string{"models:gpt-3.5-turbo"}},
		},
		Teams: map[string]config.RBACTeamConfig{"platform-admins": {Role: "admin"}},
	})
	key := ks.Authenticate(raw)

	// What the admin PUT /-/keys/{id} runs for a metadata change.
	_, err := ks.Update(key.ID, nil, nil, nil, nil, map[string]string{"team": "platform-admins"})
	var reserved *authpkg.ReservedMetadataError
	if !errors.As(err, &reserved) {
		t.Fatalf("Update(team) = %v, want a ReservedMetadataError", err)
	}

	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.Model = "gpt-4o"
	rc.Metadata["authorization"] = "Bearer " + raw
	rc.Metadata["key_team"] = "platform-admins" // set on the request before the auth plugin runs
	engine := pipeline.NewEngine(New(ks, true), rbacplugin.New(roles, true))
	called := false
	err = engine.Process(context.Background(), rc, func(context.Context, *models.RequestContext) error {
		called = true
		return nil
	})

	var apiErr *models.APIError
	if called || !errors.As(err, &apiErr) || apiErr.Status != http.StatusForbidden {
		t.Fatalf("gpt-4o with a viewer key: called=%v err=%v, want a 403 from RBAC", called, err)
	}
	if got := rc.Metadata["rbac_team"]; got != "" {
		t.Errorf("rbac_team = %q, want none: the key has no team", got)
	}
}

// A key the operator gave a team in config.yaml has it on every request: the auth plugin
// writes key_team from the key's own metadata, after the labels.
func TestPlugin_ConfigKeyHasItsTeam(t *testing.T) {
	_, rc := authenticate(t, config.AuthKeyConfig{
		Name: "ops-key", Key: "sk-agentcc-team-config", KeyType: "byok",
		Metadata: map[string]string{"team": "platform"},
	})
	defer rc.Release()

	if got := rc.Metadata["key_team"]; got != "platform" {
		t.Errorf("key_team = %q, want platform", got)
	}
}
