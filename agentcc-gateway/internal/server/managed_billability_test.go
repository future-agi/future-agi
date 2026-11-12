package server

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/catalog"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// These refresh fixtures exercise prices independently of the bundled snapshot.
func billabilityModels(t *testing.T) map[string]*catalog.Model {
	t.Helper()
	all, err := catalog.ParseOpenRouter([]byte(`{"data":[
		{"id":"acme/token","pricing":{"prompt":"0.000002","completion":"0.000010"}},
		{"id":"acme/request","pricing":{"prompt":"0","completion":"0","request":"0.04"}},
		{"id":"acme/listed:free","pricing":{"prompt":"0","completion":"0"}},
		{"id":"openrouter/free","pricing":{"prompt":"0","completion":"0"}},
		{"id":"acme/unknown-zero","pricing":{"prompt":"0","completion":"0"}},
		{"id":"acme/malformed","pricing":{"prompt":"bad","completion":"0.01"}},
		{"id":"acme/missing-price"},
		{"id":"acme/missing-price:free"},
		{"id":"acme/dynamic","pricing":{"prompt":"-1","completion":"-1"}},
		{"id":"acme/token:nitro","pricing":{"prompt":"0.000020","completion":"0.000100"}},
		{"id":"acme/unsupported","pricing":{"prompt":"0","completion":"0","per_generation":"0.04"}}
	]}`))
	if err != nil {
		t.Fatal(err)
	}
	all["acme/unpriced"] = &catalog.Model{ID: "acme/unpriced", Unpriced: true, Prices: catalog.Prices{Prompt: 1e-6}}
	all["acme/stale"] = &catalog.Model{ID: "acme/stale", StalePrice: true, Prices: catalog.Prices{Prompt: 1e-6}}
	all["acme/out-of-range"] = &catalog.Model{ID: "acme/out-of-range", Prices: catalog.Prices{Prompt: 1e308}}
	bundled, _, err := catalog.BundledOpenRouter()
	if err != nil {
		t.Fatal(err)
	}
	for _, id := range []string{"google/lyria-3-clip-preview", "google/lyria-3-pro-preview"} {
		if bundled[id] == nil {
			t.Fatalf("missing paid-generation guard %s", id)
		}
		all[id] = bundled[id]
	}
	for name, price := range map[string]string{
		"dynamic-endpoint":   `{"prompt":"-1","completion":"-1"}`,
		"zero-endpoint":      `{"prompt":"0","completion":"0"}`,
		"missing-endpoint":   `{}`,
		"malformed-endpoint": `{"prompt":"bad","completion":"0.01"}`,
		"mixed-endpoints":    `{"prompt":"0","completion":"0"}`,
		"endpoint:free":      `{"prompt":"0","completion":"0"}`,
	} {
		id := "acme/" + name
		m := *all["acme/token"]
		if name == "endpoint:free" {
			m = *all["acme/listed:free"]
		}
		m.ID = id
		eps, err := catalog.ParseOpenRouterEndpoints(id, []byte(fmt.Sprintf(`{"data":{"id":%q,"endpoints":[{"tag":"mock","pricing":%s}]}}`, id, price)))
		if err != nil {
			t.Fatal(err)
		}
		m.Endpoints = eps
		if name == "mixed-endpoints" {
			m.Endpoints = append(m.Endpoints, catalog.Endpoint{ID: "priced", UpstreamProvider: "priced", Source: catalog.SourceOpenRouter, Prices: m.Prices})
		}
		all[id] = &m
	}
	return all
}

func installBillabilityModels(all map[string]*catalog.Model) func(*testing.T, *managedTestGateway) {
	return func(t *testing.T, g *managedTestGateway) {
		t.Helper()
		g.srv.handlers.catalog.Put(&catalog.Snapshot{ProviderID: "openrouter", CatalogSource: catalog.SourceOpenRouter, Source: catalog.OriginLive, FetchedAt: time.Now(), Models: all})
		g.srv.RefreshModelDB()
	}
}

var refusedBillabilityIDs = []string{
	"acme/unknown-zero", "acme/malformed", "acme/missing-price", "acme/missing-price:free",
	"acme/dynamic", "acme/unpriced", "acme/stale", "acme/unsupported", "acme/absent", "acme/token:floor", "acme/out-of-range",
	"google/lyria-3-clip-preview", "google/lyria-3-pro-preview",
	"acme/dynamic-endpoint", "acme/zero-endpoint", "acme/missing-endpoint", "acme/malformed-endpoint",
}

// unlistedBillabilityIDs are the refused ids the catalogue does not list at
// all: an unknown id, and a variant of a listed model that is not listed.
var unlistedBillabilityIDs = map[string]bool{"acme/absent": true, "acme/token:floor": true}

func TestManagedBillabilityRefusesBeforeDispatch(t *testing.T) {
	lists := installBillabilityModels(billabilityModels(t))
	for _, id := range refusedBillabilityIDs {
		for _, surface := range managedSurfaces {
			for _, stream := range []bool{false, true} {
				for _, prefix := range []string{"", "openrouter/"} {
					t.Run(fmt.Sprintf("%s/%s/%t/%s", id, surface, stream, prefix), func(t *testing.T) {
						name := prefix + id
						r := runManagedOn(t, lists, surface, name, stream, managedGatewayOptions{}, nil, false, nil)
						if unlistedBillabilityIDs[id] {
							// Listed nowhere: 404 model_not_found by its id
							// (DX-ERRORS-1), 403 behind the provider prefix.
							refusedUnlisted(t, name, name, r)
							return
						}
						refusedAsNotAvailable(t, name, name, r)
					})
				}
			}
		}
		t.Run(id+"/provider-lock", func(t *testing.T) {
			r := runManagedOn(t, lists, managedSurfaces[0], id, true, managedGatewayOptions{}, nil, false, managedLock)
			if unlistedBillabilityIDs[id] {
				refusedAsNotFound(t, id, id, r)
			} else {
				refusedAsNotAvailable(t, id, "openrouter", r)
			}
		})
	}
}

func TestManagedBillabilitySettlesSupportedPrices(t *testing.T) {
	lists := installBillabilityModels(billabilityModels(t))
	for _, tc := range []struct {
		id        string
		estimated int64
		free      bool
	}{
		{"acme/token", 48, false}, {"acme/request", 42000, false}, {"acme/token:nitro", 483, false},
		{"acme/mixed-endpoints", 48, false}, {"acme/listed:free", 0, true}, {"openrouter/free", 0, true}, {"acme/endpoint:free", 0, true},
	} {
		for _, surface := range managedSurfaces {
			for _, end := range cutStreamEnds {
				t.Run(tc.id+surface+end.name, func(t *testing.T) {
					r := runManagedOn(t, lists, surface, tc.id, true, end.opts, end.upstream, end.leave, nil)
					wantHold, upstream := int64(43025), int64(46)
					if surface == "/v1/messages" {
						wantHold = 689
					}
					if tc.id == "acme/token:nitro" {
						upstream = 460
						wantHold = 430248
						if surface == "/v1/messages" {
							wantHold = 6888
						}
					}
					if tc.id == "acme/request" {
						wantHold, upstream = 42000, 40000
					}
					if tc.free {
						wantHold, upstream = 0, 0
					}
					// A stream the upstream drops before its usage is charged
					// at its own hold, less the markup upstream; a hold of 0 is
					// never charged. One its client leaves keeps the estimate
					// of what was received.
					cut := end.name == "the upstream fails mid-stream"
					wantSpent, source := tc.estimated, "catalog"
					if cut && wantHold > 0 {
						wantSpent, upstream, source = wantHold, wantHold*10000/10500, "catalog_max"
					}
					if r.status != http.StatusOK || r.billing != "managed" || r.calls != 1 || r.upstreamSent != tc.id || r.spent != wantSpent || r.held != 0 {
						t.Fatalf("status=%d billing=%s calls=%d model=%s spent=%d held=%d; want one exact call, spent=%d", r.status, r.billing, r.calls, r.upstreamSent, r.spent, r.held, wantSpent)
					}
					if len(r.auths) != 1 || r.auths[0] != "Bearer fagi-openrouter-key" {
						t.Fatalf("wrong credential source: %v", r.auths)
					}
					if len(r.recs) != 1 {
						t.Fatalf("records=%v", r.recs)
					}
					if heldMicros(t, r) != wantHold || r.recs[0]["upstream_micros"] != fmt.Sprint(upstream) || r.recs[0]["cost_source"] != source {
						t.Errorf("hold/upstream/source: %v; want hold=%d upstream=%d source=%s", r.recs, wantHold, upstream, source)
					}
					if r.recs[0]["catalog_snapshot_id"] == "" {
						t.Error("settlement omits the admitted catalogue revision")
					}
					if tc.id == "acme/mixed-endpoints" {
						var prefs struct {
							Only []string `json:"only"`
						}
						if len(r.providerPrefs) != 1 || json.Unmarshal(r.providerPrefs[0], &prefs) != nil || len(prefs.Only) != 1 || prefs.Only[0] != "priced" {
							t.Errorf("endpoint pin: %s", r.providerPrefs)
						}
					}
					kind := "spend"
					if tc.free {
						kind = "void"
					}
					if r.recs[0]["kind"] != kind || r.recs[0]["usage_estimated"] != "1" || (!tc.free && r.recs[0]["cost_source"] == "unpriced") {
						t.Errorf("records=%v", r.recs)
					}
				})
			}
			if !tc.free {
				t.Run(tc.id+surface+"/reported", func(t *testing.T) {
					r := runManagedOn(t, lists, surface, tc.id, true, managedGatewayOptions{}, textThenUsage, false, nil)
					if r.calls != 1 || r.spent != 7350 || r.held != 0 || len(r.recs) != 1 || r.recs[0]["kind"] != "spend" || r.recs[0]["cost_source"] != "provider_reported" || r.recs[0]["usage_estimated"] == "1" {
						t.Fatalf("reported: %+v records=%v", r, r.recs)
					}
				})
			}
		}
	}
}

func TestManagedBillabilityListsOnlyCallableModels(t *testing.T) {
	g := newManagedTestGateway(t, managedOptIn())
	installBillabilityModels(billabilityModels(t))(t, g)
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
	if err := json.NewDecoder(resp.Body).Decode(&list); err != nil {
		t.Fatal(err)
	}
	listed := map[string]bool{}
	for _, m := range list.Data {
		listed[m.ID] = true
	}
	access := buildCatalogAccess(g.srv.handlers.catalog.Load(), managedOptIn(), 5, time.Now())
	for _, id := range refusedBillabilityIDs {
		if listed[id] {
			t.Errorf("/v1/models advertises %s", id)
		}
		for _, r := range access.Data {
			if r.ID == id && (r.Managed || r.ManagedAvailableOnOptIn) {
				t.Errorf("access advertises %s", id)
			}
		}
	}
	for _, id := range []string{"acme/token", "acme/request", "acme/listed:free", "openrouter/free", "acme/token:nitro", "acme/mixed-endpoints", "acme/endpoint:free"} {
		if !listed[id] {
			t.Errorf("/v1/models omits %s", id)
		}
	}
}

func TestManagedBillabilityOwnKeyKeepsCredits(t *testing.T) {
	lists := installBillabilityModels(billabilityModels(t))
	for _, id := range []string{"google/lyria-3-clip-preview", "acme/unknown-zero", "acme/dynamic-endpoint", "acme/zero-endpoint", "acme/unsupported"} {
		for _, prefix := range []string{"", "openrouter/"} {
			servedOnTheOrgsOwnKeyOn(t, lists, prefix+id, id)
		}
	}
}
