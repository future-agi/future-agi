package mcp

import (
	"context"
	"encoding/json"
	"net/http/httptest"
	"reflect"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	gatewayadmin "github.com/futureagi/agentcc-gateway/internal/contracts/generated"
)

// The interface makes the pre-implementation red a behavioral failure rather
// than a compiler failure. All upstream data is synthetic and stays in memory.
type servedReader interface {
	AdminServedTools(context.Context, string, string, string) (*gatewayadmin.MCPAdminTools, error)
}

func servedFixture(t *testing.T) (*Server, servedReader, *fakeTenancy, *auth.KeyStore) {
	t.Helper()
	s := newTestServer(t)
	t.Cleanup(s.Close)
	reader, ok := any(s).(servedReader)
	if !ok {
		t.Fatal("missing runtime-backed served-tool read")
	}
	ten, _, _ := twoOrgs()
	ten.snapshots["org-a"] = orgConfig(t, `{"mcp":{"enabled":true,"access":{"default":"allow"},"servers":{"github":{"url":"https://fixture.invalid"}},"virtual_servers":{"eng":{"id":"vs_eng","members":["github"],"tools":{"exclude":["github/hidden"]},"access":{"rules":[{"match":{},"deny":["github/denied"]}]}}}}}`)
	ten.pools["org-a"].servers[0].Tools = []Tool{
		pinTool(t, `{"name":"read","inputSchema":{"type":"object"}}`),
		pinTool(t, `{"name":"write","inputSchema":{"type":"object"}}`),
		pinTool(t, `{"name":"hidden","inputSchema":{"type":"object"}}`),
		pinTool(t, `{"name":"denied","inputSchema":{"type":"object"}}`),
	}
	s.SetTenancy(ten)
	t.Cleanup(func() { s.SetTenancy(nil) })
	s.Registry().SetOrgSource(func(org string) MemberSource {
		_, pool, _ := ten.Resolve(org)
		return PoolMembers(pool, s.Registry().Separator())
	})
	ks := auth.NewKeyStore(config.AuthConfig{Keys: []config.AuthKeyConfig{
		{Name: "reader", Key: "synthetic-reader-fixture", Metadata: map[string]string{"org_id": "org-a"}, AllowedTools: []string{"github__read"}},
		{Name: "foreign", Key: "synthetic-foreign-fixture", Metadata: map[string]string{"org_id": "org-b"}},
	}})
	s.SetIdentity(Identity{Keys: ks})
	return s, reader, ten, ks
}

func TestServedReadMatchesRuntime(t *testing.T) {
	s, reader, _, ks := servedFixture(t)
	for _, selected := range []string{"", ks.Lookup("synthetic-reader-fixture").ID} {
		t.Run("context-"+selected, func(t *testing.T) {
			got, err := reader.AdminServedTools(context.Background(), "org-a", "vs_eng", selected)
			if err != nil {
				t.Fatal(err)
			}
			r := WithVirtualServer(httptest.NewRequest("POST", "/mcp", nil), "vs_eng")
			if selected != "" {
				r.Header.Set("Authorization", "Bearer synthetic-reader-fixture")
				var ok bool
				r, ok = s.authenticateHTTP(httptest.NewRecorder(), r)
				if !ok {
					t.Fatal("fixture key refused")
				}
			}
			cc := s.newCallContext(r, &Message{Method: MethodToolsList}, nil)
			if selected == "" {
				cc.Principal = Principal{Kind: PrincipalAdmin, OrgID: "org-a"}
			}
			s.runChain(cc, toolsListChain)
			defer cc.runFinalizers()
			if cc.Decision != nil {
				t.Fatalf("runtime refused: %s", cc.Decision.Code)
			}
			actual, expected := []string{}, []string{}
			for _, x := range got.Tools {
				actual = append(actual, x.Name)
				if x.Quarantined {
					t.Fatal("served a quarantined tool")
				}
			}
			wire := httptest.NewRecorder()
			s.writeToolsList(wire, cc)
			response := decodeResponse(t, wire)
			var listed ListToolsResult
			if response.Error != nil || json.Unmarshal(response.Result, &listed) != nil {
				t.Fatal("runtime tools/list wire response failed")
			}
			for _, x := range listed.Tools {
				expected = append(expected, x.Name)
			}
			if !reflect.DeepEqual(actual, expected) {
				t.Fatalf("read %v differs from runtime %v", actual, expected)
			}
			want := 2
			if selected != "" {
				want = 1
			}
			if len(actual) != want {
				t.Fatalf("wanted %d policy-filtered tools, got %d", want, len(actual))
			}
		})
	}
}

func TestServedReadRejectsForeignContext(t *testing.T) {
	_, reader, _, ks := servedFixture(t)
	for _, c := range [][3]string{{"unknown", "vs_eng", ""}, {"org-b", "vs_eng", ""}, {"org-a", "missing", ""}, {"org-a", "vs_eng", "missing"}, {"org-a", "vs_eng", ks.Lookup("synthetic-foreign-fixture").ID}} {
		if _, err := reader.AdminServedTools(context.Background(), c[0], c[1], c[2]); err == nil {
			t.Fatal("unknown or foreign context accepted")
		}
	}
}

func TestServedReadQuarantineKeepsAdminInventory(t *testing.T) {
	s, reader, ten, _ := servedFixture(t)
	s.SetDeps(Deps{Pins: OrgPins{}})
	ten.snapshots["org-a"] = orgConfig(t, `{"mcp":{"enabled":true,"servers":{"github":{"url":"https://fixture.invalid"}},"virtual_servers":{"eng":{"id":"vs_eng","members":["github"]}},"pinning":{"mode":"quarantine","auto_baseline":false}}}`)
	got, err := reader.AdminServedTools(context.Background(), "org-a", "vs_eng", "")
	if err != nil {
		t.Fatal(err)
	}
	if len(got.Tools) != 0 {
		t.Fatal("unaccepted tools must be absent from served read")
	}
	inventory, err := s.AdminTools("org-a", "vs_eng")
	if err != nil || len(inventory.Tools) != 4 {
		t.Fatal("admin quarantine inventory changed")
	}
}

func TestServedReadRestrictedServerAndSearchMode(t *testing.T) {
	s, reader, ten, ks := servedFixture(t)
	cfg := ten.snapshots["org-a"].MCP.VirtualServers["eng"]
	cfg.Access.Keys = []string{"another-key"}
	if got, err := reader.AdminServedTools(context.Background(), "org-a", "vs_eng", ""); err != nil || len(got.Tools) != 2 {
		t.Fatal("unrestricted admin context must inspect its own virtual server")
	}
	if _, err := reader.AdminServedTools(context.Background(), "org-a", "vs_eng", ks.Lookup("synthetic-reader-fixture").ID); err == nil {
		t.Fatal("key context bypassed virtual-server access")
	}
	cfg.Access.Keys = nil
	cfg.Mode = "search"
	got, err := reader.AdminServedTools(context.Background(), "org-a", "vs_eng", "")
	if err != nil || len(got.Tools) != 3 {
		t.Fatal("search mode must list the actual three runtime meta-tools")
	}
	for _, tool := range got.Tools {
		if tool.ServerID != "__gateway__" || tool.Identity.Platform != "other" {
			t.Fatal("meta-tool attributed to upstream")
		}
	}
	inventory, err := s.AdminTools("org-a", "vs_eng")
	if err != nil || len(inventory.Tools) != 4 {
		t.Fatal("inventory changed with runtime mode")
	}
}

func TestServedReadUnprovenIdentityStaysOther(t *testing.T) {
	_, reader, ten, _ := servedFixture(t)
	// Deliberately invented extensions: these are negative cases, not vendor
	// fixtures. Neither platform documents these paths (research 2026-10-27).
	cases := []struct{ name, fields string }{
		{"composio-meta", `"_meta":{"composio":{"toolkit":{"slug":"github","name":"GitHub"}}}`},
		{"pipedream-annotations", `"annotations":{"pipedream":{"app":{"name_slug":"google_sheets","name":"Google Sheets"}}}`},
		{"composio-annotations", `"annotations":{"composio":{"toolkit":{"slug":"GITHUB","name":"GitHub"}}}`},
		{"pipedream-meta", `"_meta":{"pipedream":{"app":{"name_slug":"google_sheets","name":"Google Sheets"}}}`},
		{"conflicting", `"_meta":{"composio":{"toolkit":{"slug":"github","name":"GitHub"}}},"annotations":{"pipedream":{"app":{"name_slug":"github","name":"GitHub"}}}`},
		{"rest-only-toolkit", `"toolkit":{"slug":"github","name":"GitHub"}`},
		{"control", `"_meta":{"composio":{"toolkit":{"slug":"github","name":"GitHub\nSecret"}}}`},
		{"oversized-name", `"_meta":{"composio":{"toolkit":{"slug":"github","name":"` + strings.Repeat("x", 97) + `"}}}`},
		{"missing", `"description":"COMPOSIO_GITHUB is not metadata"`},
		{"malicious", `"_meta":{"composio":{"toolkit":{"slug":"../secret","name":"<script>"}}}`},
		{"oversized-slug", `"_meta":{"composio":{"toolkit":{"slug":"` + strings.Repeat("x", 65) + `","name":"X"}}}`},
		{"wrong-type", `"_meta":{"pipedream":{"app":{"name_slug":[],"name":"X"}}}`},
		{"oversized-meta", `"_meta":{"padding":"` + strings.Repeat("x", 17<<10) + `","composio":{"toolkit":{"slug":"github","name":"GitHub"}}}`},
		{"oversized-raw", `"description":"` + strings.Repeat("x", 65<<10) + `","_meta":{"composio":{"toolkit":{"slug":"github","name":"GitHub"}}}`},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			ten.pools["org-a"].servers[0].Tools = []Tool{pinTool(t, `{"name":"COMPOSIO_GITHUB_read","inputSchema":{"type":"object"},`+c.fields+`}`)}
			got, err := reader.AdminServedTools(context.Background(), "org-a", "vs_eng", "")
			if err != nil || len(got.Tools) != 1 {
				t.Fatal("missing served fixture")
			}
			raw, _ := json.Marshal(got.Tools[0])
			var row map[string]any
			_ = json.Unmarshal(raw, &row)
			identity, ok := row["identity"].(map[string]any)
			if !ok || identity["platform"] != "other" || identity["toolkit_slug"] != "" || identity["toolkit_name"] != "" {
				t.Fatalf("incorrect normalized identity for %s", c.name)
			}
			if len(identity) != 3 || row["annotations"] != nil || row["_meta"] != nil {
				t.Fatal("arbitrary upstream metadata escaped")
			}
		})
	}
}
