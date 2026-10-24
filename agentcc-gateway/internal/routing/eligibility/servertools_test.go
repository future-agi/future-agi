package eligibility

import (
	"encoding/json"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// toolReq is a chat request carrying the tools whose types are given, each
// with the fields a caller sends (a server tool keeps its own bytes).
func toolReq(t *testing.T, types ...string) *models.ChatCompletionRequest {
	t.Helper()
	req := &models.ChatCompletionRequest{Model: "claude-sonnet-5.5"}
	for _, typ := range types {
		raw := `{"type":"` + typ + `","name":"` + strings.SplitN(typ, "_2", 2)[0] + `","max_uses":3}`
		if typ == "function" {
			raw = `{"type":"function","function":{"name":"lookup","parameters":{"type":"object"}}}`
		}
		var tool models.Tool
		if err := json.Unmarshal([]byte(raw), &tool); err != nil {
			t.Fatal(err)
		}
		req.Tools = append(req.Tools, tool)
	}
	return req
}

// claudeOn is a BYOK candidate claude-sonnet-5.5 on provider id, of type
// host (the provider's api_format, as Input.Host carries it), for an org
// whose own config for id names baseURL ("" for none).
func claudeOn(id, host, baseURL string, req *models.ChatCompletionRequest) Input {
	org := &tenant.OrgConfig{}
	if baseURL != "" {
		org.Providers = map[string]*tenant.ProviderConfig{id: {Enabled: true, APIKey: "k", BaseURL: baseURL, APIFormat: host}}
	}
	return Input{Model: "claude-sonnet-5.5", ProviderID: id, Host: host, Org: org, Request: req, Now: testNow}
}

// judgeAll checks in as an explicit route, an auto candidate and a
// fallback hop (an explicit check of the hop's own provider): the rule must
// give the same answer on every path.
func judgeAll(t *testing.T, name string, in Input, wantCode string) {
	t.Helper()
	for _, auto := range []bool{false, true} {
		in.Auto = auto
		v := Check(&in)
		switch {
		case wantCode == "" && !v.OK:
			t.Errorf("%s (auto=%v): refused %s: %s", name, auto, v.Code, v.Reason)
		case wantCode != "" && (v.OK || v.Code != wantCode || v.Reason == ""):
			t.Errorf("%s (auto=%v): verdict %+v, want %s with a reason", name, auto, v, wantCode)
		}
	}
}

// Bedrock runs no Anthropic web search: a request carrying any version is
// refused there on every path, explicit, auto or fallback.
func TestWebSearchNeverToBedrock(t *testing.T) {
	for _, v := range []string{"web_search_20250305", "web_search_20260209", "web_search_20260318"} {
		judgeAll(t, "bedrock "+v, claudeOn("bedrock", "bedrock", "", toolReq(t, v)), CodeServerToolUnsupported)
		// Bedrock's Messages-compatible endpoint, configured as Anthropic
		// format, is still Bedrock.
		judgeAll(t, "anthropic-format bedrock "+v,
			claudeOn("org-bedrock", "anthropic", "https://bedrock-runtime.us-east-1.amazonaws.com/anthropic", toolReq(t, v)), CodeServerToolUnsupported)
	}
	in := claudeOn("bedrock", "bedrock", "", toolReq(t, "web_search_20250305"))
	if v := Check(&in); StatusFor(v.Code) != 403 || !strings.Contains(v.Reason, "web_search_20250305") || !strings.Contains(v.Reason, "bedrock") {
		t.Errorf("refusal = %+v, want a 403 naming the tool and the source", v)
	}
	// Function tools and requests without tools are untouched.
	judgeAll(t, "bedrock function tool", claudeOn("bedrock", "bedrock", "", toolReq(t, "function")), "")
	judgeAll(t, "bedrock no tools", claudeOn("bedrock", "bedrock", "", &models.ChatCompletionRequest{}), "")
	judgeAll(t, "bedrock no request", claudeOn("bedrock", "bedrock", "", nil), "")
}

// Claude Platform on AWS is operated by Anthropic and speaks Anthropic's
// own API, so it runs every version; it is a valid AWS route, unlike Bedrock.
func TestWebSearchAllowedOnClaudePlatformAWS(t *testing.T) {
	tools := toolReq(t, "web_search_20260318", "web_fetch_20260318", "code_execution_20260521")
	for _, base := range []string{"https://aws.claude-platform.example.com", "https://claude.us-east-1.amazonaws.com"} {
		in := claudeOn("org-claude-aws", "anthropic", base, tools)
		in.DataRegion = "us-east-1"
		judgeAll(t, "claude platform on aws "+base, in, "")
	}
	judgeAll(t, "claude api", claudeOn("anthropic", "anthropic", "", tools), "")
	judgeAll(t, "claude api with base url", claudeOn("org-anthropic", "anthropic", "https://api.anthropic.com", tools), "")
	// The same tools on Bedrock in the same region are refused.
	judgeAll(t, "bedrock us-east-1", claudeOn("bedrock", "bedrock", "", tools), CodeServerToolUnsupported)
}

// Google Cloud and Azure-hosted Foundry run only the first web search
// version: 20260209 and later are refused there, 20250305 is not.
func TestNewSearchVersionsNotToGoogleCloudOrAzureFoundry(t *testing.T) {
	sources := map[string]string{
		"google cloud":         "https://us-east5-aiplatform.googleapis.com/v1/projects/p/locations/us-east5/publishers/anthropic",
		"azure-hosted foundry": "https://acme.services.ai.azure.com/anthropic",
	}
	for name, base := range sources {
		judgeAll(t, name+" 20250305", claudeOn("org-claude", "anthropic", base, toolReq(t, "web_search_20250305")), "")
		for _, v := range []string{"web_search_20260209", "web_search_20260318"} {
			judgeAll(t, name+" "+v, claudeOn("org-claude", "anthropic", base, toolReq(t, v)), CodeServerToolUnsupported)
		}
		// One unsupported tool refuses the request, however many others run.
		judgeAll(t, name+" mixed", claudeOn("org-claude", "anthropic", base, toolReq(t, "web_search_20250305", "web_search_20260209")), CodeServerToolUnsupported)
	}
}

// Detection reads the date suffix of the tool type, so web_search_20260318
// (response inclusion) is its own version: refused where only the first
// version runs, allowed on the Claude API.
func TestWebSearch20260318Detected(t *testing.T) {
	req := toolReq(t, "function", "web_search_20260318")
	in := claudeOn("org-claude", "anthropic", "https://acme.services.ai.azure.com/anthropic", req)
	v := Check(&in)
	if v.OK || v.Code != CodeServerToolUnsupported || !strings.Contains(v.Reason, "web_search_20260318") {
		t.Fatalf("verdict = %+v, want server_tool_unsupported naming web_search_20260318", v)
	}
	judgeAll(t, "claude api 20260318", claudeOn("anthropic", "anthropic", "", req), "")
	// A type that only resembles a server tool is not one.
	for _, typ := range []string{"web_search_preview", "web_search", "web_search_2026031", "bash_20250124"} {
		judgeAll(t, "lookalike "+typ, claudeOn("bedrock", "bedrock", "", toolReq(t, typ)), "")
	}
}

// Web fetch runs on Anthropic's own API and on Foundry, never on Bedrock or
// Google Cloud.
func TestWebFetchNotToBedrockOrGoogle(t *testing.T) {
	for _, v := range []string{"web_fetch_20250910", "web_fetch_20260209", "web_fetch_20260309", "web_fetch_20260318"} {
		req := toolReq(t, v)
		judgeAll(t, "bedrock "+v, claudeOn("bedrock", "bedrock", "", req), CodeServerToolUnsupported)
		judgeAll(t, "google cloud "+v, claudeOn("org-claude", "anthropic", "https://europe-west1-aiplatform.googleapis.com/v1", req), CodeServerToolUnsupported)
		judgeAll(t, "foundry "+v, claudeOn("org-claude", "anthropic", "https://acme.services.ai.azure.com/anthropic", req), "")
		judgeAll(t, "claude api "+v, claudeOn("anthropic", "anthropic", "", req), "")
	}
}

// Code execution runs only where Anthropic operates the model: not on
// Bedrock or Google Cloud, and Azure-hosted Foundry answers it with a 400.
// Another vendor's model on its own API (GPT, Gemini) runs no Anthropic tool
// (it would fail, or drop the tool unseen), so it is refused there too, on
// every path, which keeps auto from choosing it, as is a Gemini, Vertex AI
// or Cohere provider whatever its model. An OpenAI-compatible endpoint
// serving Claude or a model of no known vendor may be a proxy that runs the
// tool, and an aggregator decides for itself: neither is refused.
func TestCodeExecutionNotToBedrockGoogleOrAzureFoundry(t *testing.T) {
	req := toolReq(t, "code_execution_20250825")
	judgeAll(t, "bedrock", claudeOn("bedrock", "bedrock", "", req), CodeServerToolUnsupported)
	judgeAll(t, "google cloud", claudeOn("org-claude", "anthropic", "https://us-east5-aiplatform.googleapis.com/v1", req), CodeServerToolUnsupported)
	judgeAll(t, "azure foundry", claudeOn("org-claude", "anthropic", "https://acme.services.ai.azure.com/anthropic", req), CodeServerToolUnsupported)
	judgeAll(t, "claude api", claudeOn("anthropic", "anthropic", "", req), "")
	for _, c := range []struct{ host, model string }{
		{"openai", "gpt-4o"}, {"azure", "gpt-4.1"}, {"openai", "o4-mini"},
		{"gemini", "gemini-3-pro"}, {"vertex", "gemini-2.5-flash"}, {"openai", "google/gemini-3-pro"},
		// A Gemini, Vertex AI or Cohere provider speaks its own API: given
		// Claude or a model of no known vendor, it still runs no Anthropic tool.
		{"gemini", "claude-sonnet-5.5"}, {"vertex", "claude-sonnet-5.5"}, {"vertex", "org-model"},
		{"cohere", "command-z"}, {"cohere", "claude-sonnet-5.5"},
	} {
		in := claudeOn("p", c.host, "", req)
		in.Model = c.model
		judgeAll(t, c.host+" "+c.model, in, CodeServerToolUnsupported)
	}
	for _, c := range []struct{ host, model string }{
		{"openai", "claude-sonnet-5.5"}, {"openai", "anthropic/claude-sonnet-5.5"}, {"openai", "org-model"},
		{"azure", "claude-sonnet-5.5"}, {"azure", "org-model"},
	} {
		in := claudeOn("proxy", c.host, "", req)
		in.Model = c.model
		judgeAll(t, "proxy "+c.host+" "+c.model, in, "")
	}
	agg := claudeOn("openrouter", "openai", "", req)
	agg.Model, agg.Aggregator = "openai/gpt-4o", true
	judgeAll(t, "aggregator", agg, "")
	unknown := claudeOn("a2a", "", "", req)
	judgeAll(t, "unknown provider type", unknown, "")
}

// The rule is the last in the registration list and allocates nothing for
// a request it allows.
func TestServerToolRuleRegisteredLastAndAllocationFree(t *testing.T) {
	names := RuleNames()
	if names[len(names)-1] != "server_tools" {
		t.Fatalf("rules = %v, want server_tools last", names)
	}
	in := claudeOn("anthropic", "anthropic", "", toolReq(t, "function", "web_search_20260318"))
	if allocs := testing.AllocsPerRun(200, func() { _ = Check(&in) }); allocs != 0 {
		t.Fatalf("Check allocates %.0f for an allowed server-tool request", allocs)
	}
}

// An org config counts only when the request would use it: enabled and
// holding credentials, as the server's orgProviderConfig decides. Without
// them the route is the configured provider's, judged by its type, so an
// org entry naming Google Cloud but holding no key does not refuse the
// configured Anthropic provider, and one naming Anthropic's API does not
// open Google Cloud's.
func TestServerToolRouteUsesOnlyCredentialedOrgConfig(t *testing.T) {
	req := toolReq(t, "web_search_20260209")
	for name, pc := range map[string]*tenant.ProviderConfig{
		"no key":   {Enabled: true, BaseURL: "https://us-east5-aiplatform.googleapis.com/v1", APIFormat: "anthropic"},
		"disabled": {APIKey: "k", BaseURL: "https://us-east5-aiplatform.googleapis.com/v1", APIFormat: "anthropic"},
	} {
		in := claudeOn("anthropic", "anthropic", "", req)
		in.Org.Providers = map[string]*tenant.ProviderConfig{"anthropic": pc}
		judgeAll(t, "uncredentialed google cloud org entry ("+name+")", in, "")
	}
	// The same entry with a key is the route, and refuses the new version.
	judgeAll(t, "credentialed google cloud org entry",
		claudeOn("anthropic", "anthropic", "https://us-east5-aiplatform.googleapis.com/v1", req), CodeServerToolUnsupported)
}
