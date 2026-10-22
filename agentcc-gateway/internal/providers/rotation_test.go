package providers

import (
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"sync"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// TestRotationProviderKeySwap: RotateProviderKey swaps the provider's
// instance for one built from the same config with the new key; lookups
// (GetProvider, Resolve) return it at once; the previous instance is returned
// to the caller and still answers with the old key; a rotation candidate is
// never registered.
func TestRotationProviderKeySwap(t *testing.T) {
	var mu sync.Mutex
	var auths []string
	up := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		io.Copy(io.Discard, r.Body)
		if r.Method == http.MethodPost {
			mu.Lock()
			auths = append(auths, r.Header.Get("Authorization"))
			mu.Unlock()
		}
		w.Header().Set("Content-Type", "application/json")
		io.WriteString(w, `{"id":"x","object":"chat.completion","created":1,"model":"gpt-4o","choices":[{"index":0,"message":{"role":"assistant","content":"ok"},"finish_reason":"stop"}],"usage":{"prompt_tokens":1,"completion_tokens":1,"total_tokens":2}}`)
	}))
	defer up.Close()
	last := func() string {
		mu.Lock()
		defer mu.Unlock()
		return auths[len(auths)-1]
	}
	cfg := config.DefaultConfig()
	cfg.Providers["openai"] = config.ProviderConfig{BaseURL: up.URL, APIKey: "sk-old", APIFormat: "openai", Models: []string{"gpt-4o"}}
	r, err := NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	req := &models.ChatCompletionRequest{Model: "gpt-4o", Messages: []models.Message{{Role: "user", Content: []byte(`"hi"`)}}}
	call := func(p Provider) {
		t.Helper()
		if _, err := p.ChatCompletion(context.Background(), req); err != nil {
			t.Fatal(err)
		}
	}

	before, _ := r.GetProvider("openai")
	candidate, format, modelIDs, err := r.RotationCandidate("openai", "sk-candidate")
	if err != nil || format != "openai" || len(modelIDs) != 1 || modelIDs[0] != "gpt-4o" {
		t.Fatalf("RotationCandidate = %v %q %v %v", candidate, format, modelIDs, err)
	}
	call(candidate)
	if last() != "Bearer sk-candidate" {
		t.Errorf("the candidate sent %q", last())
	}
	candidate.Close()
	if p, _ := r.GetProvider("openai"); p != before {
		t.Fatal("RotationCandidate registered its instance")
	}

	old, err := r.RotateProviderKey("openai", "sk-new")
	if err != nil || old != before {
		t.Fatalf("RotateProviderKey = %v, %v; want the previous instance", old, err)
	}
	p, _ := r.GetProvider("openai")
	call(p)
	if last() != "Bearer sk-new" {
		t.Errorf("GetProvider after the rotation sent %q, want the new key", last())
	}
	resolved, err := r.Resolve("gpt-4o")
	if err != nil {
		t.Fatal(err)
	}
	call(resolved)
	if last() != "Bearer sk-new" {
		t.Errorf("Resolve after the rotation sent %q, want the new key", last())
	}
	call(old) // a call already holding the previous instance
	if last() != "Bearer sk-old" {
		t.Errorf("the previous instance sent %q, want the old key", last())
	}

	// A second rotation builds from the config the first one left.
	if _, err := r.RotateProviderKey("openai", "sk-newer"); err != nil {
		t.Fatal(err)
	}
	p, _ = r.GetProvider("openai")
	call(p)
	if last() != "Bearer sk-newer" {
		t.Errorf("after a second rotation the provider sent %q", last())
	}
	if _, err := r.RotateProviderKey("absent", "k"); err == nil {
		t.Error("RotateProviderKey of an unknown provider succeeded")
	}
}

// uncomparableProvider is a Provider whose dynamic type cannot be a map key or
// be compared with ==, as a test fake registered with RegisterProvider can be.
type uncomparableProvider struct {
	Provider
	tags []string
}

// TestRotationCurrentInstance: work that captured a provider instance and
// runs later (an async job queued before a promote) gets the instance serving
// now, so it sends the key in force when it starts: CurrentInstance maps an
// instance a rotation replaced, through any number of later rotations, to the
// current one, and returns every other instance unchanged (the current one, an
// org's own provider built apart from the registry, a rotation candidate, a
// runtime-registered fake, nil).
func TestRotationCurrentInstance(t *testing.T) {
	var mu sync.Mutex
	var auths []string
	up := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		io.Copy(io.Discard, r.Body)
		if r.Method == http.MethodPost {
			mu.Lock()
			auths = append(auths, r.Header.Get("Authorization"))
			mu.Unlock()
		}
		w.Header().Set("Content-Type", "application/json")
		io.WriteString(w, `{"id":"x","object":"chat.completion","created":1,"model":"gpt-4o","choices":[{"index":0,"message":{"role":"assistant","content":"ok"},"finish_reason":"stop"}],"usage":{"prompt_tokens":1,"completion_tokens":1,"total_tokens":2}}`)
	}))
	defer up.Close()
	cfg := config.DefaultConfig()
	pcfg := config.ProviderConfig{BaseURL: up.URL, APIKey: "sk-old", APIFormat: "openai", Models: []string{"gpt-4o"}}
	cfg.Providers["openai"] = pcfg
	r, err := NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	req := &models.ChatCompletionRequest{Model: "gpt-4o", Messages: []models.Message{{Role: "user", Content: []byte(`"hi"`)}}}
	sent := func(p Provider) string {
		t.Helper()
		if _, err := p.ChatCompletion(context.Background(), req); err != nil {
			t.Fatal(err)
		}
		mu.Lock()
		defer mu.Unlock()
		return auths[len(auths)-1]
	}

	queued, _ := r.GetProvider("openai") // an async job captures it
	if got := r.CurrentInstance(queued); got != queued {
		t.Fatal("CurrentInstance changed the current instance before any rotation")
	}
	if _, err := r.RotateProviderKey("openai", "sk-new"); err != nil {
		t.Fatal(err)
	}
	if got := sent(r.CurrentInstance(queued)); got != "Bearer sk-new" {
		t.Errorf("an instance captured before the promote, started after it, sent %q; want the new key", got)
	}
	second, _ := r.GetProvider("openai")
	if got := r.CurrentInstance(second); got != second {
		t.Error("CurrentInstance changed the current instance")
	}
	if _, err := r.RotateProviderKey("openai", "sk-newer"); err != nil {
		t.Fatal(err)
	}
	for name, p := range map[string]Provider{"captured before both rotations": queued, "captured between them": second} {
		if got := sent(r.CurrentInstance(p)); got != "Bearer sk-newer" {
			t.Errorf("an instance %s sent %q; want the key of the last rotation", name, got)
		}
	}

	// Instances the registry never served for a provider stay as they are.
	orgCfg := pcfg
	orgCfg.APIKey = "sk-org-own"
	orgOwn, err := createProvider("openai", orgCfg) // as OrgProviderCache builds an org's provider
	if err != nil {
		t.Fatal(err)
	}
	if got := sent(r.CurrentInstance(orgOwn)); got != "Bearer sk-org-own" {
		t.Errorf("an org's own provider sent %q; want the org's key", got)
	}
	candidate, _, _, err := r.RotationCandidate("openai", "sk-candidate")
	if err != nil {
		t.Fatal(err)
	}
	if r.CurrentInstance(candidate) != candidate {
		t.Error("CurrentInstance changed a rotation candidate")
	}
	fake := uncomparableProvider{tags: []string{"x"}}
	r.RegisterProvider("fake", fake, nil)
	if got, ok := r.CurrentInstance(fake).(uncomparableProvider); !ok || len(got.tags) != 1 {
		t.Error("CurrentInstance changed an uncomparable provider")
	}
	if r.CurrentInstance(nil) != nil {
		t.Error("CurrentInstance(nil) is not nil")
	}
}
