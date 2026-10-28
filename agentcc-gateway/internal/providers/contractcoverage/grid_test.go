package contractcoverage

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"go/ast"
	"go/parser"
	"go/token"
	"io/fs"
	"net/url"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/providers/contracttest"
	"github.com/futureagi/agentcc-gateway/internal/providers/contractwatch"
)

// providersDir is internal/providers, from this package's directory.
const providersDir = ".."

const (
	coverageDoc    = "../CONTRACT_COVERAGE.md"
	exemptionsFile = "exemptions.json"
)

// Endpoints of capabilities no contract case can drive: nothing parses
// their payloads, or nothing implements them. A row that implements one
// needs an exemption. speechRelay is covered by the speech sessions.
const (
	endpointSearch      = "search"
	endpointVideo       = "video"
	endpointRealtime    = "realtime"
	endpointSpeechRelay = "speech_relay"
)

// updating reports whether -update is set. contracttest registers the flag
// (this package imports it), as every contract suite's goldens use it.
func updating() bool {
	f := flag.Lookup("update")
	if f == nil {
		return false
	}
	g, ok := f.Value.(flag.Getter)
	if !ok {
		return false
	}
	v, _ := g.Get().(bool)
	return v
}

// textCompletionProvider is the server's nativeTextCompletionProvider
// (internal/server/handlers_completion.go), the one capability /v1/completions
// type-asserts that provider.go does not declare. TestCapabilityTable checks
// the server still declares it with this method.
type textCompletionProvider interface {
	TextCompletion(ctx context.Context, req *models.CompletionRequest) (*models.CompletionResponse, error)
}

// capability maps one capability interface to the contract endpoints that
// drive it.
type capability struct {
	// Iface is the interface's name in provider.go ("" for completions).
	Iface     string
	Endpoints []string
	Has       func(p any) bool
}

func has[T any](p any) bool { _, ok := p.(T); return ok }

// capabilities is every capability interface a handler type-asserts, in the
// grid's column order. TestCapabilityTable checks it against provider.go and
// contracttest.Endpoints, so a new interface or endpoint cannot be left out.
var capabilities = []capability{
	{"Provider", []string{contracttest.EndpointChat}, has[providers.Provider]},
	{"ChatViaResponsesProvider", []string{contracttest.EndpointChatViaResponses}, has[providers.ChatViaResponsesProvider]},
	{"", []string{contracttest.EndpointCompletions}, has[textCompletionProvider]},
	{"EmbeddingProvider", []string{contracttest.EndpointEmbeddings}, has[providers.EmbeddingProvider]},
	{"ImageProvider", []string{contracttest.EndpointImages}, has[providers.ImageProvider]},
	{"SpeechProvider", []string{contracttest.EndpointSpeech}, has[providers.SpeechProvider]},
	{"TranscriptionProvider", []string{contracttest.EndpointTranscription}, has[providers.TranscriptionProvider]},
	{"TranslationProvider", []string{contracttest.EndpointTranslation}, has[providers.TranslationProvider]},
	{"RerankProvider", []string{contracttest.EndpointRerank}, has[providers.RerankProvider]},
	{"OCRProvider", []string{contracttest.EndpointOCR}, has[providers.OCRProvider]},
	{"ResponsesProvider", []string{contracttest.EndpointResponses}, has[providers.ResponsesProvider]},
	{"AssistantsProvider", []string{contracttest.EndpointAssistants}, has[providers.AssistantsProvider]},
	{"VectorStoresProvider", []string{contracttest.EndpointVectorStores}, has[providers.VectorStoresProvider]},
	{"AnthropicNativeProvider", []string{contracttest.EndpointAnthropicMessages, contracttest.EndpointAnthropicCountTokens}, has[providers.AnthropicNativeProvider]},
	{"GenAINativeProvider", []string{contracttest.EndpointGenAIGenerate, contracttest.EndpointGenAICountTokens, contracttest.EndpointGenAIEmbed}, has[providers.GenAINativeProvider]},
	// ListModels is part of Provider; it is its own column.
	{"Provider", []string{contracttest.EndpointListModels}, has[providers.Provider]},
	{"SearchProvider", []string{endpointSearch}, has[providers.SearchProvider]},
	{"VideoProvider", []string{endpointVideo}, has[providers.VideoProvider]},
	{"RealtimeProvider", []string{endpointRealtime}, has[providers.RealtimeProvider]},
	{"SpeechRelayProvider", []string{endpointSpeechRelay}, has[providers.SpeechRelayProvider]},
}

// notCapabilities are provider.go interfaces that add no endpoint.
var notCapabilities = map[string]string{
	"ClientTimeoutReporter": "a provider's own client timeout, read by the handlers' deadline logic",
	"PriceTableNamer":       "the name the price table keys a provider's models under",
}

// columns is every endpoint, in grid order.
func columns() []string {
	var out []string
	for _, c := range capabilities {
		out = append(out, c.Endpoints...)
	}
	return out
}

// shortNames label the grid's columns.
var shortNames = map[string]string{
	contracttest.EndpointChat:                 "chat",
	contracttest.EndpointChatViaResponses:     "chat→resp",
	contracttest.EndpointCompletions:          "compl",
	contracttest.EndpointEmbeddings:           "embed",
	contracttest.EndpointImages:               "images",
	contracttest.EndpointSpeech:               "speech",
	contracttest.EndpointTranscription:        "transcr",
	contracttest.EndpointTranslation:          "transl",
	contracttest.EndpointRerank:               "rerank",
	contracttest.EndpointOCR:                  "ocr",
	contracttest.EndpointResponses:            "resp",
	contracttest.EndpointAssistants:           "asst",
	contracttest.EndpointVectorStores:         "vstores",
	contracttest.EndpointAnthropicMessages:    "a.msgs",
	contracttest.EndpointAnthropicCountTokens: "a.count",
	contracttest.EndpointGenAIGenerate:        "g.gen",
	contracttest.EndpointGenAICountTokens:     "g.count",
	contracttest.EndpointGenAIEmbed:           "g.embed",
	contracttest.EndpointListModels:           "models",
	endpointSearch:                            "search",
	endpointVideo:                             "video",
	endpointRealtime:                          "rt",
	endpointSpeechRelay:                       "relay",
}

// row is one provider key: a native adapter, an OpenAI-compatible preset
// ("groq@", the openai suite's groq@<model> fixtures), Vertex AI ("vertex@",
// the gemini suite's vertex@<model> fixtures) or a speech-relay vendor.
type row struct {
	Key     string
	Adapter string // the adapter package whose testdata holds its fixtures
	Format  string // the api_format it is configured with
	Label   string
	Impl    map[string]bool
}

// presetFormat is the api_format of the OpenAI-compatible presets.
const presetFormat = "openai"

// formatAliases are createProvider api_formats served by another format's
// adapter, so they need no row of their own.
var formatAliases = map[string]string{"google": "gemini"}

// speechVendors are the speech relay's vendors (createProvider's speech
// formats; TestCapabilityTable checks the list against registry.go).
var speechVendors = []string{"cartesia", "deepgram"}

// openAIPresets are providers.KnownProviders' OpenAI-compatible presets,
// sorted.
func openAIPresets() []string {
	var out []string
	for name, p := range providers.KnownProviders {
		if p.APIFormat == presetFormat {
			out = append(out, name)
		}
	}
	sort.Strings(out)
	return out
}

// buildRows constructs every row's adapter through providers.NewRegistry, as
// the gateway does from config.yaml, and type-asserts it against each
// capability. No adapter calls upstream while it is built (each row names a
// model, so the registry does not auto-discover).
func buildRows(t *testing.T) []*row {
	t.Helper()
	rows := []*row{
		{Key: "anthropic", Adapter: "anthropic", Format: "anthropic", Label: "Anthropic"},
		{Key: "openai", Adapter: "openai", Format: "openai", Label: "OpenAI"},
		{Key: "azure", Adapter: "azure", Format: "azure", Label: "Azure OpenAI"},
		{Key: "gemini", Adapter: "gemini", Format: "gemini", Label: "Gemini API"},
		{Key: "vertex@", Adapter: "gemini", Format: "gemini", Label: "Vertex AI"},
		{Key: "bedrock", Adapter: "bedrock", Format: "bedrock", Label: "Amazon Bedrock"},
		{Key: "cohere", Adapter: "cohere", Format: "cohere", Label: "Cohere"},
	}
	for _, p := range openAIPresets() {
		rows = append(rows, &row{Key: p + "@", Adapter: "openai", Format: presetFormat, Label: p + " (preset)"})
	}
	for _, v := range speechVendors {
		rows = append(rows, &row{Key: v, Adapter: "speech", Format: v, Label: v + " (speech relay)"})
	}

	cfg := &config.Config{Providers: map[string]config.ProviderConfig{}}
	for i, r := range rows {
		pc := config.ProviderConfig{APIFormat: r.Format, APIKey: "contract-coverage-key", Models: []string{fmt.Sprintf("contract-coverage-%d", i)}}
		switch {
		case r.Key == "azure":
			pc.BaseURL = "https://contract-coverage.openai.azure.com"
		case r.Key == "vertex@":
			pc.BaseURL = "https://aiplatform.googleapis.com"
			pc.Headers = map[string]string{"x-gcp-project": "contract-coverage", "x-gcp-location": "global"}
		case r.Key == "bedrock":
			pc.AWSAccessKeyID, pc.AWSSecretAccessKey, pc.AWSRegion = "AKIDCONTRACTCOVERAGE", "contract-coverage-secret", "us-east-1"
		case strings.HasSuffix(r.Key, "@"):
			pc.APIFormat, pc.Type = "", strings.TrimSuffix(r.Key, "@")
		}
		cfg.Providers[r.Key] = pc
	}
	reg, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("building the adapters: %v", err)
	}
	for _, r := range rows {
		p, ok := reg.GetProvider(r.Key)
		if !ok {
			t.Fatalf("the registry did not create %s", r.Key)
		}
		r.Impl = map[string]bool{}
		for _, c := range capabilities {
			if c.Has(p) {
				for _, ep := range c.Endpoints {
					r.Impl[ep] = true
				}
			}
		}
		if sr, ok := p.(providers.SpeechRelayProvider); ok && sr.Vendor() != r.Key {
			t.Errorf("speech row %s: the adapter serves vendor %q", r.Key, sr.Vendor())
		}
	}
	return rows
}

// rowKey is the row a fixture model belongs to: "<preset>@" for the openai
// suite's preset models, "vertex@" for the gemini suite's Vertex AI models,
// else the adapter.
func rowKey(adapter, model string, presets map[string]bool) string {
	switch adapter {
	case "openai":
		if p, rest, ok := strings.Cut(model, "@"); ok && rest != "" && presets[p] {
			return p + "@"
		}
	case "gemini":
		if rest, ok := strings.CutPrefix(model, "vertex@"); ok && rest != "" {
			return "vertex@"
		}
	}
	return adapter
}

// fixtureCase is one contract case (or speech session).
type fixtureCase struct {
	Row, Adapter, Model, Name, Endpoint string
	Error, Recordable                   bool
}

// fixtureModel is one model directory (or speech vendor).
type fixtureModel struct {
	Row, Adapter, Model string
	Recorded            bool
}

// collectFixtures reads every case under internal/providers/*/testdata/
// contracts and every speech session, with its endpoint (case.json, default
// chat), status and recordability.
func collectFixtures(t *testing.T) ([]fixtureCase, []fixtureModel) {
	t.Helper()
	presets := map[string]bool{}
	for _, p := range openAIPresets() {
		presets[p] = true
	}
	var cases []fixtureCase
	var modelsOut []fixtureModel
	adapters, err := os.ReadDir(providersDir)
	if err != nil {
		t.Fatal(err)
	}
	for _, a := range adapters {
		root := filepath.Join(providersDir, a.Name(), filepath.FromSlash(contracttest.DefaultRoot))
		modelDirs, err := os.ReadDir(root)
		if errors.Is(err, fs.ErrNotExist) || !a.IsDir() {
			continue
		}
		if err != nil {
			t.Fatal(err)
		}
		for _, md := range modelDirs {
			if !md.IsDir() {
				continue
			}
			model, err := url.PathUnescape(md.Name())
			if err != nil {
				t.Fatalf("%s: %v", filepath.Join(root, md.Name()), err)
			}
			rk := rowKey(a.Name(), model, presets)
			var meta struct {
				Source string `json:"source"`
			}
			raw, err := os.ReadFile(filepath.Join(root, md.Name(), contracttest.MetaFile))
			if err != nil {
				t.Fatal(err)
			}
			if err := json.Unmarshal(raw, &meta); err != nil {
				t.Fatalf("%s: %v", filepath.Join(root, md.Name(), contracttest.MetaFile), err)
			}
			modelsOut = append(modelsOut, fixtureModel{Row: rk, Adapter: a.Name(), Model: model, Recorded: meta.Source == contracttest.SourceRecorded})
			caseDirs, err := os.ReadDir(filepath.Join(root, md.Name()))
			if err != nil {
				t.Fatal(err)
			}
			for _, cd := range caseDirs {
				dir := filepath.Join(root, md.Name(), cd.Name())
				if !cd.IsDir() {
					continue
				}
				if _, err := os.Stat(filepath.Join(dir, contracttest.RequestFile)); err != nil {
					continue
				}
				spec, err := contracttest.ReadCaseSpec(dir)
				if err != nil {
					t.Fatal(err)
				}
				ep := spec.Endpoint
				if ep == "" {
					ep = contracttest.EndpointChat
				}
				cases = append(cases, fixtureCase{Row: rk, Adapter: a.Name(), Model: model, Name: cd.Name(), Endpoint: ep,
					Error: spec.Status >= 400, Recordable: spec.CanRecord()})
			}
		}
	}
	// The speech relay's sessions: speech/testdata/sessions/<vendor>/<case>.
	sessions := filepath.Join(providersDir, "speech", "testdata", "sessions")
	vendors, err := os.ReadDir(sessions)
	if err != nil {
		t.Fatal(err)
	}
	for _, v := range vendors {
		if !v.IsDir() {
			continue
		}
		modelsOut = append(modelsOut, fixtureModel{Row: v.Name(), Adapter: "speech", Model: v.Name()})
		dirs, err := os.ReadDir(filepath.Join(sessions, v.Name()))
		if err != nil {
			t.Fatal(err)
		}
		for _, d := range dirs {
			dir := filepath.Join(sessions, v.Name(), d.Name())
			if _, err := os.Stat(filepath.Join(dir, "session.json")); err != nil {
				continue
			}
			var golden struct {
				Error json.RawMessage `json:"error"`
			}
			if raw, err := os.ReadFile(filepath.Join(dir, "golden.json")); err == nil {
				_ = json.Unmarshal(raw, &golden)
			}
			cases = append(cases, fixtureCase{Row: v.Name(), Adapter: "speech", Model: v.Name(), Name: d.Name(),
				Endpoint: endpointSpeechRelay, Error: len(golden.Error) > 0})
		}
	}
	return cases, modelsOut
}

// Exemption kinds: why an implemented cell has no contract case.
const (
	kindNotServed  = "not_served"  // the upstream has no such endpoint
	kindNotParsed  = "not_parsed"  // the gateway relays the bytes and parses nothing
	kindNoUpstream = "no_upstream" // the adapter answers without calling upstream
	kindUncovered  = "uncovered"   // served and parsed, no fixture yet: an accepted coverage gap
)

var kindMarks = map[string]string{kindNotServed: "n/s", kindNotParsed: "n/p", kindNoUpstream: "n/u", kindUncovered: "GAP"}

var kindTitles = map[string]string{
	kindNotServed:  "not served (the upstream has no such endpoint, or the gateway never routes there)",
	kindNotParsed:  "relayed, not parsed",
	kindNoUpstream: "answered without an upstream call",
	kindUncovered:  "accepted coverage gaps (served and parsed, no fixture yet)",
}

var kindOrder = []string{kindUncovered, kindNoUpstream, kindNotParsed, kindNotServed}

// Exemptions is exemptions.json.
type Exemptions struct {
	About string      `json:"about"`
	Cells []Exemption `json:"cells"`
}

// Exemption exempts some implemented cells of one row.
type Exemption struct {
	Provider  string   `json:"provider"`
	Endpoints []string `json:"endpoints"`
	Kind      string   `json:"kind"`
	Reason    string   `json:"reason"`
}

func loadExemptions(t *testing.T) Exemptions {
	t.Helper()
	raw, err := os.ReadFile(exemptionsFile)
	if err != nil {
		t.Fatal(err)
	}
	var ex Exemptions
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.DisallowUnknownFields()
	if err := dec.Decode(&ex); err != nil {
		t.Fatalf("%s: %v", exemptionsFile, err)
	}
	return ex
}

// cell is one (row, endpoint) of the grid.
type cell struct {
	Cases, Errors int
	Exempt        *Exemption
}

// grid is the computed matrix.
type grid struct {
	Rows   []*row
	Cols   []string
	Cells  map[string]map[string]*cell // row -> endpoint -> cell, implemented endpoints only
	Cases  []fixtureCase
	Models []fixtureModel
}

// buildGrid fills the matrix and applies the exemptions, returning every
// problem: a case for a row or endpoint that does not exist, an implemented
// cell with neither a case nor an exemption, and a stale or malformed
// exemption.
func buildGrid(rows []*row, cases []fixtureCase, mods []fixtureModel, ex Exemptions) (*grid, []string) {
	g := &grid{Rows: rows, Cols: columns(), Cells: map[string]map[string]*cell{}, Cases: cases, Models: mods}
	var problems []string
	byKey := map[string]*row{}
	for _, r := range rows {
		byKey[r.Key] = r
		g.Cells[r.Key] = map[string]*cell{}
		for ep := range r.Impl {
			g.Cells[r.Key][ep] = &cell{}
		}
	}
	for _, c := range cases {
		r := byKey[c.Row]
		if r == nil {
			problems = append(problems, fmt.Sprintf("%s/%s/%s: no provider key %q; add the adapter or preset to buildRows", c.Adapter, c.Model, c.Name, c.Row))
			continue
		}
		ce := g.Cells[c.Row][c.Endpoint]
		if ce == nil {
			problems = append(problems, fmt.Sprintf("%s/%s/%s: endpoint %s, which %s does not implement", c.Adapter, c.Model, c.Name, c.Endpoint, c.Row))
			continue
		}
		ce.Cases++
		if c.Error {
			ce.Errors++
		}
	}
	seen := map[[2]string]bool{}
	for i := range ex.Cells {
		e := &ex.Cells[i]
		at := fmt.Sprintf("%s: cells[%d] (%s %v)", exemptionsFile, i, e.Provider, e.Endpoints)
		r := byKey[e.Provider]
		switch {
		case r == nil:
			problems = append(problems, at+": no such provider key")
			continue
		case kindMarks[e.Kind] == "":
			problems = append(problems, fmt.Sprintf("%s: kind %q, want one of %s, %s, %s, %s", at, e.Kind, kindNotServed, kindNotParsed, kindNoUpstream, kindUncovered))
		case strings.TrimSpace(e.Reason) == "":
			problems = append(problems, at+": no reason")
		case len(e.Endpoints) == 0:
			problems = append(problems, at+": no endpoints")
		}
		for _, ep := range e.Endpoints {
			ce := g.Cells[r.Key][ep]
			switch {
			case seen[[2]string{r.Key, ep}]:
				problems = append(problems, fmt.Sprintf("%s: %s %s is exempted twice", at, r.Key, ep))
			case ce == nil:
				problems = append(problems, fmt.Sprintf("%s: stale: %s does not implement %s", at, r.Key, ep))
			case ce.Cases > 0:
				problems = append(problems, fmt.Sprintf("%s: stale: %s %s has %d cases; drop the exemption", at, r.Key, ep, ce.Cases))
			default:
				ce.Exempt = e
			}
			seen[[2]string{r.Key, ep}] = true
		}
	}
	for _, r := range rows {
		for _, ep := range g.Cols {
			if ce := g.Cells[r.Key][ep]; ce != nil && ce.Cases == 0 && ce.Exempt == nil {
				problems = append(problems, fmt.Sprintf("%s implements %s, which has no contract case: add one (internal/providers/contracttest/README.md) or exempt it in %s with a reason", r.Key, ep, exemptionsFile))
			}
		}
	}
	return g, problems
}

// TestCapabilityTable keeps the grid's columns complete: every interface in
// provider.go is a capability (with its endpoints) or explicitly not one,
// every contracttest endpoint is some capability's, the server still
// declares the completions capability, and every api_format the registry
// can create has a row.
func TestCapabilityTable(t *testing.T) {
	fset := token.NewFileSet()
	f, err := parser.ParseFile(fset, filepath.Join(providersDir, "provider.go"), nil, 0)
	if err != nil {
		t.Fatal(err)
	}
	inTable := map[string]bool{}
	for _, c := range capabilities {
		inTable[c.Iface] = true
	}
	declared := map[string]bool{}
	ast.Inspect(f, func(n ast.Node) bool {
		ts, ok := n.(*ast.TypeSpec)
		if !ok {
			return true
		}
		if _, ok := ts.Type.(*ast.InterfaceType); !ok {
			return true
		}
		declared[ts.Name.Name] = true
		if !inTable[ts.Name.Name] && notCapabilities[ts.Name.Name] == "" {
			t.Errorf("provider.go declares interface %s, which the coverage grid does not know: add it to capabilities (with the endpoints that drive it) or to notCapabilities", ts.Name.Name)
		}
		return true
	})
	for name := range inTable {
		if name != "" && !declared[name] {
			t.Errorf("capabilities names %s, which provider.go no longer declares", name)
		}
	}
	cols := map[string]bool{}
	for _, ep := range columns() {
		if cols[ep] {
			t.Errorf("endpoint %s is in two columns", ep)
		}
		cols[ep] = true
		if shortNames[ep] == "" {
			t.Errorf("endpoint %s has no column label", ep)
		}
	}
	for _, ep := range contracttest.Endpoints {
		if !cols[ep] {
			t.Errorf("contracttest endpoint %s is no capability's: add it to capabilities", ep)
		}
	}

	// The server's completions capability.
	sf, err := parser.ParseFile(fset, filepath.Join(providersDir, "..", "server", "handlers_completion.go"), nil, 0)
	if err != nil {
		t.Fatal(err)
	}
	found := false
	ast.Inspect(sf, func(n ast.Node) bool {
		ts, ok := n.(*ast.TypeSpec)
		if !ok || ts.Name.Name != "nativeTextCompletionProvider" {
			return true
		}
		if it, ok := ts.Type.(*ast.InterfaceType); ok {
			for _, m := range it.Methods.List {
				for _, name := range m.Names {
					found = found || name.Name == "TextCompletion"
				}
			}
		}
		return false
	})
	if !found {
		t.Error("internal/server no longer declares nativeTextCompletionProvider.TextCompletion; update textCompletionProvider")
	}

	// Every api_format createProvider handles has a row.
	rf, err := parser.ParseFile(fset, filepath.Join(providersDir, "registry.go"), nil, 0)
	if err != nil {
		t.Fatal(err)
	}
	var formats []string
	ast.Inspect(rf, func(n ast.Node) bool {
		fd, ok := n.(*ast.FuncDecl)
		if !ok || fd.Name.Name != "createProvider" {
			return true
		}
		ast.Inspect(fd, func(n ast.Node) bool {
			if cc, ok := n.(*ast.CaseClause); ok {
				for _, e := range cc.List {
					if bl, ok := e.(*ast.BasicLit); ok && bl.Kind == token.STRING {
						s, _ := strconv.Unquote(bl.Value)
						formats = append(formats, s)
					}
				}
			}
			return true
		})
		return false
	})
	if len(formats) == 0 {
		t.Fatal("found no api_format cases in registry.go createProvider")
	}
	rowFormats := map[string]bool{"anthropic": true, "openai": true, "azure": true, "gemini": true, "bedrock": true, "cohere": true}
	for _, v := range speechVendors {
		rowFormats[v] = true
	}
	for _, f := range formats {
		if !rowFormats[f] && !rowFormats[formatAliases[f]] {
			t.Errorf("createProvider handles api_format %q, which has no row in the coverage grid: add one to buildRows", f)
		}
	}
}

// TestCoverageGrid builds the provider × endpoint matrix and requires every
// implemented cell to have a contract case or a reasoned exemption; it then
// compares ../CONTRACT_COVERAGE.md with what it computed (-update rewrites
// it).
func TestCoverageGrid(t *testing.T) {
	rows := buildRows(t)
	cases, mods := collectFixtures(t)
	g, problems := buildGrid(rows, cases, mods, loadExemptions(t))
	for _, p := range problems {
		t.Error(p)
	}
	reg, err := contractwatch.LoadRegistry(filepath.Join(providersDir, "contractwatch", contractwatch.RegistryFile))
	if err != nil {
		t.Fatal(err)
	}
	doc := renderCoverage(g, reg)
	compareDoc(t, coverageDoc, doc)
}

// compareDoc compares a generated document with the committed one, or
// writes it under -update.
func compareDoc(t *testing.T, path string, got []byte) {
	t.Helper()
	if updating() {
		if err := os.WriteFile(path, got, 0o644); err != nil {
			t.Fatal(err)
		}
		return
	}
	want, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("%v; create it with: go test ./internal/providers/contractcoverage/ -update", err)
	}
	if !bytes.Equal(want, got) {
		t.Errorf("%s is stale; regenerate it with: go test ./internal/providers/contractcoverage/ -update\n%s", path, firstDiff(string(want), string(got)))
	}
}

func firstDiff(a, b string) string {
	al, bl := strings.Split(a, "\n"), strings.Split(b, "\n")
	for i := 0; i < len(al) || i < len(bl); i++ {
		var x, y string
		if i < len(al) {
			x = al[i]
		}
		if i < len(bl) {
			y = bl[i]
		}
		if x != y {
			return fmt.Sprintf("line %d:\n  committed: %s\n  computed:  %s", i+1, x, y)
		}
	}
	return ""
}
