package server

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/catalog"
	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/routing/eligibility"
)

const r601CalendarImage = "google/gemini-2.5-flash-image"

// Read the shipped calendar, not an obsolete date copied into a test. Parse
// the date independently of ShutDown so a broken lifecycle predicate cannot
// silently change the expected outcome too.
func r601PinnedShutdown(t *testing.T, g *managedTestGateway, id string) (modeldb.Deprecation, time.Time, bool) {
	t.Helper()
	h := g.srv.handlers
	d, ok := h.deprecations.Lookup(h.deprecationHost(nil, "openrouter"), id)
	if !ok {
		if id == r601CalendarImage {
			t.Fatal("pinned image shutdown is missing")
		}
		return d, time.Time{}, false
	}
	at, err := time.Parse(time.DateOnly, d.ShutdownDate)
	if err != nil {
		t.Fatal(err)
	}
	return d, at, true
}

func r601CalendarRetired(t *testing.T, g *managedTestGateway, id string, now time.Time) bool {
	t.Helper()
	_, shutdown, ok := r601PinnedShutdown(t, g, id)
	return ok && !now.Before(shutdown)
}

func r601CatalogAt(t *testing.T, g *managedTestGateway, at time.Time) {
	t.Helper()
	opts := catalog.OptionsFromConfig(g.srv.cfg, []catalog.Source{{ProviderID: "openrouter", Type: catalog.SourceOpenRouter}})
	// Preserve the installed fixture's per-model opt-in. R601 opts in directly,
	// whereas CAT2 installs the authoritative startup gate before this call.
	opts.CAT2Enabled = nil
	opts.Now = func() time.Time { return at }
	if err := g.srv.handlers.catalog.Configure(opts); err != nil {
		t.Fatal(err)
	}
}

func r601CatalogAfterShutdown(t *testing.T, g *managedTestGateway) modeldb.Deprecation {
	t.Helper()
	d, shutdown, _ := r601PinnedShutdown(t, g, r601CalendarImage)
	r601CatalogAt(t, g, shutdown.Add(24*time.Hour))
	return d
}

// HTTP admission uses wall time (hook_policy.go's eligibility.Input leaves Now
// unset), unlike catalog views. Keep its date-derived HTTP assertions and also
// exercise the real eligibility engine with deterministic times on both sides
// of the pinned shutdown. This must still prove 410 after retirement even while
// the wall-clock HTTP matrix exercises a listing that is not retired yet.
func r601CheckCalendarBoundary(t *testing.T, g *managedTestGateway, managed bool) {
	t.Helper()
	d, shutdown, _ := r601PinnedShutdown(t, g, r601CalendarImage)
	entry, ok := g.srv.handlers.catalog.Load().Get("openrouter", r601CalendarImage)
	if !ok || entry.ExpirationDate != "" {
		t.Fatal("calendar-only retirement fixture missing or masked by listing expiry")
	}
	for _, tc := range []struct {
		name    string
		at      time.Time
		retired bool
	}{
		{"before_shutdown", shutdown.Add(-time.Nanosecond), false},
		{"on_shutdown", shutdown, true},
		{"after_shutdown", shutdown.Add(24 * time.Hour), true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			in := eligibility.Input{Model: r601CalendarImage, ProviderID: "openrouter", Org: managedOptIn(),
				Managed: managed, Aggregator: true, Entry: entry, Shutdowns: g.srv.handlers.deprecations,
				Host: g.srv.handlers.deprecationHost(nil, "openrouter"), Now: tc.at}
			v := eligibility.Check(&in)
			if !tc.retired {
				if !v.OK {
					t.Fatalf("listing refused before pinned shutdown %s: %+v", d.ShutdownDate, v)
				}
				return
			}
			if v.OK || v.Code != eligibility.CodeModelExpired || eligibility.StatusFor(v.Code) != http.StatusGone ||
				!strings.Contains(v.Reason, d.ShutdownDate) || !strings.Contains(v.Reason, d.Replacement) {
				t.Fatalf("calendar retirement at %s must refuse with 410 and pinned reason: %+v", tc.at, v)
			}
		})
	}
}

func TestR601RetiredListingNotAdvertised(t *testing.T) {
	g, _, _ := r601Gateway(t)
	_, shutdown, _ := r601PinnedShutdown(t, g, r601CalendarImage)
	paths := []string{"/v1/models", "/-/orgs/org-m/catalog/access"}
	check := func(t *testing.T, path string, wantListed bool) {
		t.Helper()
		req := httptest.NewRequest(http.MethodGet, path, nil)
		credential := managedOrgKey
		if strings.HasPrefix(path, "/-/") {
			credential = adminToken
		}
		req.Header.Set("Authorization", "Bearer "+credential)
		w := httptest.NewRecorder()
		g.srv.httpServer.Handler.ServeHTTP(w, req)
		if w.Code != 200 {
			t.Fatalf("status %d body %s", w.Code, w.Body)
		}
		var doc map[string]json.RawMessage
		if json.Unmarshal(w.Body.Bytes(), &doc) != nil {
			t.Fatal("invalid JSON")
		}
		listed := strings.Contains(w.Body.String(), `"google/gemini-2.5-flash-image"`)
		if listed != wantListed {
			t.Fatalf("calendar listing at %s: advertised=%t want=%t", g.srv.handlers.catalog.Now(), listed, wantListed)
		}
	}
	t.Run("before_shutdown", func(t *testing.T) {
		r601CatalogAt(t, g, shutdown.Add(-time.Nanosecond))
		for _, path := range paths {
			t.Run(path, func(t *testing.T) { check(t, path, true) })
		}
	})
	r601CatalogAfterShutdown(t, g)
	for _, path := range paths {
		t.Run(path, func(t *testing.T) { check(t, path, false) })
	}
}
