package vserver

import (
	"strconv"

	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// Principal is who an access rule matches: the caller's key, the agent it
// says it runs as, and its user when it has one.
type Principal struct {
	KeyID   string
	AgentID string
	UserID  string
}

// Usable reports whether p may address the virtual server. One that names
// no keys, agents or users is open to every principal of its org, as is the
// org's default; otherwise p must be named in one of the lists.
func Usable(a tenant.MCPVirtualServerAccess, p Principal) bool {
	if a.DefaultForOrg != nil && *a.DefaultForOrg {
		return true
	}
	if len(a.Keys) == 0 && len(a.Agents) == 0 && len(a.Users) == 0 {
		return true
	}
	return listed(a.Keys, p.KeyID) || listed(a.Agents, p.AgentID) || listed(a.Users, p.UserID)
}

// RuleID names access.rules[i] in a tool_denied answer's metadata.rule.
func RuleID(i int) string { return "access.rules[" + strconv.Itoa(i) + "]" }

// DeniedByRules returns the id of the first access rule matching p that
// refuses a tool, or "" when every matching rule lets it through. A rule's
// allow list is matched against allowNames and its deny list against
// denyNames, so a caller can keep names an override chose out of the allow
// side. Each rule only narrows, so a deny always wins.
func DeniedByRules(rules []*tenant.MCPAccessRule, p Principal, allowNames, denyNames []string) string {
	for i, r := range rules {
		if r == nil || !matches(r.Match, p) {
			continue
		}
		if len(r.Allow) > 0 && !Any(r.Allow, allowNames...) {
			return RuleID(i)
		}
		if Any(r.Deny, denyNames...) {
			return RuleID(i)
		}
	}
	return ""
}

// Outside reports whether a narrowing glob list leaves the tool out: the list
// is set and no glob matches any of names. An unset list narrows nothing.
func Outside(globs []string, names ...string) bool {
	return len(globs) > 0 && !Any(globs, names...)
}

// matches reports whether every criterion the rule sets matches p: a list
// matches when it names p's id. A rule with no criteria matches everyone.
func matches(m *tenant.MCPAccessRuleMatch, p Principal) bool {
	if m == nil {
		return true
	}
	if len(m.KeyIDs) > 0 && !listed(m.KeyIDs, p.KeyID) {
		return false
	}
	if len(m.AgentIDs) > 0 && !listed(m.AgentIDs, p.AgentID) {
		return false
	}
	if len(m.UserIDs) > 0 && !listed(m.UserIDs, p.UserID) {
		return false
	}
	return true
}

func listed(list []string, id string) bool {
	if id == "" {
		return false
	}
	for _, v := range list {
		if v == id {
			return true
		}
	}
	return false
}
