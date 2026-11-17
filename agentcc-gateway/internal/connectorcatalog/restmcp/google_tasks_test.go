package restmcp

import (
	"encoding/json"
	"net/http"
	"net/url"
	"os"
	"testing"
	"testing/fstest"

	"github.com/futureagi/agentcc-gateway/internal/connectorcatalog/manifest"
)

// Load only the two production records; no generated tools or fixture catalog.
func googleTasksCatalog(t *testing.T) *manifest.Catalog {
	t.Helper()
	files := fstest.MapFS{}
	for _, name := range []string{"app.yaml", "providers/futureagi-rest.yaml"} {
		path := "apps/google-tasks/" + name
		data, err := os.ReadFile(repoPath(t, "agentcc-gateway/catalog/connectors/"+path))
		if err != nil {
			t.Fatal(err)
		}
		files[path] = &fstest.MapFile{Data: data}
	}
	cat, err := manifest.LoadDir(files)
	if err != nil {
		t.Fatal(err)
	}
	return cat
}

func TestGoogleTasksCompileReadOnly(t *testing.T) {
	cat := googleTasksCatalog(t)
	app := compileOne(t, cat, "google-tasks")
	if app.Path != "/google-tasks/mcp" || app.ToolCount() != 2 {
		t.Fatalf("path=%s tools=%d", app.Path, app.ToolCount())
	}
	p := cat.App("google-tasks").Providers[0]
	if p.Kind != manifest.KindREST || p.Tools.Generated != "" || len(p.Tools.Items) != 2 {
		t.Fatal("expected a native REST provider with two inline tools")
	}
	for name, path := range map[string]string{
		"list_tasklists": "/tasks/v1/users/@me/lists",
		"list_tasks":     "/tasks/v1/lists/{tasklist}/tasks",
	} {
		tool := app.Tool(name)
		if tool == nil {
			t.Fatalf("missing tool %s", name)
		}
		if tool.method != http.MethodGet || tool.origin != "https://tasks.googleapis.com" || tool.path != path {
			t.Fatalf("%s: unexpected REST operation", name)
		}
		if !tool.annotations.ReadOnlyHint || tool.annotations.DestructiveHint || !tool.annotations.IdempotentHint {
			t.Fatalf("%s: unexpected annotations", name)
		}
		if tool.unwrap != "" || len(tool.fields) != 0 {
			t.Fatalf("%s: pagination envelope must remain intact", name)
		}
	}
}

func TestGoogleTasksGETPages(t *testing.T) {
	app := compileOne(t, googleTasksCatalog(t), "google-tasks")
	for _, tc := range []struct {
		name, path string
		args       map[string]any
	}{
		{"list_tasklists", "/tasks/v1/users/@me/lists", map[string]any{"maxResults": 1000}},
		{"list_tasks", "/tasks/v1/lists/list%2Fwith%20space%3Fx%23%25/tasks", map[string]any{
			"tasklist": "list/with space?x#%", "maxResults": 100,
			"showCompleted": true, "showHidden": true, "showAssigned": false,
		}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			vendor := newVendorFake(t, func(w http.ResponseWriter, r *http.Request) {
				page := map[string]any{"kind": "opaque-kind", "etag": "fake-etag"}
				if r.URL.Query().Get("pageToken") == "" {
					page["items"] = []any{map[string]any{"id": "fake-item", "title": "Example"}}
					page["nextPageToken"] = "next+/=&"
				}
				jsonAnswer(http.StatusOK, page)(w, r)
			})
			rt := startRuntime(t, []*App{app}, map[string]string{"https://tasks.googleapis.com": vendor.URL})
			for page := 0; page < 2; page++ {
				c := rt.callTool(t, app.Path, testToken, tc.name, tc.args)
				if c.IsError {
					t.Fatalf("page %d: %s", page, c.text())
				}
				var envelope struct {
					Kind, Etag, NextPageToken string
					Items                     []json.RawMessage
				}
				if err := json.Unmarshal(c.Structured, &envelope); err != nil {
					t.Fatalf("expected structured page envelope: %v", err)
				}
				if envelope.Kind != "opaque-kind" || envelope.Etag != "fake-etag" {
					t.Fatal("lost envelope metadata")
				}
				if page == 0 && (envelope.NextPageToken != "next+/=&" || len(envelope.Items) != 1) {
					t.Fatal("lost items or nextPageToken")
				}
				if page == 1 && (envelope.NextPageToken != "" || len(envelope.Items) != 0) {
					t.Fatal("missing items must represent an empty final page")
				}
				reqs := vendor.requests()
				if len(reqs) != page+1 {
					t.Fatalf("expected exactly one GET per call, got %d", len(reqs))
				}
				r := reqs[page]
				if r.Method != http.MethodGet || r.Path != tc.path || len(r.Body) != 0 || r.Header.Get("Authorization") != "Bearer "+testToken {
					t.Fatal("unexpected method, encoded path, body or forwarded bearer")
				}
				q, err := url.ParseQuery(r.Query)
				wantMax, wantKeys := "1000", 1
				if tc.name == "list_tasks" {
					wantMax, wantKeys = "100", 4
					if q.Get("showCompleted") != "true" || q.Get("showHidden") != "true" || q.Get("showAssigned") != "false" {
						t.Fatal("boolean filters not preserved")
					}
				}
				if page == 1 {
					wantKeys++
					if q.Get("pageToken") != "next+/=&" {
						t.Fatal("pageToken not preserved")
					}
				}
				if err != nil || q.Get("maxResults") != wantMax || len(q) != wantKeys {
					t.Fatal("unexpected query parameters")
				}
				tc.args["pageToken"] = envelope.NextPageToken
			}
		})
	}
}

func TestGoogleTasksRejectsInvalidInputsAndMissingBearer(t *testing.T) {
	app := compileOne(t, googleTasksCatalog(t), "google-tasks")
	vendor := newVendorFake(t, jsonAnswer(http.StatusOK, map[string]any{}))
	rt := startRuntime(t, []*App{app}, map[string]string{"https://tasks.googleapis.com": vendor.URL})
	for _, tc := range []struct{ tool, args string }{
		{"list_tasklists", `{"maxResults":1001}`},
		{"list_tasklists", `{"maxResults":0}`},
		{"list_tasklists", `{"maxResults":1.5}`},
		{"list_tasklists", `{"method":"POST"}`},
		{"list_tasklists", `{"body":{}}`},
		{"list_tasklists", `{"url":"https://example.invalid"}`},
		{"list_tasks", `{}`},
		{"list_tasks", `{"tasklist":""}`},
		{"list_tasks", `{"tasklist":"@default","maxResults":101}`},
		{"list_tasks", `{"tasklist":"@default","maxResults":0}`},
		{"list_tasks", `{"tasklist":"@default","showHidden":"true"}`},
		{"list_tasks", `{"tasklist":"@default","method":"POST"}`},
	} {
		r := rt.legacyCall(t, app.Path, testToken, "tools/call", `{"name":"`+tc.tool+`","arguments":`+tc.args+`}`)
		if r.Error == nil && !decodeCall(t, r).IsError {
			t.Errorf("accepted %s %s", tc.tool, tc.args)
		}
	}
	r := rt.post(t, app.Path, `{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"list_tasklists","arguments":{}}}`, "MCP-Protocol-Version", "2025-11-25")
	if r.Status != http.StatusUnauthorized {
		t.Errorf("missing bearer: HTTP %d, want 401", r.Status)
	}
	if len(vendor.requests()) != 0 {
		t.Fatal("invalid input or missing bearer reached the vendor")
	}
}
