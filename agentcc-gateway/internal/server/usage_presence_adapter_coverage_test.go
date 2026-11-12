package server

// A managed (credit-funded) provider may only use an adapter
// whose usage accounting the presence matrices verify. This test reads every
// api_format createProvider switches on, builds each adapter the way the
// registry does, and fails when an adapter is accepted for managed: true
// without a presence-matrix row (upInt2Adapters), or a matrix-verified one is
// refused. A new adapter therefore fails here until it is covered or kept off
// config's managed allowlist.

import (
	"fmt"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/providers"
)

// createProviderFormats is every api_format label in createProvider's switch,
// read from its source so a new case cannot escape this test.
func createProviderFormats(t *testing.T) []string {
	t.Helper()
	formats, err := readProviderFormats(filepath.Join("..", "providers", "registry.go"), nil)
	if err != nil {
		t.Fatal(err)
	}
	if len(formats) < 8 {
		t.Fatalf("read too few api_formats from createProvider: %v", formats)
	}
	return formats
}

// presenceAdapterType builds format's adapter through the registry, with
// synthetic credentials and no discovery, and returns its Go type.
func presenceAdapterType(t *testing.T, format, model string) reflect.Type {
	t.Helper()
	off := false
	pc := config.ProviderConfig{BaseURL: "https://upstream.invalid", APIKey: "synthetic", APIFormat: format,
		Models: []string{model}, AutoDiscover: &off}
	if format == "bedrock" {
		pc.APIKey = ""
		pc.AWSAccessKeyID, pc.AWSSecretAccessKey, pc.AWSRegion = "SYNTHETIC", "synthetic", "us-east-1"
	}
	cfg := config.DefaultConfig()
	cfg.Providers = map[string]config.ProviderConfig{"coverage": pc}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("api_format %q: %v", format, err)
	}
	p, ok := registry.GetProvider("coverage")
	if !ok {
		t.Fatalf("api_format %q built no provider", format)
	}
	return reflect.TypeOf(p)
}

// managedFormatValidates is config's verdict on a managed provider in format.
func managedFormatValidates(format string) error {
	off := false
	cfg := config.DefaultConfig()
	cfg.Providers = map[string]config.ProviderConfig{"coverage": {BaseURL: "https://upstream.invalid", APIKey: "synthetic",
		APIFormat: format, Managed: true, AutoDiscover: &off}}
	return cfg.Validate()
}

func presenceMatrixCoverage(t *testing.T) map[reflect.Type][]string {
	t.Helper()
	covered := map[reflect.Type][]string{}
	for _, a := range upInt2Adapters {
		typ := presenceAdapterType(t, a.format, a.model)
		covered[typ] = append(covered[typ], a.name)
	}
	return covered
}

// Return diagnostics so the negative control exercises the very same guard
// that fails TestUsagePresenceAdapterCoverage, including config's real verdict.
func presenceCoverageProblems(t *testing.T, formats []string, covered map[reflect.Type][]string) ([]string, int) {
	t.Helper()
	var problems []string
	refused := 0
	for _, format := range formats {
		typ := presenceAdapterType(t, format, "acme-native-token")
		rows := covered[typ]
		err := managedFormatValidates(format)
		switch {
		case len(rows) > 0 && err != nil:
			problems = append(problems, fmt.Sprintf("api_format %q builds %v, verified by presence rows %v, yet managed: true is refused: %v", format, typ, rows, err))
		case len(rows) == 0 && err == nil:
			problems = append(problems, fmt.Sprintf("api_format %q builds %v, which no presence-matrix row covers, yet config accepts it as managed: true", format, typ))
		case len(rows) == 0:
			refused++
		}
	}
	return problems, refused
}

func TestUsagePresenceAdapterCoverage(t *testing.T) {
	problems, refused := presenceCoverageProblems(t, createProviderFormats(t), presenceMatrixCoverage(t))
	for _, problem := range problems {
		t.Error(problem)
	}
	if refused == 0 {
		t.Error("every adapter is matrix-covered; keep a refused case so this test proves the allowlist refuses")
	}
	for _, format := range []string{"", "acme", "OpenAI"} {
		if managedFormatValidates(format) == nil {
			t.Errorf("config accepted managed: true with api_format %q", format)
		}
	}

	// Each verified native adapter must also be one the native hold floor
	// recognises; the OpenAI wire (OpenAI, Azure) is metered by its own decoder.
	for _, a := range upInt2Adapters {
		g := upInt2Gateway(t, a, "https://upstream.invalid")
		want := a.format != "openai" && a.format != "azure"
		if got := g.srv.handlers.nativeTokenUsage(upInt2Provider); got != want {
			t.Errorf("%s: nativeTokenUsage=%t, want %t", a.name, got, want)
		}
	}
}

func TestUsagePresenceAdapterCoverageRejectsMissingAdapter(t *testing.T) {
	formats := createProviderFormats(t)
	covered := presenceMatrixCoverage(t)
	if problems, _ := presenceCoverageProblems(t, formats, covered); len(problems) != 0 {
		t.Fatalf("unmodified coverage must pass: %v", problems)
	}
	// Synthetic coverage gap, not a synthetic config verdict: the real
	// managed allowlist still admits this adapter, but its matrix row is gone.
	missing := presenceAdapterType(t, "anthropic", "acme-native-token")
	if len(covered[missing]) == 0 {
		t.Fatal("negative control needs an initially covered adapter")
	}
	delete(covered, missing)
	problems, _ := presenceCoverageProblems(t, formats, covered)
	if len(problems) != 1 || !strings.Contains(problems[0], `api_format "anthropic"`) ||
		!strings.Contains(problems[0], "no presence-matrix row covers, yet config accepts it as managed: true") {
		t.Fatalf("missing adapter must fail the managed coverage guard: %v", problems)
	}
	t.Logf("negative control detected missing adapter: %s", problems[0])
}
