package rbac

import (
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

func testConfig() config.RBACConfig {
	return config.RBACConfig{
		Enabled:     true,
		DefaultRole: "member",
		Roles: map[string]config.RBACRoleConfig{
			"admin": {Permissions: []string{"*"}},
			"manager": {Permissions: []string{
				"models:*", "providers:*", "admin:keys:read", "admin:keys:write", "guardrails:override",
			}},
			"member": {Permissions: []string{
				"models:gpt-4o", "models:gpt-3.5-turbo", "models:claude-*",
				"providers:openai", "providers:anthropic",
			}},
			"viewer": {Permissions: []string{
				"models:gpt-3.5-turbo", "providers:openai",
			}},
		},
		Teams: map[string]config.RBACTeamConfig{
			"engineering": {
				Role:   "member",
				Models: []string{"gpt-4o", "claude-3-opus"},
				Members: map[string]config.RBACMemberConfig{
					"alice": {Role: "admin"},
					"bob":   {},
				},
			},
			"research": {
				Role:   "manager",
				Models: []string{"*"},
			},
		},
	}
}

// --- Permission Matching ---

func TestMatchPermission_GlobalWildcard(t *testing.T) {
	if !matchPermission("*", "models:gpt-4o") {
		t.Error("* should match everything")
	}
	if !matchPermission("*", "admin:config:write") {
		t.Error("* should match admin permissions")
	}
}

func TestMatchPermission_CategoryWildcard(t *testing.T) {
	if !matchPermission("models:*", "models:gpt-4o") {
		t.Error("models:* should match models:gpt-4o")
	}
	if matchPermission("models:*", "providers:openai") {
		t.Error("models:* should not match providers:openai")
	}
}

func TestMatchPermission_PrefixWildcard(t *testing.T) {
	if !matchPermission("models:gpt-*", "models:gpt-4o") {
		t.Error("models:gpt-* should match models:gpt-4o")
	}
	if !matchPermission("models:gpt-*", "models:gpt-3.5-turbo") {
		t.Error("models:gpt-* should match models:gpt-3.5-turbo")
	}
	if matchPermission("models:gpt-*", "models:claude-3") {
		t.Error("models:gpt-* should not match models:claude-3")
	}
}

func TestMatchPermission_ExactMatch(t *testing.T) {
	if !matchPermission("models:gpt-4o", "models:gpt-4o") {
		t.Error("exact match should work")
	}
	if matchPermission("models:gpt-4o", "models:gpt-4") {
		t.Error("partial match should not work")
	}
}

func TestMatchPermission_AdminWildcard(t *testing.T) {
	if !matchPermission("admin:keys:*", "admin:keys:read") {
		t.Error("admin:keys:* should match admin:keys:read")
	}
	if !matchPermission("admin:keys:*", "admin:keys:write") {
		t.Error("admin:keys:* should match admin:keys:write")
	}
	if matchPermission("admin:keys:*", "admin:config:read") {
		t.Error("admin:keys:* should not match admin:config:read")
	}
}

// --- HasPermission ---

func TestHasPermission_Admin(t *testing.T) {
	s := NewStore(testConfig())
	if !s.HasPermission("admin", "models:gpt-4o") {
		t.Error("admin should have model access")
	}
	if !s.HasPermission("admin", "admin:config:write") {
		t.Error("admin should have admin access")
	}
	if !s.HasPermission("admin", "guardrails:override") {
		t.Error("admin should have guardrail override")
	}
}

func TestHasPermission_Manager(t *testing.T) {
	s := NewStore(testConfig())
	if !s.HasPermission("manager", "models:gpt-4o") {
		t.Error("manager should have model access")
	}
	if !s.HasPermission("manager", "admin:keys:read") {
		t.Error("manager should have key read access")
	}
	if !s.HasPermission("manager", "guardrails:override") {
		t.Error("manager should have guardrail override")
	}
	if s.HasPermission("manager", "admin:config:write") {
		t.Error("manager should not have config write access")
	}
}

func TestHasPermission_Member(t *testing.T) {
	s := NewStore(testConfig())
	if !s.HasPermission("member", "models:gpt-4o") {
		t.Error("member should access gpt-4o")
	}
	if !s.HasPermission("member", "models:claude-3-opus") {
		t.Error("member should access claude-* via wildcard")
	}
	if s.HasPermission("member", "models:llama-3") {
		t.Error("member should not access llama-3")
	}
	if s.HasPermission("member", "admin:keys:read") {
		t.Error("member should not have admin access")
	}
	if s.HasPermission("member", "guardrails:override") {
		t.Error("member should not have guardrail override")
	}
}

func TestHasPermission_Viewer(t *testing.T) {
	s := NewStore(testConfig())
	if !s.HasPermission("viewer", "models:gpt-3.5-turbo") {
		t.Error("viewer should access gpt-3.5-turbo")
	}
	if s.HasPermission("viewer", "models:gpt-4o") {
		t.Error("viewer should not access gpt-4o")
	}
}

func TestHasPermission_UnknownRole(t *testing.T) {
	s := NewStore(testConfig())
	if s.HasPermission("unknown", "models:gpt-4o") {
		t.Error("unknown role should have no permissions")
	}
}

// --- ResolveRole ---

func TestResolveRole_UserLevel(t *testing.T) {
	s := NewStore(testConfig())
	role := s.ResolveRole("engineering", "alice", "")
	if role != "admin" {
		t.Errorf("alice should be admin, got %q", role)
	}
}

func TestResolveRole_KeyLevel(t *testing.T) {
	s := NewStore(testConfig())
	role := s.ResolveRole("engineering", "charlie", "manager")
	if role != "manager" {
		t.Errorf("key-level role should be manager, got %q", role)
	}
}

func TestResolveRole_TeamLevel(t *testing.T) {
	s := NewStore(testConfig())
	role := s.ResolveRole("engineering", "bob", "")
	if role != "member" {
		t.Errorf("bob should inherit team role member, got %q", role)
	}
}

func TestResolveRole_TeamLevelResearch(t *testing.T) {
	s := NewStore(testConfig())
	role := s.ResolveRole("research", "dave", "")
	if role != "manager" {
		t.Errorf("research team should be manager, got %q", role)
	}
}

func TestResolveRole_Default(t *testing.T) {
	s := NewStore(testConfig())
	role := s.ResolveRole("", "", "")
	if role != "member" {
		t.Errorf("default should be member, got %q", role)
	}
}

func TestResolveRole_UnknownTeam(t *testing.T) {
	s := NewStore(testConfig())
	role := s.ResolveRole("unknown-team", "someone", "")
	if role != "member" {
		t.Errorf("unknown team should fall to default, got %q", role)
	}
}

func TestResolveRole_UserOverridesKey(t *testing.T) {
	s := NewStore(testConfig())
	// alice has user-level admin role, even if key says viewer.
	role := s.ResolveRole("engineering", "alice", "viewer")
	if role != "admin" {
		t.Errorf("user-level should override key-level, got %q", role)
	}
}

// --- TeamModelAllowed ---

func TestTeamModelAllowed_InList(t *testing.T) {
	s := NewStore(testConfig())
	if !s.TeamModelAllowed("engineering", "gpt-4o") {
		t.Error("engineering should allow gpt-4o")
	}
}

func TestTeamModelAllowed_NotInList(t *testing.T) {
	s := NewStore(testConfig())
	if s.TeamModelAllowed("engineering", "llama-3") {
		t.Error("engineering should not allow llama-3")
	}
}

func TestTeamModelAllowed_WildcardTeam(t *testing.T) {
	s := NewStore(testConfig())
	if !s.TeamModelAllowed("research", "anything-at-all") {
		t.Error("research team has * models, should allow anything")
	}
}

func TestTeamModelAllowed_UnknownTeam(t *testing.T) {
	s := NewStore(testConfig())
	if !s.TeamModelAllowed("nonexistent", "gpt-4o") {
		t.Error("unknown team should allow all (no restrictions)")
	}
}

func TestTeamModelAllowed_NoModelsConfigured(t *testing.T) {
	cfg := config.RBACConfig{
		Teams: map[string]config.RBACTeamConfig{
			"empty-team": {Role: "member"},
		},
	}
	s := NewStore(cfg)
	if !s.TeamModelAllowed("empty-team", "gpt-4o") {
		t.Error("team with no model restrictions should allow all")
	}
}

// --- Store methods ---

func TestStore_GetRole(t *testing.T) {
	s := NewStore(testConfig())
	role := s.GetRole("admin")
	if role == nil {
		t.Fatal("admin role should exist")
	}
	if role.Name != "admin" {
		t.Errorf("name = %q", role.Name)
	}
}

func TestStore_GetRoleUnknown(t *testing.T) {
	s := NewStore(testConfig())
	if s.GetRole("nonexistent") != nil {
		t.Error("nonexistent role should return nil")
	}
}

func TestStore_GetTeam(t *testing.T) {
	s := NewStore(testConfig())
	team := s.GetTeam("engineering")
	if team == nil {
		t.Fatal("engineering team should exist")
	}
	if team.Role != "member" {
		t.Errorf("team role = %q", team.Role)
	}
}

func TestStore_DefaultRole(t *testing.T) {
	s := NewStore(testConfig())
	if s.DefaultRole() != "member" {
		t.Errorf("default role = %q", s.DefaultRole())
	}
}

func TestStore_DefaultRoleFallback(t *testing.T) {
	s := NewStore(config.RBACConfig{})
	if s.DefaultRole() != "member" {
		t.Errorf("empty config default role = %q", s.DefaultRole())
	}
}

// --- Edge Cases ---

func TestStore_EmptyConfig(t *testing.T) {
	s := NewStore(config.RBACConfig{})
	role := s.ResolveRole("", "", "")
	if role != "member" {
		t.Errorf("empty config resolve = %q", role)
	}
	if s.HasPermission("member", "models:gpt-4o") {
		t.Error("no roles defined, should have no permissions")
	}
}

func TestMatchPermission_EmptyStrings(t *testing.T) {
	if matchPermission("", "models:gpt-4o") {
		t.Error("empty held permission should not match")
	}
	if matchPermission("models:gpt-4o", "") {
		t.Error("empty required should not match non-empty held")
	}
	if !matchPermission("", "") {
		t.Error("empty matches empty")
	}
}
