package providers

import (
	"context"
	"os"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/leanovate/gopter"
	"github.com/leanovate/gopter/gen"
	"github.com/leanovate/gopter/prop"
)

// ---------------------------------------------------------------------------
// Stub provider — satisfies Provider interface for property tests.
// ---------------------------------------------------------------------------

type stubProvider struct{ id string }

func (s *stubProvider) ID() string { return s.id }
func (s *stubProvider) ChatCompletion(_ context.Context, _ *models.ChatCompletionRequest) (*models.ChatCompletionResponse, error) {
	return nil, nil
}
func (s *stubProvider) StreamChatCompletion(_ context.Context, _ *models.ChatCompletionRequest) (<-chan models.StreamChunk, <-chan error) {
	ch := make(chan models.StreamChunk)
	close(ch)
	errCh := make(chan error)
	close(errCh)
	return ch, errCh
}
func (s *stubProvider) ListModels(_ context.Context) ([]models.ModelObject, error) { return nil, nil }
func (s *stubProvider) Close() error                                                { return nil }

// newTestRegistry returns a bare Registry with no routing, ready for direct provider registration.
func newTestRegistry() *Registry {
	return &Registry{
		providers:      make(map[string]Provider),
		modelMap:       make(map[string]string),
		modelProviders: make(map[string][]string),
	}
}

// ---------------------------------------------------------------------------
// Generators
// ---------------------------------------------------------------------------

// providerIDGen generates valid provider ID strings (letters + digits + hyphens, no slash).
var providerIDGen = gen.RegexMatch(`[a-z][a-z0-9\-]{0,15}`)

// modelNameGen generates bare model names (no slashes).
var modelNameGen = gen.RegexMatch(`[a-z][a-z0-9\-\.]{0,30}`)

// ---------------------------------------------------------------------------
// Properties: prefix-match resolution
// ---------------------------------------------------------------------------

// Property 1: For any registered provider ID and any bare model name, resolving
// "providerID/modelName" finds that provider.
func TestProp_PrefixMatch_KnownProvider(t *testing.T) {
	properties := gopter.NewProperties(nil)

	properties.Property("prefix-match resolves to registered provider", prop.ForAll(
		func(providerID, modelName string) bool {
			if providerID == "" || modelName == "" {
				return true // skip degenerate cases
			}
			reg := newTestRegistry()
			reg.RegisterProvider(providerID, &stubProvider{id: providerID}, nil)

			result, err := reg.ResolveWithRouting(providerID + "/" + modelName)
			if err != nil {
				return false
			}
			return result.Provider.ID() == providerID
		},
		providerIDGen,
		modelNameGen,
	))

	properties.TestingRun(t, gopter.NewFormatedReporter(false, 80, os.Stdout))
}

// Property 2: Resolving a model with an unknown prefix always returns an error
// (when no model map entry and no single-provider default).
func TestProp_PrefixMatch_UnknownProvider_Errors(t *testing.T) {
	properties := gopter.NewProperties(nil)

	properties.Property("unknown prefix returns error", prop.ForAll(
		func(knownID, unknownPrefix, modelName string) bool {
			if knownID == "" || unknownPrefix == "" || modelName == "" {
				return true
			}
			if strings.EqualFold(knownID, unknownPrefix) {
				return true // same ID — would match
			}
			// Registry has exactly 2 providers so the single-provider default won't fire.
			reg := newTestRegistry()
			reg.RegisterProvider(knownID, &stubProvider{id: knownID}, nil)
			reg.RegisterProvider(knownID+"-2", &stubProvider{id: knownID + "-2"}, nil)

			_, err := reg.ResolveWithRouting(unknownPrefix + "/" + modelName)
			return err != nil
		},
		providerIDGen,
		providerIDGen,
		modelNameGen,
	))

	properties.TestingRun(t, gopter.NewFormatedReporter(false, 80, os.Stdout))
}

// Property 3: A model string without a "/" never triggers the prefix-match path
// (it either resolves from modelMap or errors; it never silently strips a prefix).
func TestProp_NoSlash_NoPrefixStrip(t *testing.T) {
	properties := gopter.NewProperties(nil)

	properties.Property("no-slash model does not strip any prefix", prop.ForAll(
		func(modelName string) bool {
			// Ensure no slash in the generated model name.
			if strings.Contains(modelName, "/") || modelName == "" {
				return true
			}
			// Whatever the result, ResolveModelName must return modelName unchanged.
			got := ResolveModelName(modelName)
			return got == modelName
		},
		modelNameGen,
	))

	properties.TestingRun(t, gopter.NewFormatedReporter(false, 80, os.Stdout))
}

// ---------------------------------------------------------------------------
// Properties: ResolveModelName
// ---------------------------------------------------------------------------

// Property 4: For "providerID/modelName", ResolveModelName returns modelName.
func TestProp_ResolveModelName_StripPrefix(t *testing.T) {
	properties := gopter.NewProperties(nil)

	properties.Property("ResolveModelName strips provider prefix", prop.ForAll(
		func(providerID, modelName string) bool {
			if providerID == "" || modelName == "" {
				return true
			}
			combined := providerID + "/" + modelName
			got := ResolveModelName(combined)
			return got == modelName
		},
		providerIDGen,
		modelNameGen,
	))

	properties.TestingRun(t, gopter.NewFormatedReporter(false, 80, os.Stdout))
}

// Property 5: ResolveModelName is idempotent on already-stripped names.
func TestProp_ResolveModelName_Idempotent(t *testing.T) {
	properties := gopter.NewProperties(nil)

	properties.Property("applying ResolveModelName twice is the same as once", prop.ForAll(
		func(model string) bool {
			if model == "" {
				return true
			}
			once := ResolveModelName(model)
			twice := ResolveModelName(once)
			return once == twice
		},
		gen.RegexMatch(`[a-z][a-z0-9/\-\.]{0,40}`),
	))

	properties.TestingRun(t, gopter.NewFormatedReporter(false, 80, os.Stdout))
}

// Property 6: ResolveModelName never returns an empty string for a non-empty input.
func TestProp_ResolveModelName_NonEmpty(t *testing.T) {
	properties := gopter.NewProperties(nil)

	properties.Property("ResolveModelName output is non-empty for non-empty input", prop.ForAll(
		func(model string) bool {
			if model == "" {
				return true
			}
			// Edge: if model ends with "/" the suffix is "" — that's a degenerate input.
			if strings.HasSuffix(model, "/") {
				return true
			}
			got := ResolveModelName(model)
			return got != ""
		},
		gen.RegexMatch(`[a-z][a-z0-9/\-\.]{0,40}`),
	))

	properties.TestingRun(t, gopter.NewFormatedReporter(false, 80, os.Stdout))
}

// ---------------------------------------------------------------------------
// Properties: single-provider default
// ---------------------------------------------------------------------------

// Property 7: When exactly one provider is registered and a model is not in the
// model map, that provider is always returned regardless of model name (no slash).
func TestProp_SingleProvider_Default(t *testing.T) {
	properties := gopter.NewProperties(nil)

	properties.Property("single registered provider is the default for unknown models", prop.ForAll(
		func(providerID, modelName string) bool {
			if providerID == "" || modelName == "" {
				return true
			}
			// Ensure no slash so prefix-match path is not taken.
			if strings.Contains(modelName, "/") {
				return true
			}
			reg := newTestRegistry()
			reg.RegisterProvider(providerID, &stubProvider{id: providerID}, nil)

			result, err := reg.ResolveWithRouting(modelName)
			if err != nil {
				return false
			}
			return result.Provider.ID() == providerID
		},
		providerIDGen,
		modelNameGen,
	))

	properties.TestingRun(t, gopter.NewFormatedReporter(false, 80, os.Stdout))
}
