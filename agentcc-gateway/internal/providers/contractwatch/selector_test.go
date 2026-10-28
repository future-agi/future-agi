package contractwatch

import (
	"go/ast"
	"go/parser"
	"go/token"
	"path/filepath"
	"reflect"
	"strconv"
	"strings"
	"testing"
)

func cands(ids ...string) []Candidate {
	out := make([]Candidate, len(ids))
	for i, id := range ids {
		out[i] = Candidate{ID: id}
	}
	return out
}

func boolp(b bool) *bool { return &b }

// The repo's covered models per provider, as of this package's first
// version: the realistic candidate sets the selector runs against.
var (
	geminiCovered    = cands("gemini-3.1-flash-lite", "gemini-3.8-flash", "gemini-4-argon")
	openaiCovered    = cands("gpt-4.1-2025-04-14", "gpt-5.5-2026-04-23", "gpt-5.6-sol", "gpt-6-sol")
	anthropicCovered = cands("claude-haiku-4-5-20251001", "claude-opus-5-5", "claude-sonnet-4-5-20250929")
	bedrockCovered   = cands("anthropic.claude-opus-5-5", "global.anthropic.claude-sonnet-4-6", "us.amazon.nova-2-lite-v1:0")
	cohereCovered    = cands("command-a-03-2025", "command-a-reasoning-08-2025")
)

func TestSelectTemplate(t *testing.T) {
	tests := []struct {
		section, newID string
		meta           *Model
		candidates     []Candidate
		want           string
	}{
		// The sequences from the brief: template → new model.
		{"gemini", "gemini-4.1-argon", nil, geminiCovered, "gemini-4-argon"},
		{"openai", "gpt-6.1-sol", nil, openaiCovered, "gpt-6-sol"},
		{"anthropic", "claude-opus-5-6", nil, anthropicCovered, "claude-opus-5-5"},
		// flash-lite shares "flash" with 3.8-flash, and 3.8 is far nearer
		// 3.9 than 3.1 is: generation beats the exact tier.
		{"gemini", "gemini-3.9-flash-lite", nil, geminiCovered, "gemini-3.8-flash"},
		{"bedrock", "us.amazon.nova-2-pro-v1:0", nil, bedrockCovered, "us.amazon.nova-2-lite-v1:0"},

		// More of the same shape.
		{"gemini", "gemini-4.1-flash", nil, geminiCovered, "gemini-3.8-flash"}, // a tier match beats a nearer version with no shared tier
		{"gemini", "gemini-3-flash-preview", nil, geminiCovered, "gemini-3.1-flash-lite"},
		{"gemini", "gemini-5-argon", nil, geminiCovered, "gemini-4-argon"},
		{"openai", "gpt-6.1-terra", nil, openaiCovered, "gpt-6-sol"}, // no covered terra: nearest generation
		{"openai", "gpt-5.7", nil, openaiCovered, "gpt-5.5-2026-04-23"},
		{"openai", "gpt-6-sol-2026-11-02", nil, openaiCovered, "gpt-6-sol"},
		{"openai", "o5", nil, openaiCovered, "gpt-5.5-2026-04-23"},
		{"anthropic", "claude-sonnet-5-5", nil, anthropicCovered, "claude-sonnet-4-5-20250929"},
		{"anthropic", "claude-haiku-5-5", nil, anthropicCovered, "claude-haiku-4-5-20251001"},
		{"anthropic", "claude-fable-5-1", nil, anthropicCovered, "claude-opus-5-5"},                            // no fable: nearest version
		{"bedrock", "us.anthropic.claude-opus-5-5", nil, bedrockCovered, "global.anthropic.claude-sonnet-4-6"}, // same API (Converse) beats same model on InvokeModel
		{"bedrock", "anthropic.claude-sonnet-5-5", nil, bedrockCovered, "anthropic.claude-opus-5-5"},           // InvokeModel stays on InvokeModel
		{"bedrock", "us.anthropic.claude-sonnet-4-6", nil, bedrockCovered, "global.anthropic.claude-sonnet-4-6"},
		{"bedrock", "us.meta.llama4-maverick-17b-instruct-v1:0", nil, bedrockCovered, "global.anthropic.claude-sonnet-4-6"},
		{"cohere", "command-a-reasoning-11-2025", nil, cohereCovered, "command-a-reasoning-08-2025"},
		{"cohere", "command-a-vision-07-2025", nil, cohereCovered, "command-a-03-2025"}, // the reasoning hint in the ID keeps it off the reasoning model
		{"groq", "openai/gpt-oss-20b", nil, cands("openai/gpt-oss-120b"), "openai/gpt-oss-120b"},
		{"perplexity", "sonar-pro", nil, cands("sonar"), "sonar"},
	}
	for _, tc := range tests {
		t.Run(tc.section+"/"+tc.newID, func(t *testing.T) {
			ch, ok := SelectTemplate(tc.section, tc.newID, tc.meta, tc.candidates)
			if !ok || ch.Template != tc.want {
				t.Fatalf("SelectTemplate(%s) = %q (%s), want %q", tc.newID, ch.Template, ch.Reason, tc.want)
			}
			if ch.Reason == "" {
				t.Error("empty reason")
			}
		})
	}
}

// TestSelectTemplateThinking: when the API states thinking support for both
// sides, a thinking model's cases are not cloned onto a model without it.
func TestSelectTemplateThinking(t *testing.T) {
	covered := []Candidate{
		{ID: "gemini-3.8-flash", Meta: &Model{ID: "gemini-3.8-flash", Thinking: boolp(true)}},
		{ID: "gemini-3.1-flash-lite", Meta: &Model{ID: "gemini-3.1-flash-lite", Thinking: boolp(false)}},
	}
	// By version alone 3.8-flash would win; the API says the new model does
	// not think, so the non-thinking template is chosen.
	ch, _ := SelectTemplate("gemini", "gemini-3.9-flash-lite", &Model{Thinking: boolp(false)}, covered)
	if ch.Template != "gemini-3.1-flash-lite" {
		t.Fatalf("got %q (%s), want gemini-3.1-flash-lite", ch.Template, ch.Reason)
	}
	if !strings.Contains(ch.Reason, "thinking no for both") {
		t.Errorf("reason does not mention thinking: %s", ch.Reason)
	}
	// With thinking stated true, 3.8-flash is back.
	ch, _ = SelectTemplate("gemini", "gemini-3.9-flash-lite", &Model{Thinking: boolp(true)}, covered)
	if ch.Template != "gemini-3.8-flash" {
		t.Fatalf("got %q, want gemini-3.8-flash", ch.Template)
	}
	// Unknown on the new side: no constraint.
	ch, _ = SelectTemplate("gemini", "gemini-3.9-flash-lite", nil, covered)
	if ch.Template != "gemini-3.8-flash" {
		t.Fatalf("got %q, want gemini-3.8-flash", ch.Template)
	}
	// No compatible candidate: the best one anyway, with a warning.
	ch, _ = SelectTemplate("gemini", "gemini-3.9-flash", &Model{Thinking: boolp(true)}, covered[1:])
	if ch.Template != "gemini-3.1-flash-lite" || len(ch.Warnings) == 0 {
		t.Fatalf("got %q warnings %v, want gemini-3.1-flash-lite with a thinking warning", ch.Template, ch.Warnings)
	}
}

func TestSelectTemplateAliasHint(t *testing.T) {
	ch, _ := SelectTemplate("openai", "gpt-6-sol-2026-11-02", nil, openaiCovered)
	if ch.AliasOf != "gpt-6-sol" {
		t.Errorf("AliasOf = %q, want gpt-6-sol", ch.AliasOf)
	}
	ch, _ = SelectTemplate("bedrock", "us.anthropic.claude-sonnet-4-6", nil, bedrockCovered)
	if ch.AliasOf != "global.anthropic.claude-sonnet-4-6" {
		t.Errorf("AliasOf = %q, want the global profile", ch.AliasOf)
	}
	ch, _ = SelectTemplate("gemini", "gemini-4.1-argon", nil, geminiCovered)
	if ch.AliasOf != "" {
		t.Errorf("AliasOf = %q for a new version, want none", ch.AliasOf)
	}
	if _, ok := SelectTemplate("gemini", "gemini-4.1-argon", nil, nil); ok {
		t.Error("no candidates must give ok=false")
	}
}

func TestSelectTemplateBedrockAPIWarning(t *testing.T) {
	ch, _ := SelectTemplate("bedrock", "meta.llama3-1-70b-instruct-v1:0", nil, cands("global.anthropic.claude-sonnet-4-6"))
	if len(ch.Warnings) == 0 || !strings.Contains(ch.Warnings[0], "invoke") {
		t.Errorf("want a Bedrock API warning, got %v", ch.Warnings)
	}
}

func TestParseModelID(t *testing.T) {
	tests := []struct {
		section, id string
		want        ModelID
	}{
		{"gemini", "gemini-3.1-flash-lite", ModelID{Family: "gemini", Version: []int{3, 1}, Tiers: []string{"flash", "lite"}}},
		{"gemini", "gemini-4-argon", ModelID{Family: "gemini", Version: []int{4}, Tiers: []string{"argon"}}},
		{"gemini", "gemini-2.5-flash-preview-09-2025", ModelID{Family: "gemini", Version: []int{2, 5}, Date: "202509", Tiers: []string{"flash"}}},
		{"gemini", "gemma-3-27b-it", ModelID{Family: "gemma", Version: []int{3}, Sizes: []string{"27b"}}},
		{"openai", "gpt-4.1-2025-04-14", ModelID{Family: "gpt", Version: []int{4, 1}, Date: "20250414"}},
		{"openai", "gpt-6.1-sol", ModelID{Family: "gpt", Version: []int{6, 1}, Tiers: []string{"sol"}}},
		{"openai", "o4-mini-2025-04-16", ModelID{Family: "o", Version: []int{4}, Date: "20250416", Tiers: []string{"mini"}}},
		{"openai", "gpt-4o", ModelID{Family: "gpt", Version: []int{4}, Tiers: []string{"o"}}},
		{"anthropic", "claude-opus-4-5-20251101", ModelID{Family: "claude", Version: []int{4, 5}, Date: "20251101", Tiers: []string{"opus"}}},
		{"anthropic", "claude-3-5-haiku-20241022", ModelID{Family: "claude", Version: []int{3, 5}, Date: "20241022", Tiers: []string{"haiku"}}},
		{"anthropic", "claude-opus-4-20250514", ModelID{Family: "claude", Version: []int{4}, Date: "20250514", Tiers: []string{"opus"}}},
		{"cohere", "command-a-03-2025", ModelID{Family: "command-a", Date: "202503"}},
		{"cohere", "command-a-reasoning-08-2025", ModelID{Family: "command-a", Date: "202508", Thinking: "yes"}},
		{"cohere", "command-r-plus-08-2024", ModelID{Family: "command-r", Date: "202408", Tiers: []string{"plus"}}},
		{"groq", "openai/gpt-oss-120b", ModelID{Vendor: "openai", Family: "gpt-oss", Sizes: []string{"120b"}}},
		{"groq", "meta-llama/llama-4-scout-17b-16e-instruct", ModelID{Vendor: "meta-llama", Family: "llama", Version: []int{4}, Tiers: []string{"scout"}, Sizes: []string{"16e", "17b"}}},
		{"groq", "moonshotai/kimi-k2-instruct-0905", ModelID{Vendor: "moonshotai", Family: "kimi-k", Version: []int{2}, Date: "0905"}},
		{"perplexity", "sonar-reasoning-pro", ModelID{Family: "sonar", Tiers: []string{"pro"}, Thinking: "yes"}},
		{"bedrock", "us.amazon.nova-2-lite-v1:0", ModelID{API: "converse", Geo: "us", Vendor: "amazon", Family: "nova", Version: []int{2}, Tiers: []string{"lite"}}},
		{"bedrock", "anthropic.claude-opus-5-5", ModelID{API: "invoke", Vendor: "anthropic", Family: "claude", Version: []int{5, 5}, Tiers: []string{"opus"}}},
		{"bedrock", "global.anthropic.claude-sonnet-4-6", ModelID{API: "converse", Geo: "global", Vendor: "anthropic", Family: "claude", Version: []int{4, 6}, Tiers: []string{"sonnet"}}},
		{"bedrock", "anthropic.claude-opus-4-6-v1", ModelID{API: "invoke", Vendor: "anthropic", Family: "claude", Version: []int{4, 6}, Tiers: []string{"opus"}}},
		{"bedrock", "meta.llama3-1-70b-instruct-v1:0", ModelID{API: "invoke", Vendor: "meta", Family: "llama", Version: []int{3, 1}, Sizes: []string{"70b"}}},
		{"bedrock", "us.openai.gpt-5.4", ModelID{API: "converse", Geo: "us", Vendor: "openai", Family: "gpt", Version: []int{5, 4}}},
		{"bedrock", "eu.deepseek.v3.2", ModelID{API: "converse", Geo: "eu", Vendor: "deepseek", Family: "deepseek", Version: []int{3, 2}}},
		// apac. is a geography, but the adapter sends it to InvokeModel.
		{"bedrock", "apac.anthropic.claude-sonnet-4-6", ModelID{API: "invoke", Geo: "apac", Vendor: "anthropic", Family: "claude", Version: []int{4, 6}, Tiers: []string{"sonnet"}}},
		{"openai", "grok-4-1-fast-non-reasoning", ModelID{Family: "grok", Version: []int{4, 1}, Tiers: []string{"fast"}, Thinking: "no"}},
	}
	for _, tc := range tests {
		got := ParseModelID(tc.section, tc.id)
		tc.want.Raw = tc.id
		if !reflect.DeepEqual(got, tc.want) {
			t.Errorf("ParseModelID(%q, %q)\n got %+v\nwant %+v", tc.section, tc.id, got, tc.want)
		}
	}
}

func TestVersionOrder(t *testing.T) {
	order := []string{"gemini-3-x", "gemini-3.1-x", "gemini-3.9-x", "gemini-3.10-x", "gemini-4-x", "gemini-4.1-x"}
	for i := 1; i < len(order); i++ {
		if !Newer("gemini", order[i], order[i-1]) {
			t.Errorf("%s should be newer than %s", order[i], order[i-1])
		}
	}
	if !Newer("openai", "gpt-5.5-2026-04-23", "gpt-4.1-2025-04-14") || Newer("openai", "gpt-4.1", "gpt-4.1") {
		t.Error("Newer is wrong for openai IDs")
	}
	if !Newer("cohere", "command-a-reasoning-11-2025", "command-a-reasoning-08-2025") {
		t.Error("a later Cohere date should be newer")
	}
}

// TestBedrockProfilePrefixesMatchAdapter reads isInferenceProfile in the
// bedrock adapter and checks it routes exactly the prefixes this package
// treats as Converse, so the selector's "same API" rule cannot drift from
// the adapter.
func TestBedrockProfilePrefixesMatchAdapter(t *testing.T) {
	path := filepath.Join("..", "bedrock", "converse.go")
	f, err := parser.ParseFile(token.NewFileSet(), path, nil, 0)
	if err != nil {
		t.Fatal(err)
	}
	var found []string
	var sawFunc bool
	ast.Inspect(f, func(n ast.Node) bool {
		fn, ok := n.(*ast.FuncDecl)
		if !ok || fn.Name.Name != "isInferenceProfile" {
			return true
		}
		sawFunc = true
		ast.Inspect(fn.Body, func(n ast.Node) bool {
			call, ok := n.(*ast.CallExpr)
			if !ok || len(call.Args) != 2 {
				return true
			}
			sel, ok := call.Fun.(*ast.SelectorExpr)
			if !ok || sel.Sel.Name != "HasPrefix" {
				return true
			}
			if lit, ok := call.Args[1].(*ast.BasicLit); ok && lit.Kind == token.STRING {
				s, _ := strconv.Unquote(lit.Value)
				found = append(found, s)
			}
			return true
		})
		return false
	})
	if !sawFunc {
		t.Fatalf("%s has no isInferenceProfile; update bedrockProfilePrefixes and this test to the adapter's routing", path)
	}
	if !reflect.DeepEqual(found, bedrockProfilePrefixes) {
		t.Fatalf("the adapter's isInferenceProfile prefixes are %q, bedrockProfilePrefixes is %q: keep them identical", found, bedrockProfilePrefixes)
	}
}
