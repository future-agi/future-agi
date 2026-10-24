package main

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

// Every commented provider block in config.example.yaml is meant to be
// uncommented as it stands. Each must pass the config's validation (the
// gateway refuses to start otherwise), and Perplexity's must send chat to the
// path its Sonar API serves.

// uncommentBlock returns example with the commented provider block name (a
// "  # name:" line and the "  #" lines under it) uncommented, and whether the
// block exists.
func uncommentBlock(example, name string) (string, bool) {
	lines := strings.Split(example, "\n")
	head := "  # " + name + ":"
	for i, line := range lines {
		if line != head {
			continue
		}
		for j := i; j < len(lines) && strings.HasPrefix(lines[j], "  #"); j++ {
			if j > i && strings.HasPrefix(lines[j], "  # ") && !strings.HasPrefix(lines[j], "  #   ") {
				break // the next block or a comment line between blocks
			}
			lines[j] = "  " + strings.TrimPrefix(strings.TrimPrefix(lines[j], "  #"), " ")
		}
		return strings.Join(lines, "\n"), true
	}
	return example, false
}

func loadExample(t *testing.T, text string) (*config.Config, error) {
	t.Helper()
	for _, kv := range os.Environ() { // AGENTCC_* would override the file
		if strings.HasPrefix(kv, "AGENTCC_") {
			name, _, _ := strings.Cut(kv, "=")
			t.Setenv(name, "")
			os.Unsetenv(name)
		}
	}
	path := filepath.Join(t.TempDir(), "config.yaml")
	if err := os.WriteFile(path, []byte(text), 0o600); err != nil {
		t.Fatal(err)
	}
	return config.Load(path)
}

// TestExampleConfigValidates: config.example.yaml loads as shipped, and
// with each commented provider block uncommented on its own.
func TestExampleConfigValidates(t *testing.T) {
	raw, err := os.ReadFile("../../config.example.yaml")
	if err != nil {
		t.Fatal(err)
	}
	example := string(raw)
	if _, err := loadExample(t, example); err != nil {
		t.Fatalf("config.example.yaml as shipped: %v", err)
	}

	blocks := []string{
		"anthropic", "gemini", "vertex", "bedrock", "cohere", "vllm", "ollama", "lmstudio", "tgi", "my-server",
		"azure", "azure-ad", "groq", "mistral", "together", "huggingface", "fireworks", "deepinfra",
		"perplexity", "cerebras", "xai", "openrouter",
	}
	for _, name := range blocks {
		t.Run(name, func(t *testing.T) {
			text, ok := uncommentBlock(example, name)
			if !ok {
				t.Fatalf("config.example.yaml has no commented %q provider block", name)
			}
			cfg, err := loadExample(t, text)
			if err != nil {
				t.Fatalf("config.example.yaml with the %s block uncommented does not start: %v", name, err)
			}
			if _, ok := cfg.Providers[name]; !ok {
				t.Fatalf("the uncommented %s block did not load as a provider", name)
			}
		})
	}

	// Perplexity's Sonar API serves chat at https://api.perplexity.ai/chat/completions
	// (no version segment; internal/config/endpoint_url_test.go).
	text, _ := uncommentBlock(example, "perplexity")
	cfg, err := loadExample(t, text)
	if err != nil {
		t.Fatal(err)
	}
	pcfg := cfg.Providers["perplexity"]
	if pcfg.BaseURL != "https://api.perplexity.ai" || pcfg.APIFormat != "openai" {
		t.Errorf("perplexity block: base_url %q api_format %q", pcfg.BaseURL, pcfg.APIFormat)
	}
	// A stated api_path_prefix (even "") wins over the preset's /v1: the
	// registry fills it from the preset only when unset.
	if pcfg.APIPathPrefix == nil {
		t.Errorf("perplexity block states no api_path_prefix: the preset's /v1 would send chat to /v1/chat/completions")
	}
	if got := pcfg.EndpointURL("/v1/chat/completions"); got != "https://api.perplexity.ai/chat/completions" {
		t.Errorf("perplexity chat endpoint = %q, want https://api.perplexity.ai/chat/completions", got)
	}
}
