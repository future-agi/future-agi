package toolpolicy

import (
	"context"
	"net/http"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
	"github.com/futureagi/agentcc-gateway/internal/toolview"
)

// Exercise the actual native policy entry point, including the org-only
// enablement, the path an earlier defect came through.
func TestNativeBuiltinPolicy(t *testing.T) {
	fixtures := []struct {
		name, tool, function, content string
		make                          func([]byte) *toolview.NativeRequest
	}{
		{"web_search", `{"type":"web_search","search_context_size":"low"}`, `{"type":"function","name":"keep"}`, `"input":"hello"`, toolview.Responses},
		{"file_search", `{"type":"file_search","vector_store_ids":["vs_fixture"]}`, `{"type":"function","name":"keep"}`, `"input":"hello"`, toolview.Responses},
		{"code_interpreter", `{"type":"code_interpreter","container":{"type":"auto"}}`, `{"type":"function","name":"keep"}`, `"input":"hello"`, toolview.Responses},
		{"web_search_20250305", `{"type":"web_search_20250305","name":"web_search","max_uses":2}`, `{"name":"keep","input_schema":{}}`, `"messages":[{"role":"user","content":"hello"}]`, toolview.Anthropic},
		{"code_execution_20250825", `{"type":"code_execution_20250825","name":"code_execution"}`, `{"name":"keep","input_schema":{}}`, `"messages":[{"role":"user","content":"hello"}]`, toolview.Anthropic},
	}
	for _, fixture := range fixtures {
		for _, scope := range []string{"node", "org", "key"} {
			for _, action := range []string{"strip", "reject"} {
				for _, rule := range []string{"permissive", "allow", "deny", "allow-other", "deny-over-allow", "max"} {
					t.Run(fixture.name+"/"+scope+"/"+action+"/"+rule, func(t *testing.T) {
						cfg := config.ToolPolicyConfig{Enabled: true, DefaultAction: action}
						switch rule {
						case "allow":
							cfg.Allow = []string{fixture.name, "keep"}
						case "deny":
							cfg.Deny = []string{fixture.name}
						case "allow-other":
							cfg.Allow = []string{"keep"}
						case "deny-over-allow":
							cfg.Allow = []string{fixture.name, "keep"}
							cfg.Deny = []string{fixture.name}
						case "max":
							cfg.MaxToolsPerRequest = 1
						}
						rc := models.AcquireRequestContext()
						defer rc.Release()
						rc.Request = nil
						var store *tenant.Store
						if scope == "org" {
							store = tenant.NewStore()
							if err := store.Set("builtin-fixture", &tenant.OrgConfig{ToolPolicy: &tenant.ToolPolicyConfig{
								Enabled: true, DefaultAction: action, Allow: cfg.Allow, Deny: cfg.Deny, MaxToolsPerRequest: cfg.MaxToolsPerRequest,
							}}); err != nil {
								t.Fatal(err)
							}
							rc.Metadata[tenant.MetadataKeyOrgID] = "builtin-fixture"
							cfg = config.ToolPolicyConfig{}
						} else if scope == "key" {
							rc.Metadata["auth_allowed_tools"] = strings.Join(cfg.Allow, ",")
							rc.Metadata["auth_denied_tools"] = strings.Join(cfg.Deny, ",")
							cfg.Allow, cfg.Deny = nil, nil
						}
						p := New(cfg, store)
						body := `{ ` + fixture.content + `, "tools": [ ` + fixture.tool + `, ` + fixture.function + ` ], "metadata":{"n":1e+03} }`
						view := fixture.make([]byte(body))
						rc.PassThrough = view
						err := p.PrepareNative(context.Background(), rc)
						denied := rule == "deny" || rule == "allow-other" || rule == "deny-over-allow"
						if rule == "max" || (denied && action == "reject") {
							wantStatus := http.StatusForbidden
							if rule == "max" {
								wantStatus = http.StatusBadRequest
							}
							if err == nil || err.Status != wantStatus || (rule == "max" && err.Code != "too_many_tools") {
								t.Fatalf("wrong policy refusal: %v", err)
							}
							if string(view.Body()) != body {
								t.Fatal("refused request changed bytes")
							}
							return
						}
						if err != nil {
							t.Fatalf("valid built-in policy request rejected: %v", err)
						}
						want := body
						if denied {
							want = strings.Replace(body, fixture.tool+", ", "", 1)
							if rc.Metadata["tools_filtered"] != "1" {
								t.Fatal("built-in removal not counted")
							}
						}
						if string(view.Body()) != want || rc.Metadata["tools_requested"] != "2" || rc.Request != nil {
							t.Fatal("wrong native bytes, count or request carrier")
						}
						generation := view.Generation()
						if res := p.ProcessRequest(context.Background(), rc); res.Error != nil || string(view.Body()) != want || view.Generation() != generation {
							t.Fatal("prepared built-in policy was not sealed/idempotent")
						}
					})
				}
			}
		}
	}
}

func TestNativeBuiltinPolicyControls(t *testing.T) {
	for _, tc := range []struct {
		name, tool string
		enabled    bool
		refused    bool
	}{
		{"disabled-native", `{"type":"web_search"}`, false, false},
		{"permissive-native", `{"type":"web_search"}`, true, false},
		{"function-control", `{"type":"function","name":"web_search"}`, true, false},
		{"unknown-version", `{"type":"web_search_20990101"}`, true, true},
		{"computer-use", `{"type":"computer_use_preview"}`, true, true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			rc := models.AcquireRequestContext()
			defer rc.Release()
			rc.Request = nil
			body := `{"input":"hello","tools":[` + tc.tool + `]}`
			view := toolview.Responses([]byte(body))
			rc.PassThrough = view
			p := New(config.ToolPolicyConfig{Enabled: tc.enabled, DefaultAction: "strip"}, nil)
			err := p.PrepareNative(context.Background(), rc)
			if (err != nil) != tc.refused || (err != nil && err.Code != "native_tool_policy_unavailable") {
				t.Fatalf("wrong native classification result: %v", err)
			}
			if string(view.Body()) != body {
				t.Fatal("control request changed bytes")
			}
		})
	}
}
