package rbac

import (
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

// Permission represents a hierarchical permission string like "models:gpt-4o".
type Permission string

// Role defines a named role with a set of permissions.
type Role struct {
	Name        string
	Permissions []Permission
}

// TeamConfig defines team-level RBAC settings.
type TeamConfig struct {
	Role    string
	Models  []string
	Members map[string]MemberConfig
}

// MemberConfig defines a user-level role override within a team.
type MemberConfig struct {
	Role string
}

// Store manages roles, teams, and permission resolution.
// Immutable after initialization — no mutex needed.
type Store struct {
	roles       map[string]*Role
	teams       map[string]*TeamConfig
	defaultRole string
}

// NewStore creates an RBAC store from config.
func NewStore(cfg config.RBACConfig) *Store {
	s := &Store{
		roles:       make(map[string]*Role),
		teams:       make(map[string]*TeamConfig),
		defaultRole: cfg.DefaultRole,
	}

	if s.defaultRole == "" {
		s.defaultRole = "member"
	}

	for name, roleCfg := range cfg.Roles {
		perms := make([]Permission, len(roleCfg.Permissions))
		for i, p := range roleCfg.Permissions {
			perms[i] = Permission(p)
		}
		s.roles[name] = &Role{Name: name, Permissions: perms}
	}

	for name, teamCfg := range cfg.Teams {
		tc := &TeamConfig{
			Role:    teamCfg.Role,
			Models:  teamCfg.Models,
			Members: make(map[string]MemberConfig),
		}
		for memberName, memberCfg := range teamCfg.Members {
			tc.Members[memberName] = MemberConfig{Role: memberCfg.Role}
		}
		s.teams[name] = tc
	}

	return s
}

// ResolveRole determines the effective role for a user.
// Priority: user-level → key-level → team-level → default.
func (s *Store) ResolveRole(team, userID, keyRole string) string {
	// 1. User-level role from team membership.
	if team != "" {
		if tc, ok := s.teams[team]; ok {
			if userID != "" {
				if mc, ok := tc.Members[userID]; ok && mc.Role != "" {
					return mc.Role
				}
			}
		}
	}

	// 2. Key-level role.
	if keyRole != "" {
		return keyRole
	}

	// 3. Team-level role.
	if team != "" {
		if tc, ok := s.teams[team]; ok && tc.Role != "" {
			return tc.Role
		}
	}

	// 4. Default role.
	return s.defaultRole
}

// HasPermission checks if a role has the required permission.
func (s *Store) HasPermission(roleName string, required Permission) bool {
	role, ok := s.roles[roleName]
	if !ok {
		return false
	}

	for _, perm := range role.Permissions {
		if matchPermission(perm, required) {
			return true
		}
	}
	return false
}

// GetRole returns a role by name.
func (s *Store) GetRole(name string) *Role {
	return s.roles[name]
}

// GetTeam returns a team config by name.
func (s *Store) GetTeam(name string) *TeamConfig {
	return s.teams[name]
}

// DefaultRole returns the default role name.
func (s *Store) DefaultRole() string {
	return s.defaultRole
}

// TeamModelAllowed checks if a team has explicit model restrictions
// and whether the given model is in the allowed list.
func (s *Store) TeamModelAllowed(team, model string) bool {
	tc, ok := s.teams[team]
	if !ok {
		return true // No team config = no restriction.
	}
	if len(tc.Models) == 0 {
		return true // No model restrictions.
	}
	for _, m := range tc.Models {
		if m == "*" || m == model {
			return true
		}
		// Wildcard prefix match: "gpt-*" matches "gpt-4o".
		if strings.HasSuffix(m, "*") {
			prefix := m[:len(m)-1]
			if strings.HasPrefix(model, prefix) {
				return true
			}
		}
	}
	return false
}

// matchPermission checks if a held permission matches a required permission.
// Supports wildcards: "*" matches all, "models:*" matches "models:gpt-4o",
// "models:gpt-*" matches "models:gpt-4o".
func matchPermission(held, required Permission) bool {
	h := string(held)
	r := string(required)

	// Global wildcard.
	if h == "*" {
		return true
	}

	// Exact match.
	if h == r {
		return true
	}

	// Wildcard at end: "models:*" matches "models:gpt-4o".
	if strings.HasSuffix(h, "*") {
		prefix := h[:len(h)-1]
		return strings.HasPrefix(r, prefix)
	}

	return false
}
