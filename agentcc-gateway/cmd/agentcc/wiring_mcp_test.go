package main

import (
	"context"
	"encoding/json"
	"errors"
	"reflect"
	"strings"
	"sync/atomic"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/mcp"
	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/server"
)

// mcpTestStateKey is a fixed MCP state key in the server's state-key format;
// see internal/server's routes_mcp_test.go.
const mcpTestStateKey = "mcp-test-state-key-00000000000000000000000"

// denyAll is a ToolPolicy a wiring step installs in the test.
type denyAll struct{}

func (denyAll) Evaluate(*mcp.CallContext) mcp.PolicyDecision {
	return mcp.PolicyDecision{Effect: mcp.PolicyDeny, RuleID: "r1"}
}

// TestMCPWiringOrder checks wiring_mcp.go: the documented order of the MCP
// wiring inits, each name bound to its own init, each run once in order, and
// the hand-over to the MCP server after every afterServer callback.
func TestMCPWiringOrder(t *testing.T) {
	want := []struct {
		name string
		init func(*wiringDeps, *mcp.Deps) error
	}{
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
	if len(mcpWiringOrder) != len(want) {
		t.Fatalf("mcpWiringOrder has %d steps, want %d", len(mcpWiringOrder), len(want))
	}
	for i, w := range want {
		got := mcpWiringOrder[i]
		if got.name != w.name || reflect.ValueOf(got.init).Pointer() != reflect.ValueOf(w.init).Pointer() {
			t.Errorf("mcpWiringOrder[%d] = %s, want %s bound to its own init", i, got.name, w.name)
		}
	}
	// The steps from keylimits to budget run right after billing, in their documented order.
	var names []string
	for _, s := range mcpWiringOrder {
		names = append(names, s.name)
	}
	if !strings.Contains(strings.Join(names, ","), "billing,keylimits,runguard,delegation,policy,flow,shape,toolstats,builtin,vendors,memory,terms,budget") {
		t.Fatalf("the steps from keylimits to budget do not run right after billing: %v", names)
	}
	if last := wiringOrder[len(wiringOrder)-1]; last.name != "mcp" || reflect.ValueOf(last.init).Pointer() != reflect.ValueOf(initMCP).Pointer() {
		t.Fatalf("the last wiringOrder step is %s, want mcp after privacy", last.name)
	}

	t.Run("each init once, in order, then SetDeps after every callback", func(t *testing.T) {
		t.Setenv("AGENTCC_MCP_STATE_KEYS", mcpTestStateKey)
		var calls []string
		steps := make([]mcpWiringStep, len(mcpWiringOrder))
		for i, s := range mcpWiringOrder {
			steps[i] = mcpWiringStep{s.name, func(d *wiringDeps, deps *mcp.Deps) error {
				calls = append(calls, "init:"+s.name)
				if err := s.init(d, deps); err != nil {
					return err
				}
				d.afterServer(func() error {
					calls = append(calls, "after:"+s.name)
					if s.name == "policy" {
						// Installed in a callback: SetDeps must run after it.
						deps.ToolPolicy = denyAll{}
					}
					return nil
				})
				return nil
			}}
		}
		srv := newWiringServer(t, true)
		d := &wiringDeps{}
		earlier := false
		d.afterServer(func() error { earlier = true; return nil }) // an earlier wiring step's callback
		if err := runMCPWiring(d, steps); err != nil {
			t.Fatalf("runMCPWiring: %v", err)
		}
		var wantCalls []string
		for _, w := range want {
			wantCalls = append(wantCalls, "init:"+w.name)
		}
		if strings.Join(calls, ",") != strings.Join(wantCalls, ",") {
			t.Fatalf("inits ran %v, want %v", calls, wantCalls)
		}
		if _, ok := srv.MCP().Deps().ToolPolicy.(denyAll); ok {
			t.Fatal("the deps reached the server before the server wiring ran")
		}
		d.server = srv
		if err := d.startServerWiring(); err != nil {
			t.Fatalf("startServerWiring: %v", err)
		}
		if !earlier {
			t.Fatal("an earlier step's callback did not run")
		}
		if got := srv.MCP().Deps(); got.ToolPolicy == nil {
			t.Fatal("no deps handed over")
		} else if _, ok := got.ToolPolicy.(denyAll); !ok {
			t.Fatalf("ToolPolicy = %T: SetDeps ran before the callback that installed it", got.ToolPolicy)
		}
		if srv.MCP().Deps().Clock == nil || srv.MCP().Deps().Builtins == nil {
			t.Fatal("members nobody set lost their defaults")
		}
	})

	t.Run("a step's error stops the MCP wiring and names it", func(t *testing.T) {
		boom := errors.New("boom")
		var ran []string
		steps := []mcpWiringStep{
			{"a", func(*wiringDeps, *mcp.Deps) error { ran = append(ran, "a"); return nil }},
			{"b", func(*wiringDeps, *mcp.Deps) error { ran = append(ran, "b"); return boom }},
			{"c", func(*wiringDeps, *mcp.Deps) error { ran = append(ran, "c"); return nil }},
		}
		err := runMCPWiring(&wiringDeps{}, steps)
		if !errors.Is(err, boom) || err.Error() != "mcp b: boom" || strings.Join(ran, ",") != "a,b" {
			t.Fatalf("err = %v, ran %v; want \"mcp b: boom\" after a,b", err, ran)
		}
	})

	t.Run("MCP disabled: nothing to hand over", func(t *testing.T) {
		d := &wiringDeps{server: newWiringServer(t, false)}
		if err := initMCP(d); err != nil {
			t.Fatalf("initMCP: %v", err)
		}
		if err := d.startServerWiring(); err != nil {
			t.Fatalf("startServerWiring: %v", err)
		}
	})

	t.Run("built-in servers registered at init time survive the hand-over", func(t *testing.T) {
		t.Setenv("AGENTCC_MCP_STATE_KEYS", mcpTestStateKey)
		srv := newWiringServer(t, true)
		d := &wiringDeps{}
		steps := []mcpWiringStep{{"builtin", func(_ *wiringDeps, deps *mcp.Deps) error {
			return deps.Builtins.Register("fagi.test", fakeBuiltin{})
		}}}
		if err := runMCPWiring(d, steps); err != nil {
			t.Fatalf("runMCPWiring: %v", err)
		}
		d.server = srv
		if err := d.startServerWiring(); err != nil {
			t.Fatalf("startServerWiring: %v", err)
		}
		if _, ok := srv.MCP().Deps().Builtins.Get("fagi.test"); !ok {
			t.Fatal("the built-in server registered at init time is gone after the hand-over")
		}

		bad := []mcpWiringStep{{"builtin", func(_ *wiringDeps, deps *mcp.Deps) error {
			return deps.Builtins.Register("fagi.test", nil)
		}}}
		if err := runMCPWiring(&wiringDeps{}, bad); err == nil || !strings.HasPrefix(err.Error(), "mcp builtin: ") {
			t.Fatalf("a nil built-in server: err = %v, want the builtin step to fail", err)
		}
	})
}

// fakeBuiltin is an in-process server that answers nothing.
type fakeBuiltin struct{}

func (fakeBuiltin) ListTools(context.Context) ([]json.RawMessage, error) { return nil, nil }
func (fakeBuiltin) CallTool(context.Context, string, json.RawMessage, mcp.CallOptions) (json.RawMessage, error) {
	return nil, nil
}
func (fakeBuiltin) ListResources(context.Context) ([]json.RawMessage, error) { return nil, nil }
func (fakeBuiltin) ReadResource(context.Context, string, mcp.CallOptions) (json.RawMessage, error) {
	return nil, nil
}
func (fakeBuiltin) ListPrompts(context.Context) ([]json.RawMessage, error) { return nil, nil }
func (fakeBuiltin) GetPrompt(context.Context, string, json.RawMessage, mcp.CallOptions) (json.RawMessage, error) {
	return nil, nil
}
func (fakeBuiltin) Healthy() bool { return true }
func (fakeBuiltin) Close() error  { return nil }

// newWiringServer builds a gateway with MCP on or off.
func newWiringServer(t *testing.T, mcpEnabled bool) *server.Server {
	t.Helper()
	cfg := config.DefaultConfig()
	cfg.MCP.Enabled = mcpEnabled
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("creating registry: %v", err)
	}
	var mdb atomic.Pointer[modeldb.ModelDB]
	mdb.Store(modeldb.New(modeldb.BundledModels, nil))
	srv := server.New(cfg, "", registry, pipeline.NewEngine(), nil, nil, nil, nil, &mdb, nil, nil)
	t.Cleanup(func() {
		if m := srv.MCP(); m != nil {
			m.Close()
		}
	})
	return srv
}
