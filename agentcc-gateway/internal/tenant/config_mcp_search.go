package tenant

import "strings"

// MCPSearchConfig is an explicit per-org opt-in. CallsPerMinute is a shared
// admission bound covering BOTH index batches and queries, including failures.
// Every call also has a fixed input-byte bound; charges still use the ordinary
// embedding route's credit/BYOK accounting. It is not a dollar-price override.
type MCPSearchConfig struct {
	Enabled        bool   `json:"enabled,omitempty"`
	Model          string `json:"model,omitempty"`
	TopK           int    `json:"top_k,omitempty"`
	TimeoutMS      int    `json:"timeout_ms,omitempty"`
	CallsPerMinute int    `json:"calls_per_minute,omitempty"`
}

// MCPSearch fails closed on incomplete/invalid opt-ins. No operator-wide
// setting can silently enable paid search for an organization.
func (c *OrgConfig) MCPSearch() MCPSearchConfig {
	if c == nil || c.MCP == nil || c.MCP.Search == nil {
		return MCPSearchConfig{}
	}
	s := *c.MCP.Search
	if !s.Enabled || strings.TrimSpace(s.Model) == "" || len(s.Model) > 256 || s.CallsPerMinute < 1 || s.CallsPerMinute > 600 {
		return MCPSearchConfig{}
	}
	if s.TopK == 0 {
		s.TopK = 20
	}
	if s.TimeoutMS == 0 {
		s.TimeoutMS = 150
	}
	if s.TopK < 1 || s.TopK > 20 || s.TimeoutMS < 1 || s.TimeoutMS > 500 {
		return MCPSearchConfig{}
	}
	return s
}
