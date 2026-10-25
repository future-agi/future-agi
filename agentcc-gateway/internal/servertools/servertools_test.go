package servertools_test

import (
	"encoding/json"
	"os"
	"testing"

	feedata "github.com/futureagi/agentcc-gateway/catalog/servertools"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/servertools"
)

// The shipped table parses, holds every listed fee at its list price,
// and its embedded copy is the file on disk.
func TestShippedFeeTable(t *testing.T) {
	fees, err := feedata.Fees()
	if err != nil {
		t.Fatal(err)
	}
	disk, err := os.ReadFile("../../catalog/servertools/fees.json")
	if err != nil {
		t.Fatal(err)
	}
	if again, err := servertools.ParseFees(disk); err != nil || again.Len() != fees.Len() {
		t.Fatalf("the file on disk parses to %d rows (%v), the embedded table %d", again.Len(), err, fees.Len())
	}
	for _, tc := range []struct {
		provider string
		tool     servertools.Tool
		model    string
		gb       int
		unit     string
		usd      float64
	}{
		{"anthropic", servertools.WebSearch, "claude-sonnet-5.5", 0, "request", 0.01},
		{"anthropic", servertools.WebFetch, "claude-opus-5", 0, "request", 0},
		{"anthropic", servertools.CodeExecution, "claude-haiku-5", 0, "container_hour", 0.05},
		{"openai", servertools.WebSearch, "gpt-4.1", 0, "request", 0.01},
		{"openai", servertools.WebSearchPreview, "gpt-4o", 0, "request", 0.025},
		{"openai", servertools.WebSearchPreview, "o3", 0, "request", 0.01},
		{"openai", servertools.WebSearchPreview, "gpt-5-mini", 0, "request", 0.01},
		{"openai", servertools.FileSearch, "gpt-4.1", 0, "request", 0.0025},
		{"openai", servertools.FileSearchStorage, "gpt-4.1", 0, "gb_day", 0.10},
		{"openai", servertools.Container, "gpt-5", 1, "container_session", 0.03},
		{"openai", servertools.Container, "gpt-5", 4, "container_session", 0.12},
		{"openai", servertools.Container, "gpt-5", 16, "container_session", 0.48},
		{"openai", servertools.Container, "gpt-5", 64, "container_session", 1.92},
		{"google", servertools.GoogleSearch, "gemini-3-pro", 0, "query", 0.014},
		{"google", servertools.GoogleSearch, "google/gemini-3.1-flash", 0, "query", 0.014},
		{"google", servertools.GoogleSearch, "gemini-2.5-flash", 0, "grounded_prompt", 0.035},
		{"google", servertools.GoogleMaps, "gemini-3-pro", 0, "grounded_prompt", 0.014},
		{"google", servertools.GoogleMaps, "gemini-2.5-pro", 0, "grounded_prompt", 0.025},
	} {
		f, ok := fees.Lookup(tc.provider, tc.tool, tc.model, tc.gb)
		if !ok || f.Unit != tc.unit || f.USDPerUnit != tc.usd || f.Source == "" {
			t.Errorf("%s %s %s %dGB = %+v (found %v), want %s at $%v with a source", tc.provider, tc.tool, tc.model, tc.gb, f, ok, tc.unit, tc.usd)
		}
	}
	if f, _ := fees.Lookup("openai", servertools.WebSearch, "gpt-4o-mini-2024-07-18", 0); f.InputTokensPerUnit != 8000 {
		t.Errorf("gpt-4o-mini search block = %d tokens, want 8,000", f.InputTokensPerUnit)
	}
	if f, _ := fees.Lookup("openai", servertools.FileSearchStorage, "x", 0); f.IsBilled() {
		t.Error("file-search storage is billed per request; it accrues on the store's own account")
	}
	if f, ok := fees.Lookup("openai", servertools.Container, "gpt-5", 2); ok {
		t.Errorf("a 2 GB container found a row: %+v", f)
	}
	if got := fees.SmallestContainerGB("openai"); got != 1 {
		t.Errorf("smallest container = %d GB, want 1", got)
	}
}

func TestParseFeesRefusesBadRows(t *testing.T) {
	row := func(extra string) []byte {
		return []byte(`{"version":1,"fees":[{"provider":"p","tool":"t","unit":"request","usd_per_unit":0.01,"source":"s"` + extra + `}]}`)
	}
	if _, err := servertools.ParseFees(row("")); err != nil {
		t.Fatalf("a plain row: %v", err)
	}
	for name, data := range map[string][]byte{
		"version":            []byte(`{"version":2,"fees":[]}`),
		"unknown unit":       []byte(`{"version":1,"fees":[{"provider":"p","tool":"t","unit":"hour","usd_per_unit":1,"source":"s"}]}`),
		"negative price":     []byte(`{"version":1,"fees":[{"provider":"p","tool":"t","unit":"request","usd_per_unit":-1,"source":"s"}]}`),
		"no source":          []byte(`{"version":1,"fees":[{"provider":"p","tool":"t","unit":"request","usd_per_unit":1}]}`),
		"no provider":        []byte(`{"version":1,"fees":[{"tool":"t","unit":"request","usd_per_unit":1,"source":"s"}]}`),
		"session without gb": []byte(`{"version":1,"fees":[{"provider":"p","tool":"t","unit":"container_session","usd_per_unit":1,"session_minutes":20,"source":"s"}]}`),
		"billed storage":     []byte(`{"version":1,"fees":[{"provider":"p","tool":"t","unit":"gb_day","usd_per_unit":1,"source":"s"}]}`),
		"bad glob":           row(`,"models":["gemini-[3"]`),
		"bad free_with":      row(`,"free_with":["web_search>=2026"]`),
		"free_with non-tool": row(`,"free_with":["bash>=20250124"]`),
		"not json":           []byte(`{`),
		"negative min":       row(`,"min_minutes":-5`),
		"negative tokens":    row(`,"input_tokens_per_unit":-1`),
		"string price":       []byte(`{"version":1,"fees":[{"provider":"p","tool":"t","unit":"request","usd_per_unit":"0.01","source":"s"}]}`),
	} {
		if _, err := servertools.ParseFees(data); err == nil {
			t.Errorf("%s: parsed", name)
		}
	}
}

func TestParseTool(t *testing.T) {
	for typ, want := range map[string][2]string{
		"web_search_20250305":     {"web_search", "20250305"},
		"web_search_20260318":     {"web_search", "20260318"},
		"web_fetch_20250910":      {"web_fetch", "20250910"},
		"code_execution_20260521": {"code_execution", "20260521"},
	} {
		tool, v, ok := servertools.ParseTool(typ)
		if !ok || string(tool) != want[0] || v != want[1] {
			t.Errorf("ParseTool(%q) = %q %q %v, want %v", typ, tool, v, ok, want)
		}
	}
	for _, typ := range []string{"", "function", "web_search", "web_search_preview", "web_search_2026031", "web_search_202603189",
		"web_search_2026O318", "bash_20250124", "text_editor_20250728", "web_searchx20250305"} {
		if tool, v, ok := servertools.ParseTool(typ); ok {
			t.Errorf("ParseTool(%q) = %q %q, want not a server tool", typ, tool, v)
		}
	}
}

func TestDetectTakesTheLatestVersion(t *testing.T) {
	req := &models.ChatCompletionRequest{}
	for _, typ := range []string{"web_search_20250305", "function", "web_search_20260209", "web_fetch_20260318", "code_execution_20250825"} {
		var tool models.Tool
		if err := json.Unmarshal([]byte(`{"type":"`+typ+`"}`), &tool); err != nil {
			t.Fatal(err)
		}
		req.Tools = append(req.Tools, tool)
	}
	got := servertools.Detect(req)
	if got != (servertools.Requested{WebSearch: "20260209", WebFetch: "20260318", CodeExecution: "20250825"}) || !got.Any() {
		t.Fatalf("Detect = %+v", got)
	}
	if servertools.Detect(nil).Any() || servertools.Detect(&models.ChatCompletionRequest{}).Any() {
		t.Fatal("no request detected tools")
	}
}

func TestSourceOf(t *testing.T) {
	for _, tc := range []struct{ host, base, want string }{
		{"anthropic", "", servertools.SourceClaudeAPI},
		{"anthropic", "https://api.anthropic.com", servertools.SourceClaudeAPI},
		{"Anthropic", "HTTPS://US-EAST5-AIPLATFORM.GOOGLEAPIS.COM/v1", servertools.SourceGoogleCloud},
		{"anthropic", "https://acme.services.ai.azure.com/anthropic", servertools.SourceAzureFoundry},
		{"anthropic", "https://acme.openai.azure.com:443/anthropic", servertools.SourceAzureFoundry},
		{"anthropic", "https://claude.us-east-1.amazonaws.com", servertools.SourceClaudePlatformAWS},
		{"anthropic", "https://bedrock-runtime.us-east-1.amazonaws.com", servertools.SourceBedrock},
		{"bedrock", "https://bedrock-runtime.us-east-1.amazonaws.com", servertools.SourceBedrock},
		{"openai", "https://api.openai.com/v1", servertools.SourceNone},
		{"vertex", "https://us-central1-aiplatform.googleapis.com", servertools.SourceNone},
		{"", "https://api.anthropic.com", servertools.SourceUnknown},
	} {
		if got := servertools.SourceOf(tc.host, tc.base); got != tc.want {
			t.Errorf("SourceOf(%q, %q) = %q, want %q", tc.host, tc.base, got, tc.want)
		}
	}
}

func TestSupportTable(t *testing.T) {
	all := servertools.Support("anthropic", servertools.SourceClaudeAPI)
	for _, tool := range []servertools.Tool{servertools.WebSearch, servertools.WebFetch, servertools.CodeExecution} {
		if !all[tool].All {
			t.Errorf("claude api %s = %+v, want every version", tool, all[tool])
		}
	}
	if aws := servertools.Support("anthropic", servertools.SourceClaudePlatformAWS); len(aws) != 3 || !aws[servertools.WebSearch].Has("20260318") {
		t.Errorf("claude platform on aws = %+v, want every tool", aws)
	}
	if got := servertools.Support("anthropic", servertools.SourceBedrock); len(got) != 0 {
		t.Errorf("bedrock = %+v, want nothing", got)
	}
	g := servertools.Support("anthropic", servertools.SourceGoogleCloud)
	if len(g) != 1 || !g[servertools.WebSearch].Has("20250305") || g[servertools.WebSearch].Has("20260209") {
		t.Errorf("google cloud = %+v, want web_search 20250305 only", g)
	}
	f := servertools.Support("anthropic", servertools.SourceAzureFoundry)
	if len(f) != 2 || f[servertools.WebSearch].Has("20260209") || !f[servertools.WebFetch].Has("20260318") {
		t.Errorf("azure foundry = %+v, want web_search 20250305 and every web_fetch", f)
	}
	if got := servertools.Support("openai", servertools.SourceClaudeAPI); len(got) != 0 {
		t.Errorf("openai tools are not routed by type: %+v", got)
	}
	// The map is the caller's: changing it changes no later answer.
	all[servertools.WebSearch] = servertools.Versions{}
	if !servertools.Support("anthropic", servertools.SourceClaudeAPI)[servertools.WebSearch].All {
		t.Fatal("Support handed out its own table")
	}
	if !servertools.Runs(servertools.SourceUnknown, servertools.CodeExecution, "20990101") {
		t.Error("an unknown source refused a tool")
	}
}

func TestVendor(t *testing.T) {
	for _, tc := range []struct{ provider, model, want string }{
		{"openrouter", "anthropic/claude-sonnet-5.5", "anthropic"},
		{"my-claude", "claude-sonnet-5.5", "anthropic"},
		{"openrouter", "google/gemini-3-pro", "google"},
		{"vertex", "gemini-2.5-flash", "google"},
		{"openai", "gpt-4.1", "openai"},
		{"azure-east", "o4-mini", "openai"},
		{"openai", "custom-finetune", "openai"},
		{"gemini", "tuned-model", "google"},
		{"mystery", "mystery-1", ""},
		{"openrouter", "x-ai/grok-5", ""},
	} {
		if got := servertools.Vendor(tc.provider, tc.model); got != tc.want {
			t.Errorf("Vendor(%q, %q) = %q, want %q", tc.provider, tc.model, got, tc.want)
		}
	}
}

func TestGeminiGrounded(t *testing.T) {
	for _, u := range []*models.ServerToolUsage{{WebSearchQueries: 1}, {GroundedPrompts: 1}, {MapsGroundedPrompts: 1}} {
		if !servertools.GeminiGrounded(u) {
			t.Errorf("%+v not grounded", u)
		}
	}
	for _, u := range []*models.ServerToolUsage{nil, {}, {WebSearchRequests: 3, WebFetchRequests: 1}} {
		if servertools.GeminiGrounded(u) {
			t.Errorf("%+v grounded", u)
		}
	}
}

// An OpenAI-compatible provider is SourceNone only for a known
// non-Anthropic vendor's model, since it may be a proxy that serves Claude
// and runs its tools; a Gemini, Vertex AI or Cohere provider speaks only its
// own API and is SourceNone whatever its model.
func TestRouteSource(t *testing.T) {
	for _, tc := range []struct{ host, base, provider, model, want string }{
		{"openai", "", "openai", "gpt-4o", servertools.SourceNone},
		{"gemini", "", "gemini", "gemini-3-pro", servertools.SourceNone},
		{"openai", "", "litellm", "claude-sonnet-5.5", servertools.SourceUnknown},
		{"openai", "", "operator", "org-model", servertools.SourceUnknown},
		{"azure", "", "azure-east", "claude-sonnet-5.5", servertools.SourceUnknown},
		{"vertex", "", "org-vertex", "claude-sonnet-5.5", servertools.SourceNone},
		{"Gemini", "", "org-gemini", "org-model", servertools.SourceNone},
		{"cohere", "", "cohere", "command-z", servertools.SourceNone},
		{"bedrock", "", "bedrock", "anthropic.claude-sonnet-4-5-20250929-v1:0", servertools.SourceBedrock},
		{"anthropic", "", "anthropic", "claude-sonnet-5.5", servertools.SourceClaudeAPI},
	} {
		if got := servertools.RouteSource(tc.host, tc.base, tc.provider, tc.model); got != tc.want {
			t.Errorf("RouteSource(%q, %q, %q, %q) = %q, want %q", tc.host, tc.base, tc.provider, tc.model, got, tc.want)
		}
	}
}
