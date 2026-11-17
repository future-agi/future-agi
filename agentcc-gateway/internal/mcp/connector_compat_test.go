package mcp

import (
	"context"
	"encoding/json"
	"sort"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// A server added from the connector catalog carries catalog_ref, and the
// catalog add writes it a virtual server of its own that holds the
// connector's posture: the tools that run after an approval (approval
// require listed) and the ones it denies (left out of tools.include and
// denied by an access rule). The compatibility view, what a caller that
// names no virtual server is served when its key is bound to none and the
// org has no default one, leaves every catalog_ref server out, so an access
// default of allow never reaches a connector's tools around that posture.

// vsConnector is the id of the connector's own virtual server.
const vsConnector = "vs_01M4ZEB9E0J3AADR85Z4T4429Y"

// connectorTools are the connector's tools: one that runs, one that runs
// after an approval, and one its posture denies.
var connectorTools = []string{"close__list_leads", "close__create_lead", "close__delete_lead"}

// connectorOrg is an org with its own server github and the connector close
// added from the catalog, with close's virtual server as the catalog add
// writes it. accessDefault is mcp.access.default ("" stores none), bindKey a
// key listed on the connector's virtual server ("" lists none), include the
// connector's tools.include (nil is the catalog add's), and orgDefault the
// members of a default_for_org virtual server (nil adds none).
func connectorOrg(t *testing.T, accessDefault, bindKey string, include, orgDefault []string) *tenant.OrgConfig {
	t.Helper()
	if include == nil {
		include = []string{"close/create_lead", "close/list_leads"}
	}
	access := map[string]any{"rules": []any{map[string]any{"match": map[string]any{}, "deny": []string{"close/delete_lead"}}}}
	if bindKey != "" {
		access["keys"] = []string{bindKey}
	}
	connector := map[string]any{"id": vsConnector, "title": "Close", "members": []string{"close"}, "access": access}
	connector["tools"] = map[string]any{"include": include}
	connector["approval"] = map[string]any{"require": "listed", "tools": []string{"close/create_lead"}, "validity": "once"}
	virtualServers := map[string]any{"close": connector}
	if orgDefault != nil {
		virtualServers["desk"] = map[string]any{"members": orgDefault, "access": map[string]any{"default_for_org": true}}
	}
	servers := map[string]any{"github": map[string]any{"url": "https://github.example/mcp"}}
	servers["close"] = map[string]any{"url": "https://close.example/mcp", "catalog_ref": "close/close-mcp@1.0.0"}
	section := map[string]any{"servers": servers, "virtual_servers": virtualServers}
	if accessDefault != "" {
		section["access"] = map[string]any{"default": accessDefault}
	}
	raw, err := json.Marshal(map[string]any{"mcp": section})
	if err != nil {
		t.Fatal(err)
	}
	return orgConfig(t, string(raw))
}

// connectorApprovals stands in for the approval store: a call waits for an
// approval exactly when its virtual server's policy requires one.
type connectorApprovals struct{}

func (connectorApprovals) Check(cc *CallContext) *Denial {
	_, required, err := approvalPolicy(cc)
	if err != nil {
		return approvalUnavailable()
	}
	if required {
		return approvalRequired(cc, ApprovalRecord{ID: "synthetic-approval", Status: "pending"})
	}
	return nil
}

// newConnectorFixture serves github and close from the registry, with the
// approval stand-in, and returns the run steps (the approval check and the
// upstream call) each tools/call reached, by the tool name it called.
func newConnectorFixture(t *testing.T) (*vsFixture, map[string][]string) {
	t.Helper()
	f := newVSFixture(t, "")
	registerTools(f.Server, "github", "list_repos")
	registerTools(f.Server, "close", "create_lead", "delete_lead", "list_leads")
	deps := *f.Deps()
	deps.Approvals = connectorApprovals{}
	f.SetDeps(deps)
	reached := map[string][]string{}
	f.hookObserver = func(hook string, cc *CallContext) {
		if hook == hookCheckApproval || hook == hookCallUpstream {
			reached[cc.Tool.ExposedName] = append(reached[cc.Tool.ExposedName], hook)
		}
	}
	return f, reached
}

// listedNames is what tools/list answers, sorted and joined by commas.
func listedNames(t *testing.T, f *vsFixture, vsID string) string {
	t.Helper()
	names := f.toolNames(t, vsID, nil)
	sort.Strings(names)
	return strings.Join(names, ",")
}

func requireApprovalRequired(t *testing.T, resp *Message, name string) {
	t.Helper()
	if d := denial(t, resp); d == nil || d.Code != "approval_required" {
		t.Fatalf("calling %s answered %s %+v; want an in-band approval_required", name, resp.Result, d)
	}
}

// requireNotRun checks that no call of these tools reached the approval
// check or the upstream call.
func requireNotRun(t *testing.T, reached map[string][]string, names ...string) {
	t.Helper()
	for _, name := range names {
		if steps := reached[name]; len(steps) > 0 {
			t.Fatalf("calling %s reached %v; a tool the caller cannot see must not run", name, steps)
		}
	}
}

func TestCompatibilityViewLeavesOutCatalogConnectors(t *testing.T) {
	f, reached := newConnectorFixture(t)

	// A key bound to no virtual server, under access.default allow, with no
	// default_for_org virtual server: the org's own server, and no tool of
	// the connector, listed or called.
	f.cfg = connectorOrg(t, "allow", "", nil, nil)
	if got := listedNames(t, f, ""); got != "github__list_repos" {
		t.Fatalf("an unbound key lists %s under access.default allow; want only github__list_repos, no connector tool", got)
	}
	for _, name := range connectorTools {
		requireUnknownTool(t, f.callResult(t, "", name, nil), name)
	}
	requireNotRun(t, reached, connectorTools...)
	f.callResult(t, "", "github__list_repos", nil) // fails upstream (no client), after the approval step
	if len(reached["github__list_repos"]) == 0 {
		t.Fatal("the org's own tool did not pass the compatibility view under access.default allow")
	}

	// A key bound to the connector's virtual server is served it, with its
	// approval requirement and without the tool its posture denies.
	f.cfg = connectorOrg(t, "allow", f.principal.KeyID, nil, nil)
	if got := listedNames(t, f, ""); got != "close__create_lead,close__list_leads" {
		t.Fatalf("a key bound to the connector's virtual server lists %s; want close__create_lead,close__list_leads", got)
	}
	requireApprovalRequired(t, f.callResult(t, "", "close__create_lead", nil), "close__create_lead")

	// A deny default, stored or absent, refuses the view as before.
	for _, def := range []string{"deny", ""} {
		f.cfg = connectorOrg(t, def, "", nil, nil)
		if got := listedNames(t, f, ""); got != "" {
			t.Fatalf("access.default %q lists %s; want nothing", def, got)
		}
		requireDenied(t, f.callResult(t, "", "github__list_repos", nil), "access.default", "")
		requireUnknownTool(t, f.callResult(t, "", "close__create_lead", nil), "close__create_lead")
	}

	// An org default virtual server is served as before, with every tool of
	// the members it names.
	f.cfg = connectorOrg(t, "allow", "", nil, []string{"close", "github"})
	if got := listedNames(t, f, ""); got != "close__create_lead,close__delete_lead,close__list_leads,github__list_repos" {
		t.Fatalf("an unbound key lists %s through the org default virtual server; want every tool of its members", got)
	}
}

// TestConnectorPostureHeldOutsideCompatibilityView pins the boundary for an
// org that already has access.default allow and a connector, with no config
// rewrite: the connector's tool that needs an approval and the one its
// posture denies are neither listed nor run through the compatibility view,
// and a tools/call naming either is refused like an unknown tool, before any
// approval or upstream step. The connector's own virtual server still holds
// the first for an approval and refuses the second. A change that puts
// catalog_ref servers back in the compatibility view fails here.
func TestConnectorPostureHeldOutsideCompatibilityView(t *testing.T) {
	f, reached := newConnectorFixture(t)
	f.cfg = connectorOrg(t, "allow", "", nil, nil)
	approval, denied := "close__create_lead", "close__delete_lead"

	shown := "," + listedNames(t, f, "") + ","
	for _, name := range []string{approval, denied} {
		if strings.Contains(shown, ","+name+",") {
			t.Fatalf("the compatibility view lists %s (%s)", name, strings.Trim(shown, ","))
		}
		requireUnknownTool(t, f.callResult(t, "", name, nil), name)
	}
	requireNotRun(t, reached, approval, denied)

	// The connector's own virtual server, open to the org's principals as
	// the catalog add writes it.
	if got := listedNames(t, f, vsConnector); got != "close__create_lead,close__list_leads" {
		t.Fatalf("the connector's virtual server lists %s; want close__create_lead,close__list_leads", got)
	}
	requireApprovalRequired(t, f.callResult(t, vsConnector, approval, nil), approval)
	if steps := reached[approval]; len(steps) != 1 || steps[0] != hookCheckApproval {
		t.Fatalf("calling %s through its virtual server reached %v; want the approval check and no upstream call", approval, steps)
	}
	requireUnknownTool(t, f.callResult(t, vsConnector, denied, nil), denied)
	requireNotRun(t, reached, denied)

	// A wider include on that virtual server never admits the denied tool:
	// its access rule refuses it in band.
	f.cfg = connectorOrg(t, "allow", "", []string{"close/*"}, nil)
	requireDenied(t, f.callResult(t, vsConnector, denied, nil), "access.rules[0]", vsConnector)
	requireNotRun(t, reached, denied)
}

// The admin's Try a tool names no virtual server, so it is served the
// compatibility view too: its exemption from an access default the org did
// not store, and an access default of allow, never reach a connector's tool
// there. The served-tools preview of that view lists none either.
func TestAdminTryToolDoesNotReachAConnectorThroughTheCompatibilityView(t *testing.T) {
	for _, access := range []string{"", `"access":{"default":"allow"},`} {
		f := newTryToolFixture(t, `{"mcp":{"enabled":true,`+access+
			`"servers":{"github":{"url":"https://org.example/mcp","catalog_ref":"github/remote@1.0.0"}},`+
			`"virtual_servers":{"github":{"id":"`+vsConnector+`","members":["github"]}}}}`)

		res := f.adminRun(t, nil)
		if res.Code != "tool_not_found" || f.upstreamCalls() != 0 {
			t.Fatalf("access %q: Try a tool on a connector's tool: %+v, %d upstream calls; want tool_not_found and no call", access, res, f.upstreamCalls())
		}
		call := &Message{JSONRPC: "2.0", ID: json.RawMessage(`3`), Method: MethodToolsCall, Params: json.RawMessage(`{"name":"github__echo","arguments":{}}`)}
		requireUnknownTool(t, decodeResponse(t, postMCP(t, f.Server, call, f.key)), "github__echo")
		served, err := f.AdminServedTools(context.Background(), "org-a", "", "")
		if err != nil || len(served.Tools) != 0 {
			t.Fatalf("access %q: served tools %+v %v; want none through the compatibility view", access, served, err)
		}
		if f.upstreamCalls() != 0 {
			t.Fatalf("access %q: a connector's tool reached the upstream (%d calls)", access, f.upstreamCalls())
		}
	}
}
