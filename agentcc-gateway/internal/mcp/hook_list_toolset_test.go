package mcp

import (
	"encoding/json"
	"fmt"
	"strings"
	"testing"
	"unicode/utf8"
)

// The M30 tool-set tests: step profiles narrow what a virtual server offers
// at a step, and search mode offers three meta-tools in place of the list.

// headerStep is the agent step header (M8).
const headerStep = "x-agentcc-step"

// listAnswer posts tools/list and returns the listed names and the
// response's _meta["io.futureagi/tool_list"] (nil when absent).
func (f *vsFixture) listAnswer(t *testing.T, vsID string, headers map[string]string) ([]string, map[string]any) {
	t.Helper()
	resp := decodeResponse(t, f.post(t, vsID, MethodToolsList, struct{}{}, headers))
	if resp.Error != nil {
		t.Fatalf("tools/list: %+v", resp.Error)
	}
	var res struct {
		Tools []Tool                     `json:"tools"`
		Meta  map[string]json.RawMessage `json:"_meta"`
	}
	if err := json.Unmarshal(resp.Result, &res); err != nil {
		t.Fatal(err)
	}
	names := []string{}
	for _, tl := range res.Tools {
		names = append(names, tl.Name)
	}
	var meta map[string]any
	if raw, ok := res.Meta[MetaKeyToolList]; ok {
		if err := json.Unmarshal(raw, &meta); err != nil {
			t.Fatalf("%s is not an object: %s", MetaKeyToolList, raw)
		}
	}
	return names, meta
}

func stepHeader(step string) map[string]string {
	return map[string]string{headerStep: step}
}

// TestStepProfileNarrows: a request whose agent step has a profile is
// offered only the tools the profile includes (by <member>/<tool>, and
// <server>/<tool> for an account member, as tools.include matches), never one
// the caller could not otherwise see; a request with no step, a step with no
// profile, or a profile with no include is offered the full set. A narrowed
// list says what it saved in _meta["io.futureagi/tool_list"]; an unnarrowed
// one is sent exactly as before.
func TestStepProfileNarrows(t *testing.T) {
	f := newVSFixture(t, `{"mcp":{"virtual_servers":{"eng":{"id":"`+vsEng+`","members":["github","github@work","linear"],
		"access":{"default_for_org":true,"rules":[{"match":{"key_ids":["k1"]},"deny":["linear/delete_*"]}]},
		"step_profiles":{"plan":{"include":["linear/*","github/list_*"]},"act":{"include":[]}}}}}}`)
	registerTools(f.Server, "github", "list_repos", "create_issue")
	registerTools(f.Server, "linear", "create_ticket", "delete_ticket")
	full := "github__create_issue,github__list_repos,github_work__create_issue,github_work__list_repos,linear__create_ticket"

	for _, h := range []map[string]string{nil, stepHeader("act"), stepHeader("verify")} {
		got, meta := f.listAnswer(t, "", h)
		if strings.Join(got, ",") != full || meta != nil {
			t.Fatalf("step %v lists %v with %s %v, want the full set and no %s", h, got, MetaKeyToolList, meta, MetaKeyToolList)
		}
	}

	got, meta := f.listAnswer(t, "", stepHeader("plan"))
	if want := "github__list_repos,github_work__list_repos,linear__create_ticket"; strings.Join(got, ",") != want {
		t.Fatalf("step plan lists %v, want %s (linear/delete_ticket stays refused)", got, want)
	}
	if meta["mode"] != "direct" || meta["tools_full"] != float64(5) || meta["tools_sent"] != float64(3) || meta["estimated"] != true {
		t.Fatalf("step plan %s = %v, want mode direct, tools_full 5, tools_sent 3, estimated", MetaKeyToolList, meta)
	}
	if full, sent := meta["tokens_full"].(float64), meta["tokens_sent"].(float64); !(sent > 0 && sent < full) {
		t.Fatalf("step plan tokens_full %v, tokens_sent %v: want 0 < sent < full", full, sent)
	}

	// Twenty lists at one step are byte-identical.
	first := f.post(t, "", MethodToolsList, struct{}{}, stepHeader("plan")).Body.String()
	for i := 0; i < 20; i++ {
		if again := f.post(t, "", MethodToolsList, struct{}{}, stepHeader("plan")).Body.String(); again != first {
			t.Fatalf("list %d at step plan differs:\n%s\n%s", i, again, first)
		}
	}
}

// TestStepProfileNeverWidens: for every principal, header narrowing and
// step, the step's list is a subset of the list the same request gets with
// no step, so a profile can only take tools away.
func TestStepProfileNeverWidens(t *testing.T) {
	f := newVSFixture(t, `{"mcp":{"virtual_servers":{"eng":{"id":"`+vsEng+`","members":["github","linear"],
		"tools":{"exclude":["github/secret"]},
		"access":{"default_for_org":true,"rules":[{"match":{"key_ids":["k1"]},"allow":["github/*"]},{"match":{"agent_ids":["a1"]},"deny":["*/delete_*"]}]},
		"step_profiles":{"plan":{"include":["*"]},"act":{"include":["github/secret","linear/*","github/delete_*"]}}}}}}`)
	registerTools(f.Server, "github", "list", "delete_repo", "secret")
	registerTools(f.Server, "linear", "create", "delete_t")
	for _, p := range []struct{ key, agent string }{{"k1", ""}, {"k2", ""}, {"k2", "a1"}} {
		for _, header := range []string{"", "linear__*"} {
			f.principal.KeyID, f.agentID = p.key, p.agent
			base := map[string]string{}
			if header != "" {
				base[HeaderMCPAllowTools] = header
			}
			unstepped := map[string]bool{}
			for _, n := range f.toolNames(t, "", base) {
				unstepped[n] = true
			}
			for _, step := range []string{"plan", "act"} {
				h := map[string]string{headerStep: step}
				for k, v := range base {
					h[k] = v
				}
				for _, n := range f.toolNames(t, "", h) {
					if !unstepped[n] {
						t.Errorf("%+v header %q step %s offers %s, which the same request without a step does not see", p, header, step, n)
					}
				}
			}
		}
	}
}

// registerMany registers n tools on server: create_issue, list_issues and
// close_ticket, then tool_003 onwards, each described in plain words.
func registerMany(s *Server, server string, n int) {
	var tools []Tool
	for i := 0; i < n; i++ {
		name := fmt.Sprintf("tool_%03d", i)
		desc := fmt.Sprintf("Does task number %d for the team, with its options and a summary of what changed.", i)
		switch i {
		case 0:
			name, desc = "create_issue", "Create an issue in a repository."
		case 1:
			name, desc = "list_issues", "List the open issues of a repository."
		case 2:
			name, desc = "close_ticket", "Close a support ticket."
		}
		tools = append(tools, Tool{Name: name, Description: desc,
			InputSchema: json.RawMessage(`{"type":"object","properties":{"id":{"type":"string","description":"The id."},"note":{"type":"string"}},"required":["id"]}`)})
	}
	s.Registry().RegisterServer(server, tools)
}

// listItems posts tools/list and returns its items as sent, and its _meta.
func (f *vsFixture) listItems(t *testing.T, headers map[string]string) ([]json.RawMessage, map[string]json.RawMessage) {
	t.Helper()
	resp := decodeResponse(t, f.post(t, "", MethodToolsList, struct{}{}, headers))
	if resp.Error != nil {
		t.Fatalf("tools/list: %+v", resp.Error)
	}
	var res struct {
		Tools []json.RawMessage          `json:"tools"`
		Meta  map[string]json.RawMessage `json:"_meta"`
	}
	if err := json.Unmarshal(resp.Result, &res); err != nil {
		t.Fatal(err)
	}
	return res.Tools, res.Meta
}

// TestSearchModeThreeTools: a search-mode virtual server lists exactly
// call_tool, describe_tool and search_tools, in byte order, each with an
// object input schema naming its required arguments, the two that only
// read the listing marked read-only; none of the members' tools is listed.
// A caller the access rules let see no tool is offered the same three.
func TestSearchModeThreeTools(t *testing.T) {
	f := newVSFixture(t, searchVS(`["github"]`, `"access":{"default_for_org":true,"rules":[{"match":{"key_ids":["k-none"]},"deny":["*"]}]}`))
	registerMany(f.Server, "github", 120)
	for _, key := range []string{"k1", "k-none"} {
		f.principal.KeyID = key
		items, _ := f.listItems(t, nil)
		var got []string
		for _, raw := range items {
			var tl struct {
				Name        string `json:"name"`
				InputSchema struct {
					Type     string   `json:"type"`
					Required []string `json:"required"`
				} `json:"inputSchema"`
				Annotations *ToolAnnotations `json:"annotations"`
			}
			if err := json.Unmarshal(raw, &tl); err != nil {
				t.Fatal(err)
			}
			got = append(got, tl.Name)
			want := map[string]string{"call_tool": "name", "describe_tool": "name", "search_tools": "query"}[tl.Name]
			if tl.InputSchema.Type != "object" || strings.Join(tl.InputSchema.Required, ",") != want {
				t.Fatalf("%s input schema %+v, want an object requiring %q", tl.Name, tl.InputSchema, want)
			}
			readOnly := tl.Annotations != nil && tl.Annotations.ReadOnlyHint != nil && *tl.Annotations.ReadOnlyHint
			if readOnly != (tl.Name != "call_tool") {
				t.Fatalf("%s readOnlyHint %v: only search_tools and describe_tool only read", tl.Name, readOnly)
			}
		}
		if strings.Join(got, ",") != "call_tool,describe_tool,search_tools" {
			t.Fatalf("key %s: search mode lists %v, want the three meta-tools", key, got)
		}
	}
}

// TestSavingsRecorded: a search-mode list says in
// _meta["io.futureagi/tool_list"] how many tools the caller could see and
// how many it was sent, and the tokens each list costs, estimated as
// characters / 4: tokens_full is exactly the estimate of the list the same
// server sends in direct mode, tokens_sent that of the three meta-tools
// sent, and at 120 tools search mode sends less than a tenth.
func TestSavingsRecorded(t *testing.T) {
	access := `"access":{"default_for_org":true,"rules":[{"match":{"key_ids":["k1"]},"deny":["github/tool_11*"]}]}`
	f := newVSFixture(t, `{"mcp":{"virtual_servers":{"eng":{"id":"`+vsEng+`","members":["github"],`+access+`}}}}`)
	registerMany(f.Server, "github", 120)
	directItems, directMeta := f.listItems(t, nil)
	if _, ok := directMeta[MetaKeyToolList]; ok || len(directItems) != 110 {
		t.Fatalf("direct mode: %d tools and %s %s, want the 110 tools k1 may see and no %s", len(directItems), MetaKeyToolList, directMeta[MetaKeyToolList], MetaKeyToolList)
	}
	f.cfg = orgConfig(t, searchVS(`["github"]`, access))
	searchItems, searchMeta := f.listItems(t, nil)
	var meta map[string]any
	if err := json.Unmarshal(searchMeta[MetaKeyToolList], &meta); err != nil {
		t.Fatalf("search mode %s = %s: %v", MetaKeyToolList, searchMeta[MetaKeyToolList], err)
	}
	tokens := func(items []json.RawMessage) float64 {
		chars := 0
		for _, raw := range items {
			chars += utf8.RuneCount(raw)
		}
		return float64((chars + 3) / 4)
	}
	if meta["mode"] != "search" || meta["tools_full"] != float64(110) || meta["tools_sent"] != float64(3) || meta["estimated"] != true {
		t.Fatalf("search mode %s = %v, want mode search, tools_full 110, tools_sent 3, estimated", MetaKeyToolList, meta)
	}
	if meta["tokens_full"] != tokens(directItems) || meta["tokens_sent"] != tokens(searchItems) {
		t.Fatalf("tokens_full %v tokens_sent %v, want %v (the direct list) and %v (the meta-tools)", meta["tokens_full"], meta["tokens_sent"], tokens(directItems), tokens(searchItems))
	}
	if full, sent := meta["tokens_full"].(float64), meta["tokens_sent"].(float64); sent*10 >= full {
		t.Fatalf("search mode sends %v tokens of %v: want below a tenth", sent, full)
	}
}
