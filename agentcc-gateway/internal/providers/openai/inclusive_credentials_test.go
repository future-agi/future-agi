package openai

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers/admission"
)

func TestR602CredentialPinnedAcrossProviderReload(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer synthetic-current" {
			t.Error("dispatch and journal used different credentials")
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"id":"generation","model":"served","choices":[],"usage":{"cost":0}}`))
	}))
	defer srv.Close()
	// Routing selected the old instance, but the registry changed before begin.
	old := &Provider{id: "openrouter", apiKey: "synthetic-old", baseURL: srv.URL, httpClient: srv.Client()}
	old.slots = admission.New(old.id, 1, 0)
	t.Cleanup(old.slots.Close)
	current := &Provider{id: "openrouter", apiKey: "synthetic-current"}
	ctx, id := current.BindInclusiveCredential(context.Background())
	if id != models.CredentialFingerprint("synthetic-current") {
		t.Fatal("wrong fingerprint")
	}
	if _, err := old.ChatCompletion(ctx, &models.ChatCompletionRequest{Model: "openrouter/auto"}); err != nil {
		t.Fatal(err)
	}
	ctx = models.WithInclusiveCredential(ctx, "openrouter", "synthetic-previous-org")
	ctx = models.WithoutInclusiveCredential(ctx)
	_, id = current.BindInclusiveCredential(ctx)
	if id != models.CredentialFingerprint("synthetic-current") {
		t.Fatal("previous attempt credential survived new binding")
	}
}
