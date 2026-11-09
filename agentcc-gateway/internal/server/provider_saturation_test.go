package server

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers/admission"
)

var saturationSeq atomic.Int64

// TestSaturationOnHealthAndReadiness: a saturated provider entry is listed
// with its slots on the admin GET /-/health/providers, and /readyz reports
// how many entries are saturated, never their names, and stays 200.
func TestSaturationOnHealthAndReadiness(t *testing.T) {
	entry := fmt.Sprintf("sat-entry-%d", saturationSeq.Add(1))
	l := admission.New(entry, 2, time.Second)
	defer l.Close()
	for i := 0; i < 2; i++ {
		if err := l.Acquire(context.Background()); err != nil {
			t.Fatal(err)
		}
		defer l.Release()
	}
	g := newManagedTestGateway(t, managedOptIn())

	w := httptest.NewRecorder()
	g.srv.httpServer.Handler.ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/readyz", nil))
	var ready struct {
		Status    string `json:"status"`
		Saturated int    `json:"saturated_provider_entries"`
	}
	if err := json.Unmarshal(w.Body.Bytes(), &ready); err != nil || w.Code != http.StatusOK || ready.Status != "ready" || ready.Saturated < 1 {
		t.Fatalf("/readyz %d %s; want 200 ready with a saturated count", w.Code, w.Body)
	}
	if strings.Contains(w.Body.String(), entry) {
		t.Fatalf("/readyz names an entry: %s", w.Body)
	}

	req := httptest.NewRequest(http.MethodGet, "/-/health/providers", nil)
	req.Header.Set("Authorization", "Bearer "+adminToken)
	w = httptest.NewRecorder()
	g.srv.httpServer.Handler.ServeHTTP(w, req)
	var health struct {
		Saturation []admission.EntryState `json:"saturation"`
	}
	if err := json.Unmarshal(w.Body.Bytes(), &health); err != nil || w.Code != http.StatusOK {
		t.Fatalf("/-/health/providers %d %s", w.Code, w.Body)
	}
	found := false
	for _, st := range health.Saturation {
		if st.Entry == entry {
			found = true
			if !st.Saturated || st.InFlight != 2 || st.MaxConcurrent != 2 {
				t.Fatalf("entry state %+v", st)
			}
		}
	}
	if !found {
		t.Fatalf("/-/health/providers lacks %s: %s", entry, w.Body)
	}
}

// TestBusyHopKeepsTheBillingMode: after an attempt ended provider_busy, a hop
// that would use the other credential type is refused and one in the same
// mode is not; with no busy attempt the guard stays out of the way.
func TestBusyHopKeepsTheBillingMode(t *testing.T) {
	g := newManagedTestGateway(t, managedOptIn())
	h := g.srv.handlers
	rc := models.AcquireRequestContext()
	defer rc.Release()

	rc.Metadata["billing_mode"] = billingManaged
	if h.busyHopChangesMode(rc, "operator") || h.busyHopChangesMode(rc, "openrouter") {
		t.Fatal("the guard refuses a hop with no busy attempt before it")
	}

	rc.Billing.BusyCredential = models.CredentialManaged
	if h.busyHopChangesMode(rc, "openrouter") {
		t.Error("managed busy -> managed hop refused")
	}
	rc.Metadata["billing_mode"] = billingBYOK
	if !h.busyHopChangesMode(rc, "operator") {
		t.Error("managed busy -> BYOK hop allowed")
	}

	rc.Billing.BusyCredential = models.CredentialBYOK
	if h.busyHopChangesMode(rc, "operator") {
		t.Error("BYOK busy -> BYOK hop refused")
	}
	rc.Metadata["billing_mode"] = billingManaged
	if !h.busyHopChangesMode(rc, "openrouter") {
		t.Error("BYOK busy -> managed hop allowed")
	}
}
