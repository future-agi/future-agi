package mcp

import (
	"context"
	"encoding/json"
	"errors"
	"net/http/httptest"
	"testing"
	"time"
)

// The admin's Try a tool on a tool of a server added from the connector
// catalog runs through that connector's own virtual server, which Django
// names. The gateway takes the id only when it is one of the org's own
// virtual servers and has the tool's server as a member, and then the
// connector's approvals and denied tools apply to the admin's call as they
// do to a key's. A tool of a server the org added itself is tried as before.

// vsForeign is the id of a virtual server only another org has.
const vsForeign = "vs_01M5FQ2V9W0KXH6ZJ3E4R7T8NB"

// approvalRequests is the approval store, counting the approvals it is asked
// to create; it holds none.
type approvalRequests struct{ created int }

func (s *approvalRequests) Create(context.Context, string, string, []byte, time.Duration) error {
	s.created++
	return nil
}
func (s *approvalRequests) Load(context.Context, string) ([]byte, bool, error) {
	return nil, false, nil
}
func (s *approvalRequests) CompareSwap(context.Context, string, []byte, []byte, time.Time) (bool, error) {
	return false, nil
}
func (s *approvalRequests) List(context.Context, string) ([][]byte, error) { return nil, nil }
func (s *approvalRequests) ListPage(context.Context, string, string, int) ([][]byte, string, error) {
	return nil, "", nil
}

// newConnectorTryToolFixture is the Try a tool fixture with the connector
// close added from the catalog next to the org's own github (connectorOrg,
// with access default accessDefault and the connector's tools.include
// include), close's upstream serving the connector's three tools, the
// approval manager the wiring installs on a store that counts what it is
// asked to create, and another org whose virtual server vsForeign has a
// server close too.
func newConnectorTryToolFixture(t *testing.T, accessDefault string, include []string) (*tryToolFixture, *fakeUpstream, *approvalRequests) {
	t.Helper()
	f := newTryToolFixture(t, newOrgConfig)
	up := newFakeUpstream(t, "/mcp", func(u *fakeUpstream) {
		u.tools = []json.RawMessage{fakeTool("create_lead"), fakeTool("delete_lead"), fakeTool("list_leads")}
	})
	client := connectFake(t, up, ClientConfig{ServerID: "close"})
	connector := PoolServer{ID: "close", Source: "org", Healthy: true, Tools: client.Tools()}
	pool := f.ten.pools["org-a"].(*w3bPool)
	pool.servers["close"] = connector
	pool.clients["close"] = AdaptClient(client)
	f.ten.cfgs["org-a"] = connectorOrg(t, accessDefault, "", include, nil)
	f.ten.cfgs["org-b"] = orgConfig(t, `{"mcp":{"enabled":true,`+
		`"servers":{"close":{"url":"https://close.example/mcp","catalog_ref":"close/close-mcp@1.0.0"}},`+
		`"virtual_servers":{"close":{"id":"`+vsForeign+`","members":["close"]}}}}`)
	f.ten.pools["org-b"] = &w3bPool{org: "org-b", servers: map[string]PoolServer{"close": connector}, clients: map[string]UpstreamClient{}}
	store := &approvalRequests{}
	codec, err := NewStateCodec([]string{"synthetic-trytool-state-material-not-a-secret"}, false)
	if err != nil {
		t.Fatal(err)
	}
	deps := *f.Deps()
	deps.Approvals = NewApprovalManager(store, codec, nil)
	f.SetDeps(deps)
	return f, up, store
}

// tryTool is the console's Try a tool on name in org-a, naming the virtual
// server vs ("" names none).
func (f *tryToolFixture) tryTool(t *testing.T, vs, name string) (TestToolResult, error) {
	t.Helper()
	req := httptest.NewRequest("POST", "/-/mcp/test", nil)
	req.Header.Set("Authorization", "Bearer the-admin-token")
	return f.AdminTestTool(req, "org-a", vs, name, json.RawMessage(`{}`))
}

func TestTryAToolRunsAConnectorToolThroughItsVirtualServer(t *testing.T) {
	for _, access := range []string{"", "deny", "allow"} {
		f, up, store := newConnectorTryToolFixture(t, access, nil)
		calls := func() int { return len(up.requests(MethodToolsCall)) }

		// A tool the connector serves with no approval runs.
		res, err := f.tryTool(t, vsConnector, "close__list_leads")
		if err != nil || res.Error != "" || res.Code != "" || res.ServerID != "close" || calls() != 1 {
			t.Fatalf("access %q: Try a tool on close__list_leads: %+v %v, %d upstream calls; want the tool's answer to one call", access, res, err, calls())
		}
		if len(res.Content) != 1 || res.Content[0].Text != "called list_leads" {
			t.Fatalf("access %q: content %+v, want the upstream's answer", access, res.Content)
		}

		// The tool the connector holds for an approval does not run, and
		// the admin's call asks for no approval: none is created, so none
		// waits under MCP > Approvals for this call.
		res, err = f.tryTool(t, vsConnector, "close__create_lead")
		if err != nil || res.Code != "approval_required" || res.IsError || calls() != 1 {
			t.Fatalf("access %q: Try a tool on close__create_lead: %+v %v, %d upstream calls; want approval_required and no call", access, res, err, calls())
		}
		if store.created != 0 {
			t.Fatalf("access %q: the admin's call created %d approvals; want none", access, store.created)
		}

		// The tool the connector's posture denies is not in its virtual
		// server, so it is unknown there and does not run.
		res, err = f.tryTool(t, vsConnector, "close__delete_lead")
		if err != nil || res.Code != "tool_not_found" || calls() != 1 {
			t.Fatalf("access %q: Try a tool on close__delete_lead: %+v %v, %d upstream calls; want tool_not_found and no call", access, res, err, calls())
		}
	}
}

// The connector's deny rule holds for the admin too: with a wider include,
// the denied tool is in the virtual server but its access rule refuses it,
// whether or not the org stored an access default.
func TestTryAToolKeepsAConnectorsDenyRule(t *testing.T) {
	for _, access := range []string{"", "allow"} {
		f, up, _ := newConnectorTryToolFixture(t, access, []string{"close/*"})
		res, err := f.tryTool(t, vsConnector, "close__delete_lead")
		if err != nil || res.Code != "tool_denied" || res.Rule != "access.rules[0]" || len(up.requests(MethodToolsCall)) != 0 {
			t.Fatalf("access %q: Try a tool on a denied tool: %+v %v, %d upstream calls; want tool_denied by access.rules[0] and no call", access, res, err, len(up.requests(MethodToolsCall)))
		}
	}
}

// A virtual server that is not the org's, or that does not have the tool's
// server as a member, is refused before any step of the chain runs.
func TestTryAToolChecksTheVirtualServerItIsGiven(t *testing.T) {
	f, up, _ := newConnectorTryToolFixture(t, "allow", nil)
	// desk is one of the org's virtual servers, with github only.
	f.ten.cfgs["org-a"] = connectorOrg(t, "allow", "", nil, []string{"github"})
	hooks := 0
	f.hookObserver = func(string, *CallContext) { hooks++ }

	for _, tc := range []struct{ vs, name string }{
		{"desk", "close__create_lead"},
		{"desk", "close__list_leads"},
		{vsConnector, "github__echo"},
	} {
		res, err := f.tryTool(t, tc.vs, tc.name)
		if err != nil || res.Error != "tool not found: "+tc.name || res.Code != "" {
			t.Fatalf("%s through %s: %+v %v; want tool not found", tc.name, tc.vs, res, err)
		}
	}
	if _, err := f.tryTool(t, vsForeign, "close__create_lead"); !errors.Is(err, ErrUnknownVirtualServer) {
		t.Fatalf("another org's virtual server: %v; want ErrUnknownVirtualServer", err)
	}
	if hooks != 0 || len(up.requests(MethodToolsCall)) != 0 || f.upstreamCalls() != 0 {
		t.Fatalf("a refused virtual server ran %d chain steps, %d connector and %d github upstream calls; want none", hooks, len(up.requests(MethodToolsCall)), f.upstreamCalls())
	}
}

// A tool of a server the org added itself is tried as before, with no
// virtual server named: it runs when the org stored no access default or
// allow, a stored deny refuses it, and a connector's tool is still not
// reached through the org's default view.
func TestTryAToolOnTheOrgsOwnServerIsUnchanged(t *testing.T) {
	for _, access := range []string{"", "allow"} {
		f, up, _ := newConnectorTryToolFixture(t, access, nil)
		res := f.adminRun(t, nil)
		if res.Error != "" || res.Code != "" || res.ServerID != "github" || f.upstreamCalls() != 1 {
			t.Fatalf("access %q: Try a tool on github__echo: %+v, %d upstream calls; want the tool's answer", access, res, f.upstreamCalls())
		}
		if len(res.Content) != 1 || res.Content[0].Text != "called echo" {
			t.Fatalf("access %q: content %+v, want the upstream's answer", access, res.Content)
		}
		res, err := f.tryTool(t, "", "close__list_leads")
		if err != nil || res.Code != "tool_not_found" || len(up.requests(MethodToolsCall)) != 0 {
			t.Fatalf("access %q: a connector's tool with no virtual server: %+v %v; want tool_not_found and no call", access, res, err)
		}
	}
	f, _, _ := newConnectorTryToolFixture(t, "deny", nil)
	res := f.adminRun(t, nil)
	if res.Code != "tool_denied" || res.Rule != ruleAccessDefault || f.upstreamCalls() != 0 {
		t.Fatalf("a stored deny: %+v, %d upstream calls; want tool_denied by access.default and no call", res, f.upstreamCalls())
	}
}
