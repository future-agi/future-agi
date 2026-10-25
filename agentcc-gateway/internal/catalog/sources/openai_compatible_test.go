package sources

import (
	"os"
	"path/filepath"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/catalog"
)

func fixture(t *testing.T, name string) []byte {
	t.Helper()
	b, err := os.ReadFile(filepath.Join("testdata", name))
	if err != nil {
		t.Fatal(err)
	}
	return b
}

// TestOpenAICompatibleParser runs the one generic parser over three shapes
// hand-built from the providers' documented /models responses (no network
// in tests): an OpenRouter-shaped list with pricing, a plain OpenAI list
// (Groq's, whose context_window is not read), and a bare array (Together's,
// whose per-million pricing is not OpenRouter-shaped).
func TestOpenAICompatibleParser(t *testing.T) {
	t.Run("openrouter-shaped pricing and context", func(t *testing.T) {
		got, err := ParseOpenAICompatible(fixture(t, "openrouter_shaped.json"), "deepinfra")
		if err != nil {
			t.Fatal(err)
		}
		if len(got) != 3 {
			t.Fatalf("models = %v", keysOf(got))
		}
		a := got["Vendor/Model-A"]
		if a == nil || a.Unpriced || !a.Priced() || a.ContextLength != 131072 ||
			a.Prices.Prompt != 4e-7 || a.Prices.Completion != 16e-7 || a.Prices.CacheRead != 1e-7 {
			t.Fatalf("model A = %+v", a)
		}
		// Only pricing and context_length are read; other fields are ignored.
		if a.Name != "" || a.Created != 0 || len(a.SupportedParameters) != 0 || len(a.InputModalities) != 0 {
			t.Errorf("model A read fields it should ignore: %+v", a)
		}
		if len(a.Endpoints) != 1 {
			t.Fatalf("model A endpoints = %+v", a.Endpoints)
		}
		e := a.Endpoints[0]
		if e.ID != catalog.EndpointID(catalog.SourceOpenAICompatible, "deepinfra", "Vendor/Model-A") ||
			e.Source != catalog.SourceOpenAICompatible || e.UpstreamProvider != "deepinfra" || e.Tag != "deepinfra" ||
			e.Prices != a.Prices || e.ContextLength != 131072 || e.DataCollection != catalog.DataCollectionUnknown {
			t.Errorf("endpoint = %+v", e)
		}
		// Such an upstream reports no cost, so a price that cannot bill a call
		// by itself is no price: a negative (dynamic) one, or all zero. Both
		// are listed unpriced, with no endpoint, and never offered as managed.
		for _, id := range []string{"vendor/router", "vendor/free-model"} {
			if m := got[id]; m == nil || !m.Unpriced || m.DynamicPrice || m.Sellable() || len(m.Endpoints) != 0 {
				t.Errorf("%s = %+v; want listed unpriced", id, m)
			}
		}
	})

	t.Run("prices that cannot bill a chat call", func(t *testing.T) {
		got, err := ParseOpenAICompatible([]byte(`{"data": [
			{"id": "img", "pricing": {"image": "0.04"}},
			{"id": "per-request", "pricing": {"prompt": "0", "completion": "0", "request": "0.005"}},
			{"id": "output-only", "pricing": {"completion": "0.000002"}}
		]}`), "x")
		if err != nil {
			t.Fatal(err)
		}
		if m := got["img"]; !m.Unpriced || len(m.Endpoints) != 0 {
			t.Errorf("an image-only price = %+v; want listed unpriced", m)
		}
		// The cost plugin bills a request price, or an output price, alone.
		for _, id := range []string{"per-request", "output-only"} {
			if m := got[id]; m.Unpriced || !m.Priced() || len(m.Endpoints) != 1 {
				t.Errorf("%s = %+v; want priced", id, m)
			}
		}
	})

	t.Run("plain list without pricing", func(t *testing.T) {
		got, err := ParseOpenAICompatible(fixture(t, "openai_plain.json"), "groq")
		if err != nil {
			t.Fatal(err)
		}
		m := got["llama-3.3-70b-versatile"]
		if len(got) != 2 || m == nil || !m.Unpriced || m.Priced() || m.ContextLength != 0 {
			t.Fatalf("plain list = %v, llama = %+v", keysOf(got), m)
		}
		// Unpriced models are listed but never offered: no endpoint.
		if len(m.Endpoints) != 0 {
			t.Errorf("an unpriced model has endpoints: %+v", m.Endpoints)
		}
	})

	t.Run("bare array with another pricing shape", func(t *testing.T) {
		got, err := ParseOpenAICompatible(fixture(t, "bare_array.json"), "together")
		if err != nil {
			t.Fatal(err)
		}
		m := got["meta-llama/Llama-3.3-70B-Instruct-Turbo"]
		if len(got) != 2 || m == nil || !m.Unpriced || m.ContextLength != 131072 || len(m.Endpoints) != 0 {
			t.Fatalf("bare array = %v, llama = %+v", keysOf(got), m)
		}
	})

	for name, body := range map[string]string{
		"empty list": `{"data": []}`, "empty array": `[]`, "not json": `<html>`, "no ids": `{"data": [{"object": "model"}]}`,
	} {
		if _, err := ParseOpenAICompatible([]byte(body), "x"); err == nil {
			t.Errorf("%s parsed without an error", name)
		}
	}
}

func keysOf[V any](m map[string]V) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	return out
}
