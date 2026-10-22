package main

import (
	"fmt"

	"github.com/futureagi/agentcc-gateway/internal/mcp"
)

// mcpWiringStep is one wiring_mcp_<concern>.go file's init: it may set the
// mcp.Deps members its concern provides, and register work that needs the
// running server with d.afterServer.
type mcpWiringStep struct {
	name string
	init func(d *wiringDeps, deps *mcp.Deps) error
}

// mcpWiringOrder is the order initMCP runs the MCP wiring inits in. Each
// concern has its own wiring_mcp_<concern>.go file; none needs a wiringOrder
// entry. keylimits runs right after billing, and TestMCPWiringOrder pins the
// whole order.
var mcpWiringOrder = []mcpWiringStep{
	{"identity", initMCPIdentity},
	{"tenancy", initMCPTenancy},
	{"vserver", initMCPVServer},
	{"protocol", initMCPProtocol},
	{"sessions", initMCPSessions},
	{"listen", initMCPListen},
	{"upstream", initMCPUpstream},
	{"meter", initMCPMeter},
	{"probe", initMCPProbe},
	{"billing", initMCPBilling},
	{"keylimits", initMCPKeyLimits},
	{"runguard", initMCPRunGuard},
	{"delegation", initMCPDelegation},
	{"policy", initMCPPolicy},
	{"flow", initMCPFlow},
	{"shape", initMCPShape},
	{"toolstats", initMCPToolStats},
	{"builtin", initMCPBuiltin},
	{"vendors", initMCPVendors},
	{"memory", initMCPMemory},
	{"terms", initMCPTerms},
	{"budget", initMCPBudget},
	{"pins", initMCPPins},
	{"scan", initMCPScan},
	{"oauth", initMCPOAuth},
	{"credentials", initMCPCredentials},
	{"approvals", initMCPApprovals},
	{"telemetry", initMCPTelemetry},
	{"translate", initMCPTranslate},
	{"toolsets", initMCPToolsets},
	{"validate", initMCPValidate},
	{"llmjoin", initMCPLLMJoin},
}

// initMCP is the mcp step of wiringOrder. It runs each MCP wiring init once,
// in mcpWiringOrder, on one shared mcp.Deps, then registers its own
// afterServer callback, which runs after every callback registered before it
// (the earlier wiring steps' and the MCP inits') and hands the result to the
// MCP server with SetDeps, the SetInjectedDeps pattern. Members nobody
// set keep their safe defaults. Nothing is handed over when MCP is disabled.
func initMCP(d *wiringDeps) error {
	return runMCPWiring(d, mcpWiringOrder)
}

func runMCPWiring(d *wiringDeps, steps []mcpWiringStep) error {
	deps := &mcp.Deps{Builtins: mcp.NewBuiltinRegistry()}
	for _, st := range steps {
		if err := st.init(d, deps); err != nil {
			return fmt.Errorf("mcp %s: %w", st.name, err)
		}
	}
	d.afterServer(func() error {
		if d.server == nil {
			return nil
		}
		if srv := d.server.MCP(); srv != nil {
			srv.SetDeps(*deps)
		}
		return nil
	})
	return nil
}
