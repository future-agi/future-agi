package restmcp

import (
	"encoding/json"
	"io"
	"log"
	"net/http/httptest"
	"os"
	"reflect"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/catalog/connectors"
	"github.com/futureagi/agentcc-gateway/cmd/agentcc-cassette/cassette"
	"github.com/futureagi/agentcc-gateway/cmd/agentcc-cassette/replay"
	"github.com/futureagi/agentcc-gateway/internal/connectorcatalog/manifest"
)

func TestCN27Batch(t *testing.T) {
	checkV10Batch(t, []string{"google-drive", "google-docs", "google-sheets"})
}

// All fixtures are synthetic, and every HTTP request is replayed on loopback.
func checkV10Batch(t *testing.T, ids []string) {
	t.Helper()
	cat, err := connectors.LoadDir(repoPath(t, "agentcc-gateway/catalog/connectors"))
	if err != nil {
		t.Fatal(err)
	}
	for _, id := range ids {
		t.Run(id, func(t *testing.T) {
			app := cat.Records.App(id)
			if app == nil {
				t.Fatalf("missing catalog app %s", id)
			}
			var provider *manifest.Provider
			for _, p := range app.Providers {
				if p.ID == "futureagi-rest" {
					provider = p
				}
			}
			if provider == nil {
				t.Fatal("missing futureagi-rest provider")
			}
			if provider.Kind != manifest.KindREST || provider.Trusted || provider.Quality.Tier != "listed" {
				t.Fatal("synthetic catalog providers must remain untrusted/listed REST")
			}
			oauth := provider.Auth.OAuth2
			if oauth == nil || oauth.ClientGroup != app.Group {
				t.Fatal("missing group-bound OAuth")
			}
			if app.Group == "google" {
				base := cat.Records.App("gmail").Provider("futureagi-rest").Auth.OAuth2
				if oauth.TokenURL != base.TokenURL || !reflect.DeepEqual(oauth.Revocation, base.Revocation) {
					t.Fatal("Google client-group token/revocation drift")
				}
			} else if oauth.URLVariables["tenant"].Scope != "org" {
				t.Fatal("Microsoft tenant must be org-scoped")
			}
			tools := provider.AllTools()
			union := map[string]bool{}
			for _, tool := range tools {
				for _, scope := range tool.Scopes {
					union[scope] = true
				}
			}
			if app.Group == "microsoft" {
				union["offline_access"] = true
			}
			if len(union) != len(oauth.Scopes) {
				t.Fatal("scope union mismatch")
			}
			for _, scope := range oauth.Scopes {
				if !union[scope] || strings.Contains(scope, "/.default") {
					t.Fatal("scope not delegated/used", scope)
				}
			}
			if len(tools) < 8 || len(tools) > 15 {
				t.Fatalf("curated tool count %d, want 8..15", len(tools))
			}
			compiled := compileOne(t, cat.Records, id)
			if compiled.Path != "/"+id+"/mcp" {
				t.Fatal(compiled.Path)
			}
			base := repoPath(t, "agentcc-gateway/catalog/connectors/apps/"+id+"/providers/futureagi-rest/fixtures/cassettes/"+id)
			raw, err := os.ReadFile(base + ".calls.json")
			if err != nil {
				t.Fatal(err)
			}
			var calls []scenarioCall
			if err := json.Unmarshal(raw, &calls); err != nil {
				t.Fatal(err)
			}
			if len(calls) != len(tools)+1 {
				t.Fatalf("%d calls for %d tools", len(calls), len(tools))
			}
			raw, err = os.ReadFile(base + ".unverified.cassette.json")
			if err != nil {
				t.Fatal(err)
			}
			if err := cassette.Check(raw); err != nil {
				t.Fatal(err)
			}
			c, err := cassette.Load(raw)
			if err != nil {
				t.Fatal(err)
			}
			player, err := replay.New([]*cassette.Cassette{c}, log.New(io.Discard, "", 0))
			if err != nil {
				t.Fatal(err)
			}
			srv := httptest.NewServer(player.Handler("/mcp"))
			defer srv.Close()
			origins := map[string]string{}
			for _, tool := range tools {
				var schema map[string]any
				if err := json.Unmarshal(tool.InputSchema, &schema); err != nil {
					t.Fatal(err)
				}
				origin, ok := schema["x-origin"].(string)
				if !ok || origin == "" {
					t.Fatal("missing origin")
				}
				origins[origin] = srv.URL
			}
			rt := startRuntime(t, []*App{compiled}, origins)
			seen := map[string]bool{}
			for i, call := range calls {
				if seen[call.Tool] && i != len(calls)-1 {
					t.Fatalf("duplicate cassette tool %s", call.Tool)
				}
				seen[call.Tool] = true
				result := rt.callTool(t, compiled.Path, "fixture-bearer-not-real", call.Tool, call.Arguments)
				if result.IsError != (i == len(calls)-1) {
					t.Fatalf("%s: %s", call.Tool, result.text())
				}
			}
			if misses := player.Misses(); len(misses) != 0 {
				t.Fatalf("replay misses: %v", misses)
			}
		})
	}
}
