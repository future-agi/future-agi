package routing

import (
	"fmt"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

// modelPattern is a compiled pattern for matching model names.
type modelPattern struct {
	literal string // exact match (empty if wildcard)
	prefix  string // prefix for wildcard match
	isWild  bool   // true if pattern ends with *
}

// compiledGroup is a pre-compiled access group.
type compiledGroup struct {
	name        string
	patterns    []modelPattern
	matchAll    bool
	description string
	aliases     map[string]string
}

// AccessGroupChecker validates model access against groups assigned to API keys.
// Immutable after creation — safe for concurrent use.
type AccessGroupChecker struct {
	groups map[string]*compiledGroup
}

// NewAccessGroupChecker creates a checker from config.
func NewAccessGroupChecker(cfg config.AccessGroupsConfig) *AccessGroupChecker {
	if len(cfg) == 0 {
		return nil
	}

	groups := make(map[string]*compiledGroup, len(cfg))
	for name, gcfg := range cfg {
		cg := &compiledGroup{
			name:        name,
			description: gcfg.Description,
			aliases:     gcfg.Aliases,
		}
		for _, pattern := range gcfg.Models {
			if pattern == "*" {
				cg.matchAll = true
				continue
			}
			if strings.HasSuffix(pattern, "*") {
				cg.patterns = append(cg.patterns, modelPattern{
					prefix: pattern[:len(pattern)-1],
					isWild: true,
				})
			} else {
				cg.patterns = append(cg.patterns, modelPattern{
					literal: pattern,
				})
			}
		}
		groups[name] = cg
	}

	return &AccessGroupChecker{groups: groups}
}

// IsEnabled returns true if access groups are configured.
func (c *AccessGroupChecker) IsEnabled() bool {
	return c != nil && len(c.groups) > 0
}

// Check validates if a model is accessible by the given key groups.
// Returns the matched group name and whether access is allowed.
// If keyGroups is empty, access is unrestricted (backward compatible).
func (c *AccessGroupChecker) Check(model string, keyGroups []string) (string, bool) {
	if c == nil || len(c.groups) == 0 {
		return "", true
	}
	if len(keyGroups) == 0 {
		return "", true
	}

	for _, groupName := range keyGroups {
		group, ok := c.groups[groupName]
		if !ok {
			continue
		}
		if group.matchAll {
			return groupName, true
		}
		for _, p := range group.patterns {
			if !p.isWild {
				if model == p.literal {
					return groupName, true
				}
			} else {
				if strings.HasPrefix(model, p.prefix) {
					return groupName, true
				}
			}
		}
	}
	return "", false
}

// ResolveAlias checks if the model name is an alias in any of the key's groups.
// Returns the resolved model name or the original if no alias matches.
func (c *AccessGroupChecker) ResolveAlias(model string, keyGroups []string) string {
	if c == nil {
		return model
	}
	for _, groupName := range keyGroups {
		group, ok := c.groups[groupName]
		if !ok {
			continue
		}
		if target, ok := group.aliases[model]; ok {
			return target
		}
	}
	return model
}

// DescribeAllowed returns a human-readable description of available models.
func (c *AccessGroupChecker) DescribeAllowed(keyGroups []string) string {
	if c == nil || len(keyGroups) == 0 {
		return "No access groups assigned."
	}

	var parts []string
	for _, groupName := range keyGroups {
		group, ok := c.groups[groupName]
		if !ok {
			continue
		}
		desc := groupName
		if group.description != "" {
			desc = fmt.Sprintf("%s (%s)", groupName, group.description)
		}
		var models []string
		if group.matchAll {
			models = append(models, "*")
		}
		for _, p := range group.patterns {
			if p.isWild {
				models = append(models, p.prefix+"*")
			} else {
				models = append(models, p.literal)
			}
		}
		if len(models) > 0 {
			desc += ": " + strings.Join(models, ", ")
		}
		parts = append(parts, desc)
	}

	if len(parts) == 0 {
		return "No valid access groups found."
	}
	return "Available groups: " + strings.Join(parts, "; ")
}
