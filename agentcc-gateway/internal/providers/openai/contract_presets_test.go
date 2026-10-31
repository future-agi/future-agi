package openai

import (
	"context"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"regexp"
	"sort"
	"sync"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers/contracttest"
)

// The OpenAI-compatible presets this suite has fixtures for. Each preset
// declares itself in its own file, contract_preset_<name>_test.go, from an
// init function:
//
//	func init() {
//		registerContractPreset(contractPreset{
//			Name: "groq",
//			Enums: map[string][]contracttest.EnumSpec{
//				contracttest.EndpointChat: {...},
//			},
//		})
//	}
//
// so adding a preset, or an endpoint to one, touches that file alone. The
// suite reads everything preset-specific from the registration: which
// model-key prefixes are presets (fixtureUpstream), their value sets
// (ContractEnums, hence the strict declarations), which have a spec subset
// (TestSpecCoveragePresets), how they frame a Responses stream
// (checkStreamFraming), and the capabilities they serve at another path than
// the gateway calls (TestContractPresetPathGaps).

// contractPreset is one OpenAI-compatible upstream's declarations.
type contractPreset struct {
	// Name is the preset (providers/presets.go KnownProviders): the part of
	// a fixture's model key before the "@", and the dialect its value sets
	// are filed under. The providers package imports this one, so the test
	// cannot read the table itself; a typo in a model key fails instead.
	Name string
	// Enums are the value sets the preset documents, by endpoint
	// (ContractEnums[Name]): one entry for every endpoint its fixtures
	// drive, nil when the endpoint's bodies have no enum-like field.
	Enums map[string][]contracttest.EnumSpec
	// Spec is set when the preset's official spec is fetchable: pinned as
	// contractspec/subsets/<Name> and checked by TestSpecCoveragePresets
	// against testdata/spec_allowlist.<Name>.json.
	Spec bool
	// ResponsesStream is how the preset frames a /v1/responses stream; nil
	// is the official API's framing (officialResponsesStream).
	ResponsesStream *responsesFraming
	// SpecFreeStrings are paths the adapter checks an enum at (openAIRoots'
	// EnumChecks) where the preset's spec has a free string instead, so
	// TestSpecCoveragePresets leaves those checks out; the fixtures' value
	// sets (Enums) pin the values there.
	SpecFreeStrings []string
	// PathGaps are capabilities the preset serves at another path than the
	// one the gateway calls.
	PathGaps []presetPathGap
}

// responsesFraming is how an upstream frames a Responses API stream, which
// the /v1/responses passthrough relays as it is.
type responsesFraming struct {
	// Events are the documented event types.
	Events []string
	// DataOnly is set when every event is a data line alone, its type in
	// the data. Otherwise every event is named, and its data carries the
	// same type.
	DataOnly bool
	// Sentinel is a data line that is not JSON and ends the stream
	// ("[DONE]"), when the upstream sends one.
	Sentinel string
}

// officialResponsesStream is the official API's framing: every event named
// with one of ResponseStreamEvent's types, no sentinel.
var officialResponsesStream = &responsesFraming{Events: responsesStreamEvents}

// presetPathGap is a capability a preset serves at another path than the one
// the gateway calls, so the gateway's request goes to a path the provider
// does not serve: the (preset, endpoint) cell is not served at the gateway's
// path. TestContractPresetPathGaps pins the path the adapter calls, so fixing
// the route fails it until the entry, and its gap, are removed.
type presetPathGap struct {
	// Endpoint is the contracttest endpoint; the gateway calls
	// contractPaths[Endpoint] below the preset's base URL.
	Endpoint string
	// Documented are the paths, below the same base URL, where the
	// provider's spec serves the capability, or its nearest replacements.
	Documented []string
	// Source is where the provider documents them: repo@commit:path and
	// the operations.
	Source string
	// Gap is the gap's definition, "known gap <ID>: <reason>".
	Gap string
}

// contractPresets are the registered presets by name.
var contractPresets = map[string]contractPreset{}

// registerContractPreset adds p; each contract_preset_<name>_test.go calls it
// from its init function.
func registerContractPreset(p contractPreset) {
	switch {
	case p.Name == "" || p.Name == officialDialect:
		panic(fmt.Sprintf("registerContractPreset: %q is not a preset name", p.Name))
	case contractPresets[p.Name].Name != "":
		panic(fmt.Sprintf("registerContractPreset: preset %q registered twice", p.Name))
	case len(p.Enums) == 0:
		panic(fmt.Sprintf("registerContractPreset: preset %q declares no endpoint", p.Name))
	}
	contractPresets[p.Name] = p
	ContractEnums[p.Name] = p.Enums
}

// contractPresetNames are the registered presets, sorted.
func contractPresetNames() []string {
	names := make([]string, 0, len(contractPresets))
	for name := range contractPresets {
		names = append(names, name)
	}
	sort.Strings(names)
	return names
}

// responsesFramingFor is the Responses stream framing of c's upstream.
func responsesFramingFor(t testing.TB, c contracttest.Case) *responsesFraming {
	t.Helper()
	if p, ok := contractPresets[contractDialect(t, c)]; ok && p.ResponsesStream != nil {
		return p.ResponsesStream
	}
	return officialResponsesStream
}

// pathGapDef is a presetPathGap's Gap: a definition with an ID.
var pathGapDef = regexp.MustCompile(`^known gap [A-Z]{1,3}[1-9][0-9]*: \S`)

// TestContractPresetPathGaps pins every registered path gap: the adapter
// still calls contractPaths[Endpoint], which is none of the paths the
// provider documents. When the route is fixed this fails: delete the entry,
// and reclassify the coverage cell (contractcoverage/exemptions.json) once
// it has a fixture.
func TestContractPresetPathGaps(t *testing.T) {
	for _, name := range contractPresetNames() {
		for _, g := range contractPresets[name].PathGaps {
			t.Run(name+"/"+g.Endpoint, func(t *testing.T) {
				if !pathGapDef.MatchString(g.Gap) {
					t.Errorf("Gap %q is not a \"known gap <ID>: <reason>\" definition", g.Gap)
				}
				if len(g.Documented) == 0 || g.Source == "" {
					t.Errorf("a path gap names the documented paths and their source")
				}
				want := contractPaths[g.Endpoint]
				if want == "" {
					t.Fatalf("endpoint %q has no contractPaths entry", g.Endpoint)
				}
				got := pathCalledFor(t, g.Endpoint)
				if got != want {
					t.Errorf("the adapter now calls %s for %s, not %s: if that is a path %s documents (%q), the gap is fixed; delete this entry",
						got, g.Endpoint, want, name, g.Documented)
				}
				for _, doc := range g.Documented {
					if got == doc {
						t.Errorf("the adapter calls %s, which %s documents: the gap is fixed; delete this entry", got, name)
					}
				}
			})
		}
	}
}

// pathCalledFor drives endpoint's provider method against a server that
// records the request path and answers 404, and returns the path.
func pathCalledFor(t *testing.T, endpoint string) string {
	t.Helper()
	var (
		mu   sync.Mutex
		path string
	)
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		mu.Lock()
		path = r.URL.Path
		mu.Unlock()
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusNotFound)
		_, _ = io.WriteString(w, `{"error":{"message":"not found","type":"invalid_request_error"}}`)
	}))
	defer srv.Close()
	p, err := New("test-openai", config.ProviderConfig{BaseURL: srv.URL, APIKey: "test-key"})
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	defer func() { _ = p.Close() }()

	ctx := context.Background()
	switch endpoint {
	case contracttest.EndpointCompletions:
		_, _ = p.TextCompletion(ctx, &models.CompletionRequest{Model: "m", Prompt: []byte(`"p"`)})
	case contracttest.EndpointEmbeddings:
		_, _ = p.CreateEmbedding(ctx, &models.EmbeddingRequest{Model: "m", Input: []byte(`"p"`)})
	case contracttest.EndpointImages:
		_, _ = p.CreateImage(ctx, &models.ImageRequest{Model: "m", Prompt: "p"})
	case contracttest.EndpointSpeech:
		if body, _, err := p.CreateSpeech(ctx, &models.SpeechRequest{Model: "m", Input: "p", Voice: "v"}); err == nil {
			_ = body.Close()
		}
	case contracttest.EndpointTranscription:
		_, _ = p.CreateTranscription(ctx, &models.TranscriptionRequest{Model: "m", FileName: "a.wav", FileData: []byte("RIFF")})
	case contracttest.EndpointTranslation:
		_, _ = p.CreateTranslation(ctx, &models.TranslationRequest{Model: "m", FileName: "a.wav", FileData: []byte("RIFF")})
	case contracttest.EndpointResponses:
		_, _, _ = p.CreateResponse(ctx, []byte(`{"model":"m","input":"p"}`))
	case contracttest.EndpointListModels:
		_, _ = p.ListModels(ctx)
	case contracttest.EndpointOCR:
		_, _ = p.OCR(ctx, &models.OCRRequest{Model: "m"})
	default:
		t.Fatalf("endpoint %q has no driver in pathCalledFor", endpoint)
	}
	mu.Lock()
	defer mu.Unlock()
	if path == "" {
		t.Fatalf("%s sent no request upstream", endpoint)
	}
	return path
}
