package server

import (
	"errors"

	gatewayadmin "github.com/futureagi/agentcc-gateway/internal/contracts/generated"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// searchOrgContract is the additive, gateway-owned search extension. Embed the
// generated contract instead of decoding the entire runtime config: all other
// fields retain the canonical types and DisallowUnknownFields enforcement.
// The shared schema/generators belong to the platform lane; they can adopt
// this one field without a hand edit to generated files in this change.
type searchOrgContract struct {
	gatewayadmin.OrgConfig
	MCP *searchMCPContract `json:"mcp,omitempty"`
}
type searchMCPContract struct {
	gatewayadmin.MCPOrgConfig
	Search *tenant.MCPSearchConfig `json:"search,omitempty"`
}

func (c *searchOrgContract) runtime() (*tenant.OrgConfig, error) {
	if c == nil {
		return nil, nil
	}
	base := c.OrgConfig
	if c.MCP != nil {
		base.MCP = &c.MCP.MCPOrgConfig
	}
	out, err := gatewayContractOrgConfigToTenant(&base)
	if err != nil {
		return nil, err
	}
	if c.MCP != nil && c.MCP.Search != nil {
		out.MCP.Search = c.MCP.Search
		if c.MCP.Search.Enabled && !out.MCPSearch().Enabled {
			return nil, errors.New("mcp.search requires a model, calls_per_minute 1..600, top_k 1..20 and timeout_ms 1..500 (zero top_k/timeout_ms use defaults)")
		}
	}
	return out, nil
}
func searchOrgContractsRuntime(in map[string]*searchOrgContract) (map[string]*tenant.OrgConfig, error) {
	out := make(map[string]*tenant.OrgConfig, len(in))
	for org, c := range in {
		cfg, err := c.runtime()
		if err != nil {
			return nil, err
		}
		out[org] = cfg
	}
	return out, nil
}
