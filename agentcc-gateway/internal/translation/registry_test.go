package translation_test

import (
	"context"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/translation"
)

// fakeTranslator is a minimal InboundTranslator used only for registry tests.
type fakeTranslator struct{ name string }

func (f *fakeTranslator) Name() string { return f.name }
func (f *fakeTranslator) RequestToCanonical([]byte) (*models.ChatCompletionRequest, []string, error) {
	return nil, nil, nil
}
func (f *fakeTranslator) ResponseFromCanonical(*models.ChatCompletionResponse) ([]byte, error) {
	return nil, nil
}
func (f *fakeTranslator) StreamEventsFromCanonical(
	ctx context.Context,
	chunks <-chan models.StreamChunk,
) (<-chan []byte, <-chan error) {
	return nil, nil
}
func (f *fakeTranslator) ErrorFromCanonical(*models.APIError) (int, []byte, string) {
	return 0, nil, ""
}

func TestRegisterAndLookup(t *testing.T) {
	ft := &fakeTranslator{name: "test-format"}
	translation.Register(ft)

	got, ok := translation.InboundFor("test-format")
	if !ok {
		t.Fatal("expected InboundFor to return true after Register")
	}
	if got.Name() != "test-format" {
		t.Fatalf("got name %q, want %q", got.Name(), "test-format")
	}
}

func TestLookupUnknownFormat(t *testing.T) {
	_, ok := translation.InboundFor("does-not-exist-xyz")
	if ok {
		t.Fatal("expected InboundFor to return false for unknown format")
	}
}

func TestAllContainsRegistered(t *testing.T) {
	ft := &fakeTranslator{name: "all-test-format"}
	translation.Register(ft)

	names := translation.All()
	found := false
	for _, n := range names {
		if n == "all-test-format" {
			found = true
			break
		}
	}
	if !found {
		t.Fatalf("All() = %v, want it to contain %q", names, "all-test-format")
	}
}

func TestRegisterOverwrite(t *testing.T) {
	first := &fakeTranslator{name: "overwrite-format"}
	second := &fakeTranslator{name: "overwrite-format"}
	translation.Register(first)
	translation.Register(second)

	got, ok := translation.InboundFor("overwrite-format")
	if !ok {
		t.Fatal("expected InboundFor to return true")
	}
	// second registration should win; both are *fakeTranslator so check pointer identity
	if got != second {
		t.Fatal("expected second registration to overwrite the first")
	}
}
