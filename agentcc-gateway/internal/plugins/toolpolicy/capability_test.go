package toolpolicy

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// requestWithTools parses a tools array the way a caller writes it, so the
// entry's shape is decided by models.Tool.UnmarshalJSON rather than by the
// test building a struct the long way.
func requestWithTools(t *testing.T, toolsJSON string) *models.RequestContext {
	t.Helper()
	var req models.ChatCompletionRequest
	body := `{"model":"gemini-3.6-flash","messages":[{"role":"user","content":"hi"}],"tools":` + toolsJSON + `}`
	if err := json.Unmarshal([]byte(body), &req); err != nil {
		t.Fatalf("reading the request: %v", err)
	}
	rc := models.AcquireRequestContext()
	rc.Request = &req
	return rc
}

// The policy must enforce the capability the provider will forward.
//
// It used to read Function.Name only. A provider chooses what to forward from
// the declared type, so an entry naming one capability in its type and another
// in its function passed a policy that allowed the function and then sent the
// type. An entry written as a bare {"google_search": {}} names no function at
// all, so a denylist never matched it.
func TestPolicy_EnforcesTheForwardedCapability(t *testing.T) {
	tests := []struct {
		name       string
		allow      []string
		deny       []string
		tools      string
		wantStatus int // 0 means the request is allowed through
	}{
		{
			name:       "a type that contradicts its function name is refused",
			allow:      []string{"get_weather"},
			tools:      `[{"type":"code_execution","function":{"name":"get_weather"}}]`,
			wantStatus: http.StatusBadRequest,
		},
		{
			name:       "a denied server tool written as a bare key is caught",
			deny:       []string{"google_search"},
			tools:      `[{"google_search":{}}]`,
			wantStatus: http.StatusForbidden,
		},
		{
			name:       "a denied server tool in camelCase is caught",
			deny:       []string{"googleSearch"},
			tools:      `[{"googleSearch":{}}]`,
			wantStatus: http.StatusForbidden,
		},
		{
			name:       "a denied server tool named in type is caught",
			deny:       []string{"code_execution"},
			tools:      `[{"type":"code_execution"}]`,
			wantStatus: http.StatusForbidden,
		},
		{
			name:       "an allowlist that omits the server tool refuses it",
			allow:      []string{"get_weather"},
			tools:      `[{"google_search":{}}]`,
			wantStatus: http.StatusForbidden,
		},
		{
			name:       "an allowlist naming the server tool admits it",
			allow:      []string{"google_search"},
			tools:      `[{"google_search":{}}]`,
			wantStatus: 0,
		},
		{
			name:       "a plain function is unaffected",
			allow:      []string{"get_weather"},
			tools:      `[{"type":"function","function":{"name":"get_weather"}}]`,
			wantStatus: 0,
		},
		{
			name:       "a denied plain function is still caught",
			deny:       []string{"get_weather"},
			tools:      `[{"type":"function","function":{"name":"get_weather"}}]`,
			wantStatus: http.StatusForbidden,
		},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			p := New(config.ToolPolicyConfig{
				Enabled:       true,
				DefaultAction: "reject",
				Allow:         tc.allow,
				Deny:          tc.deny,
			}, nil)

			rc := requestWithTools(t, tc.tools)
			defer rc.Release()

			res := p.ProcessRequest(context.Background(), rc)

			if tc.wantStatus == 0 {
				if res.Error != nil {
					t.Fatalf("the request was refused: %s", res.Error.Message)
				}
				if len(rc.Request.Tools) != 1 {
					t.Errorf("tools = %d after filtering, want the entry kept", len(rc.Request.Tools))
				}
				return
			}
			if res.Error == nil {
				t.Fatalf("the request passed; the capability reaches the provider unchecked")
			}
			if res.Error.Status != tc.wantStatus {
				t.Errorf("status = %d (%s), want %d", res.Error.Status, res.Error.Code, tc.wantStatus)
			}
		})
	}
}

// The identity the policy reads and the identity a provider forwards come from
// one method, so they cannot drift apart again.
func TestToolCapability_IsTheForwardedIdentity(t *testing.T) {
	tests := []struct {
		tools string
		want  string
	}{
		{`[{"type":"function","function":{"name":"get_weather"}}]`, "get_weather"},
		{`[{"function":{"name":"get_weather"}}]`, "get_weather"},
		{`[{"google_search":{}}]`, "google_search"},
		{`[{"googleSearch":{}}]`, "google_search"}, // canonicalized: one identity per capability
		{`[{"type":"code_execution"}]`, "code_execution"},
		{`[{"type":"web_search_20250305","max_uses":5}]`, "web_search_20250305"},
		{`[{"type":"code_execution","function":{"name":"get_weather"}}]`, "code_execution"},
	}

	for _, tc := range tests {
		t.Run(tc.want, func(t *testing.T) {
			rc := requestWithTools(t, tc.tools)
			defer rc.Release()
			if got := rc.Request.Tools[0].Capability(); got != tc.want {
				t.Errorf("Capability() = %q, want %q", got, tc.want)
			}
		})
	}
}

// A built-in capability has two accepted spellings and a provider treats them
// as one. A policy must too: with only the supplied spelling to go on,
// deny: [code_execution] let {"codeExecution":{}} through to the upstream.
func TestPolicy_DeniesACapabilityInEitherSpelling(t *testing.T) {
	pairs := [][2]string{
		{"google_search", "googleSearch"},
		{"google_search_retrieval", "googleSearchRetrieval"},
		{"url_context", "urlContext"},
		{"code_execution", "codeExecution"},
	}

	for _, pair := range pairs {
		for _, configured := range pair {
			for _, requested := range pair {
				for _, shape := range []string{"bare", "typed"} {
					for _, action := range []string{"reject", "strip"} {
						name := fmt.Sprintf("deny %s / send %s / %s / %s", configured, requested, shape, action)
						t.Run(name, func(t *testing.T) {
							tools := `[{"` + requested + `":{}}]`
							if shape == "typed" {
								tools = `[{"type":"` + requested + `"}]`
							}

							p := New(config.ToolPolicyConfig{
								Enabled:       true,
								DefaultAction: action,
								Deny:          []string{configured},
							}, nil)

							rc := requestWithTools(t, tools)
							defer rc.Release()

							res := p.ProcessRequest(context.Background(), rc)

							if action == "reject" {
								if res.Error == nil {
									t.Fatalf("the denied capability passed the policy")
								}
								return
							}
							// strip: the entry must not survive to the provider.
							if res.Error != nil {
								return
							}
							if len(rc.Request.Tools) != 0 {
								t.Errorf("the denied capability survived stripping: %d tools left", len(rc.Request.Tools))
							}
						})
					}
				}
			}
		}
	}
}

// An allowlist naming one spelling admits the other, since they are the same
// capability.
func TestPolicy_AllowsACapabilityInEitherSpelling(t *testing.T) {
	for _, configured := range []string{"code_execution", "codeExecution"} {
		for _, requested := range []string{"code_execution", "codeExecution"} {
			t.Run(configured+"/"+requested, func(t *testing.T) {
				p := New(config.ToolPolicyConfig{
					Enabled:       true,
					DefaultAction: "reject",
					Allow:         []string{configured},
				}, nil)

				rc := requestWithTools(t, `[{"`+requested+`":{}}]`)
				defer rc.Release()

				if res := p.ProcessRequest(context.Background(), rc); res.Error != nil {
					t.Fatalf("an allowed capability was refused: %s", res.Error.Message)
				}
			})
		}
	}
}

// Alias equivalence belongs to provider-executed tools and nowhere else. Two
// spellings of google_search are one capability; two spellings of a function
// name are two different functions, and an application may legitimately call
// one of its own functions "codeExecution".
func TestPolicy_AliasEquivalenceDoesNotReachCustomFunctions(t *testing.T) {
	custom := func(name string) string {
		return `[{"type":"function","function":{"name":"` + name + `"}}]`
	}

	tests := []struct {
		name    string
		allow   []string
		deny    []string
		tools   string
		refused bool
	}{
		{
			name:    "an allowlist for the built-in does not admit a custom function spelled the other way",
			allow:   []string{"code_execution"},
			tools:   custom("codeExecution"),
			refused: true,
		},
		{
			name:    "nor the other direction",
			allow:   []string{"codeExecution"},
			tools:   custom("code_execution"),
			refused: true,
		},
		{
			name:    "a denylist for the built-in does not block a custom function spelled the other way",
			deny:    []string{"code_execution"},
			tools:   custom("codeExecution"),
			refused: false,
		},
		{
			name:    "nor the other direction",
			deny:    []string{"codeExecution"},
			tools:   custom("code_execution"),
			refused: false,
		},
		{
			name:    "a custom function named exactly as configured is still matched",
			deny:    []string{"codeExecution"},
			tools:   custom("codeExecution"),
			refused: true,
		},
		{
			name:    "and an allowlist naming it exactly still admits it",
			allow:   []string{"codeExecution"},
			tools:   custom("codeExecution"),
			refused: false,
		},
		{
			name:    "the built-in itself is still denied under either spelling",
			deny:    []string{"code_execution"},
			tools:   `[{"codeExecution":{}}]`,
			refused: true,
		},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			p := New(config.ToolPolicyConfig{
				Enabled:       true,
				DefaultAction: "reject",
				Allow:         tc.allow,
				Deny:          tc.deny,
			}, nil)

			rc := requestWithTools(t, tc.tools)
			defer rc.Release()

			res := p.ProcessRequest(context.Background(), rc)
			if tc.refused && res.Error == nil {
				t.Errorf("the request passed, want it refused")
			}
			if !tc.refused && res.Error != nil {
				t.Errorf("the request was refused: %s", res.Error.Message)
			}
		})
	}
}

// A custom function keeps the exact name the caller wrote, so the provider
// receives what the application asked for.
func TestToolCapability_KeepsACustomFunctionNameExactly(t *testing.T) {
	for _, name := range []string{"codeExecution", "code_execution", "googleSearch", "get_weather"} {
		t.Run(name, func(t *testing.T) {
			rc := requestWithTools(t, `[{"type":"function","function":{"name":"`+name+`"}}]`)
			defer rc.Release()
			tool := rc.Request.Tools[0]
			if got := tool.Capability(); got != name {
				t.Errorf("Capability() = %q, want %q unchanged", got, name)
			}
			if tool.NamesBuiltinCapability() {
				t.Error("a function was reported as a provider-executed capability")
			}
		})
	}
}
