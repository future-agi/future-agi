package sources

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/catalog"
	"github.com/futureagi/agentcc-gateway/internal/config"
)

// uncomment returns example with the commented-out provider block name
// ("  # name:" up to the next blank line) turned on.
func uncomment(t *testing.T, example, name string) string {
	t.Helper()
	lines := strings.Split(example, "\n")
	start := -1
	for i, l := range lines {
		if l == "  # "+name+":" {
			start = i
			break
		}
	}
	if start < 0 {
		t.Fatalf("config.example.yaml has no commented-out %s block", name)
	}
	for i := start; i < len(lines) && strings.TrimSpace(lines[i]) != ""; i++ {
		lines[i] = "  " + strings.TrimPrefix(lines[i], "  # ")
	}
	return strings.Join(lines, "\n")
}

// TestExampleConfigManagedSources: the example's managed OpenRouter provider
// and its direct Anthropic price-card provider, once uncommented, load,
// validate and resolve with the bundled card and consistent fees.
func TestExampleConfigManagedSources(t *testing.T) {
	raw, err := os.ReadFile(filepath.Join("..", "..", "..", "config.example.yaml"))
	if err != nil {
		t.Fatal(err)
	}
	text := uncomment(t, uncomment(t, string(raw), "openrouter-managed"), "anthropic-direct")
	path := filepath.Join(t.TempDir(), "config.yaml")
	if err := os.WriteFile(path, []byte(text), 0o600); err != nil {
		t.Fatal(err)
	}
	cfg, err := config.Load(path)
	if err != nil {
		t.Fatalf("the example with both managed blocks on: %v", err)
	}
	or, direct := cfg.Providers["openrouter-managed"], cfg.Providers["anthropic-direct"]
	if !or.Managed || or.CatalogSource != catalog.SourceOpenRouter || or.EffectiveUpstreamFeeBps() != 550 || or.ResaleTermsRef != "" {
		t.Errorf("openrouter-managed = %+v", or)
	}
	if !direct.Managed || direct.CatalogSource != catalog.SourcePriceCard || direct.EffectiveUpstreamFeeBps() != 0 || direct.ResaleTermsRef != "" {
		t.Errorf("anthropic-direct = %+v", direct)
	}
	got, err := Resolve(cfg, []catalog.Source{{ProviderID: "anthropic-direct"}, {ProviderID: "openrouter-managed", Type: "openrouter"}})
	if err != nil {
		t.Fatalf("resolving the example's sources: %v", err)
	}
	if got[0].Load == nil || got[0].UpstreamSlug != "anthropic" || got[1].Kind() != catalog.SourceOpenRouter {
		t.Errorf("resolved = %+v", got)
	}
}
