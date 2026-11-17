package vendors_test

import (
	"bytes"
	"encoding/json"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/connectorcatalog/manifest"
	"github.com/futureagi/agentcc-gateway/internal/mcp"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// A tools/call that names a tool the gateway does not list, on a server the
// organization added from the catalogue while the gateway does not serve it,
// is refused as every tool of that server is: isError, policy_unavailable, no
// connect link, and no token fetched. Only the exact "<server id>__" prefix of
// a server in the caller's own configuration counts; every other unknown name
// is still JSON-RPC -32602 "Unknown tool".

// unlisted is a tool name no server lists.
const unlisted = "__unlisted_tool"

// The refusal's plain sentences, the words the control plane answers with:
// Microsoft's apps, and any other app the gateway does not serve yet.
const (
	microsoftComingSoon = "Microsoft connections are not available in this release."
	appComingSoon       = "Connections to this app are not available in this release."
)

// heldServers: Outlook (a Microsoft app) and Gmail, added from the catalogue,
// and a server added by URL whose id starts with "gmail".
func heldServers(f *hostedFixture) map[string]any {
	return map[string]any{
		"outlook":    pilotEntry(f.runtime+"/microsoft-outlook/mcp", "microsoft-outlook", "1.0.0"),
		"gmail":      pilotEntry(f.runtime+"/gmail/mcp", "gmail", "1.0.0"),
		"gmail-team": map[string]any{"url": f.plainURL, "credential": map[string]any{"mode": "per_user"}},
	}
}

// notServed takes the connection enablement off each named app's FutureAGI
// provider, as the records ship while the gateway does not serve them.
func notServed(apps ...string) func(*hostedFixture) {
	return func(f *hostedFixture) {
		held := map[string]bool{}
		for _, app := range apps {
			held[app] = true
		}
		out := *f.catalog
		out.Apps = make([]*manifest.App, 0, len(f.catalog.Apps))
		for _, app := range f.catalog.Apps {
			a := *app
			if held[app.ID] {
				a.Providers = make([]*manifest.Provider, 0, len(app.Providers))
				for _, provider := range app.Providers {
					p := *provider
					if p.ID == "futureagi-rest" && p.XGateway != nil {
						gateway := *p.XGateway
						gateway.ConnectionEnablement = nil
						p.XGateway = &gateway
					}
					a.Providers = append(a.Providers, &p)
				}
			}
			out.Apps = append(out.Apps, &a)
		}
		f.catalog = &out
	}
}

// withVirtualServers serves the org's virtual servers, as a release does.
func withVirtualServers(f *hostedFixture) { f.virtualServers = true }

// TestHeldConnectorUnlistedToolGetsTheServersRefusal: whether the caller
// holds a token for the server or has none (after Disconnect), none is
// fetched and no vendor is reached.
func TestHeldConnectorUnlistedToolGetsTheServersRefusal(t *testing.T) {
	for _, c := range []struct{ name, server, listed, sentence string }{
		{"microsoft app", "outlook", "outlook__list_mail_folders", microsoftComingSoon},
		{"app not served yet", "gmail", "gmail__get_profile", appComingSoon},
	} {
		for _, state := range []struct {
			name  string
			token bool
		}{{"connected", true}, {"after Disconnect", false}} {
			t.Run(c.name+"/"+state.name, func(t *testing.T) {
				f := newHostedFixture(t, heldServers, notServed("microsoft-outlook", "gmail"))
				if state.token {
					f.cp.set(pilotKey("org-a", "u1", c.server), tokenRow{value: "synthetic-held-access-u1"})
				}
				caller := userCaller("org-a", "u1")

				want := f.callTool(t, caller, c.listed)
				refusedPlainly(t, want, c.sentence)

				got := f.callTool(t, caller, c.server+unlisted)

				refusedPlainly(t, got, c.sentence)
				equal(t, got.Denial, want.Denial)
				if strings.Contains(got.Denial.Message, "Unknown tool") {
					t.Fatalf("refusal %q names an unknown tool", got.Denial.Message)
				}
				for _, k := range f.cp.fetched() {
					if k.ServerID == c.server {
						t.Fatalf("a token was fetched for %s", c.server)
					}
				}
				equal(t, len(f.vendor.seen()), 0)
			})
		}
	}
}

// TestHeldConnectorToolInEveryNamingMode: the same refusal through a
// virtual server with prefixed or flat names. Under flat names a tool the
// held server lists is called by its own name; a name no server lists maps
// to no server, so it stays unknown. A held connector's tool is not reached
// through the organization's compatibility view (no virtual server), like
// any connector's: every name there is an unknown tool.
func TestHeldConnectorToolInEveryNamingMode(t *testing.T) {
	prefixed := map[string]string{
		"outlook" + unlisted:         microsoftComingSoon,
		"gmail" + unlisted:           appComingSoon,
		"outlook__list_mail_folders": microsoftComingSoon,
		"gmail__get_profile":         appComingSoon,
		"list_mail_folders":          "",
		"walk_echo":                  "",
	}
	for _, mode := range []struct {
		name, naming string
		calls        map[string]string // name -> the refusal's sentence, "" for unknown
	}{
		// A held connector's tool is not reached through the compatibility
		// view, like any connector's: every name is an unknown tool.
		{"held connector not reached through the compatibility view", "", map[string]string{
			"outlook" + unlisted:         "",
			"gmail" + unlisted:           "",
			"outlook__list_mail_folders": "",
			"gmail__get_profile":         "",
			"list_mail_folders":          "",
			"walk_echo":                  "",
		}},
		{"virtual server with prefixed names", "prefixed", prefixed},
		{"virtual server with flat names", "flat", map[string]string{
			"list_mail_folders":          microsoftComingSoon,
			"get_profile":                appComingSoon,
			"outlook" + unlisted:         microsoftComingSoon,
			"gmail" + unlisted:           appComingSoon,
			"outlook__list_mail_folders": microsoftComingSoon,
			"walk_echo":                  "",
			"list_mail_folder":           "",
		}},
	} {
		t.Run(mode.name, func(t *testing.T) {
			f := newHostedFixture(t, heldServers, notServed("microsoft-outlook", "gmail"), withVirtualServers)
			f.store.Set("org-a", viewConfig(t, heldServers(f), mode.naming, "gmail", "gmail-team", "outlook"))
			f.cp.set(pilotKey("org-a", "u1", "gmail-team"), tokenRow{value: f.plainToken(t, "org-a/u1")})
			caller := userCaller("org-a", "u1")

			for name, sentence := range mode.calls {
				if sentence == "" {
					unknownTool(t, f, caller, name)
					continue
				}
				refusedPlainly(t, f.callTool(t, caller, name), sentence)
			}

			for _, k := range f.cp.fetched() {
				if k.ServerID != "gmail-team" {
					t.Fatalf("a token was fetched for %s", k.ServerID)
				}
			}
			equal(t, len(f.vendor.seen()), 0)
		})
	}
}

// TestSharedPrefixOfTwoMembersStaysUnknown: Gmail through the work account
// (member gmail@work) and a server added by URL with id gmail_work both name
// their tools "gmail_work__<tool>". A name with that prefix the view does not
// list maps to no one server, so it stays unknown; the held server's own
// prefix still answers its refusal.
func TestSharedPrefixOfTwoMembersStaysUnknown(t *testing.T) {
	servers := func(f *hostedFixture) map[string]any {
		out := heldServers(f)
		out["gmail_work"] = map[string]any{"url": f.plainURL, "credential": map[string]any{"mode": "per_user"}}
		return out
	}
	f := newHostedFixture(t, servers, notServed("microsoft-outlook", "gmail"), withVirtualServers)
	f.store.Set("org-a", viewConfig(t, servers(f), "prefixed", "gmail@work", "gmail_work", "outlook"))
	f.cp.set(pilotKey("org-a", "u1", "gmail_work"), tokenRow{value: f.plainToken(t, "org-a/u1")})
	caller := userCaller("org-a", "u1")
	connected(t, f.callTool(t, caller, "gmail_work__echo"))

	unknownTool(t, f, caller, "gmail_work"+unlisted)
	refusedPlainly(t, f.callTool(t, caller, "outlook"+unlisted), microsoftComingSoon)

	for _, k := range f.cp.fetched() {
		if k.ServerID != "gmail_work" {
			t.Fatalf("a token was fetched for %s", k.ServerID)
		}
	}
	equal(t, len(f.vendor.seen()), 0)
}

// TestUnlistedToolOutsideAHeldServerStaysUnknown: a served server whose id
// starts with a held one, a prefix of no server, a held id in another case,
// and a name with no separator.
func TestUnlistedToolOutsideAHeldServerStaysUnknown(t *testing.T) {
	f := newHostedFixture(t, heldServers, notServed("microsoft-outlook", "gmail"))
	f.cp.set(pilotKey("org-a", "u1", "gmail-team"), tokenRow{value: f.plainToken(t, "org-a/u1")})
	caller := userCaller("org-a", "u1")
	connected(t, f.callTool(t, caller, "gmail-team__echo"))

	for _, name := range []string{"gmail-team" + unlisted, "gmailx" + unlisted, "Outlook" + unlisted, "outlook_unlisted_tool"} {
		unknownTool(t, f, caller, name)
	}

	for _, k := range f.cp.fetched() {
		if k.ServerID != "gmail-team" {
			t.Fatalf("a token was fetched for %s", k.ServerID)
		}
	}
	equal(t, len(f.vendor.seen()), 0)
}

// TestHeldServerOfAnotherOrgStaysUnknown: an organization that did not add
// the held servers learns nothing about another's, by any of their names.
func TestHeldServerOfAnotherOrgStaysUnknown(t *testing.T) {
	f := newHostedFixture(t, heldServers, notServed("microsoft-outlook", "gmail"))
	f.store.Set("org-b", orgConfig(t, map[string]any{
		"gmail-team": map[string]any{"url": f.plainURL, "credential": map[string]any{"mode": "per_user"}},
	}))
	caller := userCaller("org-b", "u1")

	for _, name := range []string{"outlook" + unlisted, "outlook__list_mail_folders", "gmail" + unlisted, "gmail__get_profile"} {
		unknownTool(t, f, caller, name)
	}

	for _, k := range f.cp.fetched() {
		if k.OrgID != "org-b" || k.ServerID != "gmail-team" {
			t.Fatalf("a token was fetched for %s in %s", k.ServerID, k.OrgID)
		}
	}
	equal(t, len(f.vendor.seen()), 0)
}

// refusedPlainly requires the refusal every tool of a server the gateway
// does not serve gets: isError, policy_unavailable, the plain sentence, the
// metadata with no connect link, and no internal code name in the text the
// caller reads.
func refusedPlainly(t *testing.T, a answer, sentence string) {
	t.Helper()
	denied(t, a, mcp.CodePolicyUnavailable)
	equal(t, a.Denial.Message, sentence)
	equal(t, metadata(t, a), map[string]any{"state": "coming_soon", "reason": "connection_disabled", "client_source": "org"})
	var body struct {
		Content []struct {
			Text string `json:"text"`
		} `json:"content"`
	}
	if err := json.Unmarshal(a.Raw, &body); err != nil || len(body.Content) == 0 {
		t.Fatalf("refusal has no text: %s", a.Raw)
	}
	for _, part := range body.Content {
		if !strings.HasPrefix(part.Text, sentence) || strings.Contains(strings.ToLower(part.Text), "k2") {
			t.Fatalf("refusal text %q, want the sentence %q", part.Text, sentence)
		}
	}
}

// unknownTool calls name as bearer and requires JSON-RPC -32602 "Unknown tool".
func unknownTool(t *testing.T, f *hostedFixture, bearer, name string) {
	t.Helper()
	result, rpcErr := f.callRaw(t, bearer, name)
	var refusal struct {
		Code    int    `json:"code"`
		Message string `json:"message"`
	}
	if len(result) != 0 || json.Unmarshal(rpcErr, &refusal) != nil ||
		refusal.Code != mcp.ErrCodeInvalidParams || !strings.Contains(refusal.Message, "Unknown tool: "+name) {
		t.Fatalf("%s: result %s, error %s; want %d Unknown tool", name, result, rpcErr, mcp.ErrCodeInvalidParams)
	}
}

// callRaw initializes a session as bearer and calls one tool by its gateway
// name with no arguments, returning the JSON-RPC result and error members.
func (f *hostedFixture) callRaw(t *testing.T, bearer, name string) (json.RawMessage, json.RawMessage) {
	t.Helper()
	post := func(session, method string, params any) *httptest.ResponseRecorder {
		raw, _ := json.Marshal(map[string]any{"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
		r := httptest.NewRequest("POST", "/mcp", bytes.NewReader(raw))
		r.Header.Set("Content-Type", "application/json")
		r.Header.Set("Accept", "application/json, text/event-stream")
		r.Header.Set("Authorization", "Bearer "+bearer)
		if session != "" {
			r.Header.Set("MCP-Session-Id", session)
		}
		w := httptest.NewRecorder()
		f.srv.HandlePost(w, r)
		return w
	}
	w := post("", "initialize", map[string]any{"protocolVersion": mcp.ProtocolVersion, "capabilities": map[string]any{}, "clientInfo": map[string]any{"name": "held", "version": "1"}})
	session := w.Header().Get("MCP-Session-Id")
	if session == "" {
		t.Fatalf("initialize: status=%d", w.Code)
	}
	w = post(session, "tools/call", map[string]any{"name": name, "arguments": map[string]any{}})
	var envelope struct {
		Result json.RawMessage `json:"result"`
		Error  json.RawMessage `json:"error"`
	}
	if err := json.Unmarshal(w.Body.Bytes(), &envelope); err != nil {
		t.Fatalf("tools/call %s: status=%d, body is not JSON-RPC", name, w.Code)
	}
	return envelope.Result, envelope.Error
}

// orgConfig is an org's pushed config with MCP on, access allowed by default,
// and servers.
func orgConfig(t *testing.T, servers map[string]any) *tenant.OrgConfig {
	t.Helper()
	return viewConfig(t, servers, "")
}

// viewConfig is orgConfig whose tools are served through one virtual server,
// the org's default, with the naming ("prefixed" or "flat") and members
// given; with naming "" the org has none, so callers get its compatibility
// view.
func viewConfig(t *testing.T, servers map[string]any, naming string, members ...string) *tenant.OrgConfig {
	t.Helper()
	section := map[string]any{"enabled": true, "access": map[string]any{"default": "allow"}, "servers": servers}
	if naming != "" {
		section["virtual_servers"] = map[string]any{"held": map[string]any{
			"id": "vs_held", "naming": naming, "members": members, "access": map[string]any{"default_for_org": true}}}
	}
	body, _ := json.Marshal(map[string]any{"meta": map[string]any{"version": 1}, "mcp": section})
	var cfg tenant.OrgConfig
	if err := json.Unmarshal(body, &cfg); err != nil {
		t.Fatal(err)
	}
	return &cfg
}
