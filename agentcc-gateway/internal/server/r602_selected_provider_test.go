package server

import (
	"context"
	"fmt"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/billing"
	"github.com/futureagi/agentcc-gateway/internal/billing/inclusive"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	openaipkg "github.com/futureagi/agentcc-gateway/internal/providers/openai"
)

func TestR602BindSelectedProviderNotReloadedRegistry(t *testing.T) {
	g, l, _ := r601Gateway(t)
	selectedServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer synthetic-selected-instance" {
			t.Error("credential from reloaded registry sent to previously selected endpoint")
		}
		w.Header().Set("Content-Type", "application/json")
		fmt.Fprint(w, `{"id":"selected-generation","model":"google/gemini-3.5-flash","choices":[],"usage":{"cost":0.0000095}}`)
	}))
	defer selectedServer.Close()
	selected, err := openaipkg.New("openrouter", config.ProviderConfig{BaseURL: selectedServer.URL, APIKey: "synthetic-selected-instance"})
	if err != nil {
		t.Fatal(err)
	}
	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.RequestID = "selected-provider"
	rc.Provider = "openrouter"
	rc.Model = "google/gemini-3.5-flash"
	rc.Metadata["org_id"] = "org-m"
	rc.Metadata["billing_mode"] = "managed"
	maxOut := 64
	rc.Request = &models.ChatCompletionRequest{Model: rc.Model, MaxTokens: &maxOut}
	card, ok := g.srv.handlers.catalog.Load().PriceCard("openrouter", rc.Model, "")
	if !ok {
		t.Fatal("no image-family card")
	}
	hold, err := inclusive.Bound(rc.Request, card.Inclusive)
	if err != nil {
		t.Fatal(err)
	}
	rc.Billing.ReservePrice = &card
	rc.Billing.InclusiveHoldMicros = hold
	rc.Billing.HoldMicros = hold
	rc.Billing.Reserved = true
	if _, err = l.ReserveInclusive(context.Background(), "org-m", rc.RequestID, models.AttemptID(rc.RequestID, 1), rc.Model, hold, time.Hour, billing.ReserveNew); err != nil {
		t.Fatal(err)
	}
	if _, err = g.srv.handlers.callChat(context.Background(), rc, selected, "openrouter", models.AttemptPrimary, nil); err != nil {
		t.Fatal(err)
	}
	a := rc.AttemptsSnapshot()
	if len(a) != 1 || a[0].InclusiveIdentity.Credential != models.CredentialFingerprint("synthetic-selected-instance") {
		t.Fatal("attempt bound to a different provider instance")
	}
}
