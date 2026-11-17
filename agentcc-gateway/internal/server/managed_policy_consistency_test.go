package server

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/catalog"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

func managedListedIDs(t *testing.T, g *managedTestGateway) map[string]models.EnrichedModelObject {
	t.Helper()
	ts := httptest.NewServer(g.srv.httpServer.Handler)
	defer ts.Close()
	req, _ := http.NewRequest(http.MethodGet, ts.URL+"/v1/models", nil)
	req.Header.Set("Authorization", "Bearer "+managedOrgKey)
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	var list models.EnrichedModelListResponse
	if err := json.NewDecoder(resp.Body).Decode(&list); err != nil || resp.StatusCode != 200 {
		t.Fatalf("models: status=%d err=%v", resp.StatusCode, err)
	}
	out := map[string]models.EnrichedModelObject{}
	for _, m := range list.Data {
		out[m.ID] = m
	}
	return out
}

func TestManagedPolicyDeniesListedVariantsEverywhere(t *testing.T) {
	for _, surface := range managedSurfaces {
		for _, stream := range []bool{false, true} {
			for _, name := range []string{decoratedModel, listedVariant, "openrouter/" + listedVariant, "claude-sonnet-5-5"} {
				for _, locked := range []bool{false, true} {
					t.Run(fmt.Sprintf("%s/%t/%s/lock=%t", surface, stream, name, locked), func(t *testing.T) {
						org := managedOptIn()
						org.ManagedModels.DenyModels = []string{decoratedModel}
						g := newManagedTestGateway(t, org)
						withListedVariant(t, g)
						g.ledger.SetMicros("org-m", 1_000_000)
						var headers map[string]string
						if locked {
							headers = managedLock
						}
						status, body, _ := sendManaged(t, g, surface, name, stream, false, headers)
						spent, held := settledAccount(t, g)
						if status != 403 || g.managed.calls() != 0 || spent != 0 || held != 0 {
							t.Fatalf("status=%d calls=%d spent=%d held=%d body=%s", status, g.managed.calls(), spent, held, body)
						}
					})
				}
			}
		}
	}
}

func TestManagedPolicyViewsMatchServing(t *testing.T) {
	off := false
	for _, tc := range []struct {
		name   string
		policy tenant.ManagedModelsConfig
		denied []string
	}{
		{"deny-base", tenant.ManagedModelsConfig{DenyModels: []string{decoratedModel}}, []string{decoratedModel, listedVariant}},
		{"allow-base-does-not-grant-variant", tenant.ManagedModelsConfig{AllowModels: []string{decoratedModel}}, []string{listedVariant}},
		{"allow-vendor", tenant.ManagedModelsConfig{AllowVendors: []string{"openai"}}, []string{decoratedModel, listedVariant}},
		{"deny-vendor", tenant.ManagedModelsConfig{DenyVendors: []string{"anthropic"}}, []string{decoratedModel, listedVariant}},
		{"deny-free", tenant.ManagedModelsConfig{AllowFree: &off}, []string{"qwen/qwen3.7-flash:free"}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			tc.policy.Enabled = true
			org := &tenant.OrgConfig{ManagedModels: &tc.policy}
			g := newManagedTestGateway(t, org)
			withListedVariant(t, g)
			g.ledger.SetMicros("org-m", 1_000_000)
			listed := managedListedIDs(t, g)
			access := buildCatalogAccess(g.srv.handlers.catalog.Load(), org, 5, time.Now())
			for _, id := range tc.denied {
				if _, ok := listed[id]; ok {
					t.Errorf("models advertises denied %s", id)
				}
				for _, surface := range managedSurfaces {
					for _, stream := range []bool{false, true} {
						status, _, _ := sendManaged(t, g, surface, id, stream, false, nil)
						if status != 403 {
							t.Errorf("%s stream=%t %s status=%d", surface, stream, id, status)
						}
					}
				}
				for _, row := range access.Data {
					if row.ID == id && row.Managed {
						t.Errorf("access advertises denied %s", id)
					}
				}
			}
			spent, held := settledAccount(t, g)
			if g.managed.calls() != 0 || spent != 0 || held != 0 {
				t.Fatalf("calls=%d spent=%d held=%d", g.managed.calls(), spent, held)
			}
		})
	}
}

func TestManagedPolicyProviderLockFiltersViews(t *testing.T) {
	org := managedOptIn()
	org.Routing = &tenant.RoutingConfig{ProviderLock: &tenant.ProviderLockConfig{Enabled: true, DenyProviders: []string{"openrouter"}}}
	g := newManagedTestGateway(t, org)
	if _, ok := managedListedIDs(t, g)[decoratedModel]; ok {
		t.Error("provider lock omitted from list")
	}
	for _, row := range buildCatalogAccess(g.srv.handlers.catalog.Load(), org, 5, time.Now()).Data {
		if row.Managed {
			t.Errorf("provider lock omitted from access: %s", row.ID)
		}
	}
}

func TestManagedPolicyFreeDefaultUnchanged(t *testing.T) {
	for _, allow := range []*bool{nil, ptr(true)} {
		org := managedOptIn()
		org.ManagedModels.AllowFree = allow
		g := newManagedTestGateway(t, org)
		g.ledger.SetMicros("org-m", 1_000_000)
		for _, surface := range managedSurfaces {
			status, _, _ := sendManaged(t, g, surface, "qwen/qwen3.7-flash:free", false, false, nil)
			if status != 200 {
				t.Errorf("unset/true free policy status=%d", status)
			}
		}
	}
}

func TestManagedUnavailableDatesDoNotChangeIdentity(t *testing.T) {
	for _, name := range []string{"claude-sonnet-5-5-20990101", "anthropic/claude-sonnet-5.5-20990101", "mistral-large-latest"} {
		for _, surface := range managedSurfaces {
			t.Run(name+surface, func(t *testing.T) {
				g := newManagedTestGateway(t, managedOptIn())
				_, install := installBundledCatalogue(t)
				install(t, g)
				g.ledger.SetMicros("org-m", 1_000_000)
				status, _, _ := sendManaged(t, g, surface, name, false, false, nil)
				spent, held := settledAccount(t, g)
				// An unlisted date is an id nothing lists: 404 model_not_found
				// since DX-ERRORS-1, never served as the undated model.
				if status != 404 || g.managed.calls() != 0 || spent != 0 || held != 0 {
					t.Fatalf("status=%d sent=%s calls=%d spent=%d held=%d", status, g.managed.lastModel(), g.managed.calls(), spent, held)
				}
			})
		}
	}
}

func TestManagedAccessAdvertisesCallableFreeID(t *testing.T) {
	g := newManagedTestGateway(t, managedOptIn())
	_, install := installBundledCatalogue(t)
	install(t, g)
	want := map[string]bool{"cohere/north-mini-code:free": false, "dots-studio/dots-3-note-preview:free": false, "liquid/lfm-2.5-2.6b:free": false, "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free": false}
	for _, row := range buildCatalogAccess(g.srv.handlers.catalog.Load(), managedOptIn(), 5, time.Now()).Data {
		if _, ok := want[row.ID]; ok && row.Managed {
			want[row.ID] = true
		}
	}
	for id, found := range want {
		if !found {
			t.Errorf("access omits callable %s", id)
		}
	}
}

func TestManagedAdminAndAccessExcludeExpiredOffers(t *testing.T) {
	g := newManagedTestGateway(t, managedOptIn())
	all := billabilityModels(t)
	all["acme/token"].ExpirationDate = "2000-01-01"
	installBillabilityModels(all)(t, g)
	ts := httptest.NewServer(g.srv.httpServer.Handler)
	defer ts.Close()
	req, _ := http.NewRequest(http.MethodGet, ts.URL+"/-/catalog", nil)
	req.Header.Set("Authorization", "Bearer "+adminToken)
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	var doc struct {
		Models []catalogModelView `json:"models"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&doc); err != nil {
		t.Fatal(err)
	}
	found := false
	for _, row := range doc.Models {
		if row.ID == "acme/token" {
			found = true
			if row.State != catalog.StateBlocked || row.BilledInputPerMTok != nil || !strings.Contains(row.StateReason, "2000-01-01") {
				t.Errorf("expired admin offer: %+v", row)
			}
		}
	}
	if !found {
		t.Fatal("expired row missing")
	}
	for _, row := range buildCatalogAccess(g.srv.handlers.catalog.Load(), managedOptIn(), 5, time.Now()).Data {
		if row.ID == "acme/token" && row.Access == accessManaged {
			t.Error("expired access counted as managed")
		}
	}
}
