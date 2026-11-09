package server

// Main addendum 13: a managed (credit-funded) provider may only use an adapter
// whose usage accounting the presence matrices verify. This test reads every
// api_format createProvider switches on, builds each adapter the way the
// registry does, and fails when an adapter is accepted for managed: true
// without a presence-matrix row (upInt2Adapters), or a matrix-verified one is
// refused. A new adapter therefore fails here until it is covered or kept off
// config's managed allowlist.

import (
	"go/ast"
	"go/parser"
	"go/token"
	"path/filepath"
	"reflect"
	"strconv"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/providers"
)

// createProviderFormats is every api_format label in createProvider's switch,
// read from its source so a new case cannot escape this test.
func createProviderFormats(t *testing.T) []string {
	t.Helper()
	file, err := parser.ParseFile(token.NewFileSet(), filepath.Join("..", "providers", "registry.go"), nil, 0)
	if err != nil {
		t.Fatal(err)
	}
	var formats []string
	switches := 0
	for _, decl := range file.Decls {
		fn, ok := decl.(*ast.FuncDecl)
		if !ok || fn.Name.Name != "createProvider" || fn.Body == nil {
			continue
		}
		ast.Inspect(fn.Body, func(n ast.Node) bool {
			sw, ok := n.(*ast.SwitchStmt)
			if !ok {
				return true
			}
			if sel, ok := sw.Tag.(*ast.SelectorExpr); !ok || sel.Sel.Name != "APIFormat" {
				return true
			}
			switches++
			for _, stmt := range sw.Body.List {
				for _, expr := range stmt.(*ast.CaseClause).List {
					lit, ok := expr.(*ast.BasicLit)
					if !ok || lit.Kind != token.STRING {
						t.Fatalf("createProvider case %T is not a string literal", expr)
					}
					format, err := strconv.Unquote(lit.Value)
					if err != nil {
						t.Fatal(err)
					}
					formats = append(formats, format)
				}
			}
			return false
		})
	}
	if switches != 1 || len(formats) < 8 {
		t.Fatalf("read %d api_format switches and %v from createProvider", switches, formats)
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

func TestUsagePresenceAdapterCoverage(t *testing.T) {
	covered := map[reflect.Type][]string{}
	for _, a := range upInt2Adapters {
		typ := presenceAdapterType(t, a.format, a.model)
		covered[typ] = append(covered[typ], a.name)
	}
	refused := 0
	for _, format := range createProviderFormats(t) {
		typ := presenceAdapterType(t, format, "acme-native-token")
		rows, ok := covered[typ]
		err := managedFormatValidates(format)
		switch {
		case ok && err != nil:
			t.Errorf("api_format %q builds %v, verified by presence rows %v, yet managed: true is refused: %v", format, typ, rows, err)
		case !ok && err == nil:
			t.Errorf("api_format %q builds %v, which no presence-matrix row covers, yet config accepts it as managed: true", format, typ)
		case !ok:
			refused++
		}
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
