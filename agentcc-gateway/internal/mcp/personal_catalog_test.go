package mcp

import (
	"context"
	"encoding/json"
	"fmt"
	"testing"
)

// The pool's offline tools are common, but authenticated
// resource/prompt definitions and additional tools vary with the account.
type personalCatalogFixture struct {
	UpstreamClient
	account string
}

func (c *personalCatalogFixture) ListTools(context.Context) ([]json.RawMessage, error) {
	return []json.RawMessage{json.RawMessage(`{"name":"common","inputSchema":{"type":"object"}}`),
		json.RawMessage(fmt.Sprintf(`{"name":%q,"description":%q}`, c.account, c.account+" tool"))}, nil
}
func (c *personalCatalogFixture) ListResources(context.Context) ([]json.RawMessage, error) {
	return []json.RawMessage{json.RawMessage(fmt.Sprintf(`{"uri":%q,"name":%q,"description":%q}`, "file:///"+c.account, c.account, c.account+" resource"))}, nil
}
func (c *personalCatalogFixture) ListPrompts(context.Context) ([]json.RawMessage, error) {
	return []json.RawMessage{json.RawMessage(fmt.Sprintf(`{"name":%q,"description":%q,"arguments":[{"name":%q,"required":true}]}`, c.account, c.account+" prompt", c.account+"_arg"))}, nil
}

type personalCatalogPool struct {
	TenantPool
	accounts map[string]string
	asked    []string
}

func (p *personalCatalogPool) OrgID() string { return "org-a" }
func (p *personalCatalogPool) Server(id string) (PoolServer, bool) {
	return PoolServer{ID: "mine", PerUser: true, Tools: []Tool{{Name: "common"}},
		Resources: []Resource{{URI: "file:///a", Name: "a", Description: "a resource"}},
		Prompts:   []Prompt{{Name: "a", Description: "a prompt"}}}, id == "mine"
}
func (p *personalCatalogPool) Client(_ string, user string, auth AuthOverride) (UpstreamClient, error) {
	p.asked = append(p.asked, user)
	account, ok := p.accounts[user]
	if !ok || auth.Header.Get("Authorization") != "Bearer "+account {
		return nil, CredentialRequired("mine", "no_token")
	}
	return &personalCatalogFixture{account: account}, nil
}

func TestPersonalCatalogListsAndLookupsUseCurrentCaller(t *testing.T) {
	s := newTestServer(t)
	defer s.Close()
	accounts := map[string]string{"member-a": "a", "member-b": "b"}
	pool := &personalCatalogPool{accounts: accounts}
	s.SetDeps(Deps{CredentialResolver: userTokens(accounts)})
	s.registry.SetOrgSource(func(string) MemberSource { return PoolMembers(pool, s.registry.Separator()) })
	cfg := orgConfig(t, `{"mcp":{"enabled":true,"access":{"default":"allow"},"virtual_servers":{"personal":{"id":"personal","members":["mine"],"access":{"default_for_org":true}}}}}`)
	viewFor := func(user string) *CallContext {
		t.Helper()
		cc := &CallContext{ctx: context.WithValue(context.Background(), tenantPoolKey{}, pool),
			Principal: oauthPrincipal(user), OrgConfig: cfg, Method: MethodResourcesList}
		if d := resolveVirtualServer(s, cc); d != nil {
			t.Fatalf("resolve %s: %v", user, d)
		}
		return cc
	}
	seenPrompts := map[string]string{}
	check := func(user, account string) {
		t.Helper()
		cc := viewFor(user)
		filterResources(s, cc)
		filterPrompts(s, cc)
		assembleList(s, cc)
		if len(cc.ResourceList) != 1 || cc.ResourceList[0].URI != "mcp://mine/file:///"+account || cc.ResourceList[0].Name != account || cc.ResourceList[0].Description != account+" resource" {
			t.Errorf("%s received another account's resources: %+v", user, cc.ResourceList)
		}
		if len(cc.PromptList) != 1 || cc.PromptList[0].Description != account+" prompt" || len(cc.PromptList[0].Arguments) != 1 || cc.PromptList[0].Arguments[0].Name != account+"_arg" {
			t.Errorf("%s received another account's prompt definitions: %+v", user, cc.PromptList)
		}
		if len(cc.ToolList) != 2 {
			t.Errorf("%s did not receive its personal tools: %+v", user, cc.ToolList)
		}
		cc.legacy.resourceURI = "mcp://mine/file:///" + account
		if d := authorizeResource(s, cc); d != nil {
			t.Errorf("%s cannot look up its own resource: %v", user, d)
		}
		for _, e := range callViewOf(cc).prompts.Entries {
			if e.Name != account {
				t.Errorf("%s can look up another account's prompt: %s", user, e.Name)
			}
			cc.legacy.promptName = e.Exposed
			if d := authorizePrompt(s, cc); d != nil {
				t.Errorf("%s cannot look up its own prompt: %v", user, d)
			}
			seenPrompts[account] = e.Exposed
		}
		for owner, name := range seenPrompts {
			if owner != account {
				cc.legacy.promptName = name
				if d := authorizePrompt(s, cc); d == nil {
					t.Errorf("%s can look up %s's prompt", user, owner)
				}
			}
		}
		other := "a"
		if account == "a" {
			other = "b"
		}
		cc.legacy.resourceURI = "mcp://mine/file:///" + other
		if d := authorizeResource(s, cc); d == nil {
			t.Errorf("%s can look up %s's resource", user, other)
		}
	}
	check("member-a", "a")
	check("member-b", "b")
	for _, user := range []string{"unconnected", ""} {
		cc := viewFor(user)
		filterResources(s, cc)
		filterPrompts(s, cc)
		if len(cc.ResourceList) != 0 || len(cc.PromptList) != 0 {
			t.Errorf("caller %q without an account received personal metadata", user)
		}
	}
	// Reconnecting the same person must rebuild their request view, not keep
	// definitions from either their old account or the other member's account.
	accounts["member-b"] = "b_new"
	check("member-b", "b_new")
	check("member-a", "a")
	if len(pool.asked) != 4 {
		t.Errorf("personal discovery acquired %d clients, want each connected caller's account", len(pool.asked))
	}
}
