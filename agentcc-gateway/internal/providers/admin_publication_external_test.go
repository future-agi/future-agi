package providers_test

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"reflect"
	"strings"
	"sync/atomic"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/routebinding"
	"github.com/futureagi/agentcc-gateway/internal/server"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

type inertAdminProvider struct {
	id        string
	closes    atomic.Int32
	forbidden atomic.Int32
}

var _ providers.Provider = (*inertAdminProvider)(nil)

func (p *inertAdminProvider) ID() string   { return p.id }
func (p *inertAdminProvider) Close() error { p.closes.Add(1); return nil }
func (p *inertAdminProvider) ChatCompletion(context.Context, *models.ChatCompletionRequest) (*models.ChatCompletionResponse, error) {
	p.forbidden.Add(1)
	return nil, errors.New("inert provider cannot dispatch")
}
func (p *inertAdminProvider) StreamChatCompletion(context.Context, *models.ChatCompletionRequest) (<-chan models.StreamChunk, <-chan error) {
	p.forbidden.Add(1)
	chunks, errs := make(chan models.StreamChunk), make(chan error, 1)
	close(chunks)
	errs <- errors.New("inert provider cannot dispatch")
	close(errs)
	return chunks, errs
}
func (p *inertAdminProvider) ListModels(context.Context) ([]models.ModelObject, error) {
	p.forbidden.Add(1)
	return nil, errors.New("inert provider cannot list")
}

type adminCacheFixture struct {
	s         *tenant.Store
	g         *routebinding.Gate
	cache     *providers.OrgProviderCache
	h         *server.OrgConfigHandlers
	entries   map[string]map[string]*inertAdminProvider
	callbacks []string
}

func newAdminCacheFixture(t *testing.T) *adminCacheFixture {
	t.Helper()
	g, err := routebinding.NewGate()
	if err != nil {
		t.Fatal("gate setup failed")
	}
	s := tenant.NewStore()
	if s.BindNativeLifecycle(g) != nil {
		t.Fatal("fresh owner binding failed")
	}
	for _, org := range []string{"orgA", "orgB"} {
		cfg := &tenant.OrgConfig{ModelMap: map[string]string{"synthetic": org}}
		if s.Set(org, cfg) != nil {
			t.Fatal("seed publication failed")
		}
	}
	f := &adminCacheFixture{s: s, g: g, entries: make(map[string]map[string]*inertAdminProvider)}
	seed := make(map[string]map[string]providers.Provider)
	for _, org := range []string{"orgA", "orgB"} {
		f.entries[org] = make(map[string]*inertAdminProvider)
		seed[org] = make(map[string]providers.Provider)
		for _, id := range []string{"first", "second"} {
			p := &inertAdminProvider{id: id}
			f.entries[org][id] = p
			seed[org][id] = p
		}
	}
	f.cache = providers.NewInertOrgProviderCacheForTest(seed)
	f.h = server.NewOrgConfigHandlers(s, "synthetic", f.cache)
	f.h.SetOnConfigChange(func(org string) { f.callbacks = append(f.callbacks, org) })
	return f
}

func (f *adminCacheFixture) invoke(kind string) *httptest.ResponseRecorder {
	method, path, body := http.MethodPut, "/-/orgs/orgA/config?org_id=orgA", "{}"
	if kind == "delete" {
		method = http.MethodDelete
	}
	if kind == "bulk" {
		method, path, body = http.MethodPost, "/-/orgs/configs/bulk", `{"orgA":{},"orgB":{}}`
	}
	r := httptest.NewRequest(method, path, strings.NewReader(body))
	r.Header.Set("Authorization", "Bearer synthetic")
	w := httptest.NewRecorder()
	switch kind {
	case "set":
		f.h.SetOrgConfig(w, r)
	case "delete":
		f.h.DeleteOrgConfig(w, r)
	case "bulk":
		f.h.BulkLoadOrgConfigs(w, r)
	}
	return w
}

func (f *adminCacheFixture) assertCache(t *testing.T, evicted map[string]bool) {
	t.Helper()
	wantCount := 4 - 2*len(evicted)
	if f.cache.Count() != wantCount {
		t.Fatal("cache entry count changed incorrectly")
	}
	// Identity and Close assertions precede every warmed GetOrCreate, so even a
	// hypothetical rebuild cannot conceal accidental eviction.
	for org, entries := range f.entries {
		for id, p := range entries {
			wantCloses := int32(0)
			if evicted[org] {
				wantCloses = 1
				if f.cache.Get(org, id) != nil {
					t.Fatal("successful publication retained evicted entry")
				}
			} else if f.cache.Get(org, id) != p {
				t.Fatal("warm provider identity was not retained")
			}
			if p.closes.Load() != wantCloses || p.forbidden.Load() != 0 {
				t.Fatal("unexpected provider close or dispatch")
			}
		}
	}
	for org, entries := range f.entries {
		for id, p := range entries {
			got, err := f.cache.GetOrCreate(org, id, "")
			if evicted[org] {
				if err == nil || got != nil {
					t.Fatal("nil-base cache miss rebuilt an entry")
				}
			} else if err != nil || got != p {
				t.Fatal("warmed lookup did not retain exact provider")
			}
			if p.forbidden.Load() != 0 {
				t.Fatal("cache lookup dispatched")
			}
		}
	}
	if f.cache.Count() != wantCount {
		t.Fatal("lookup changed cache membership")
	}
}

func TestAdminFailedPublicationRetainsActualProviderCache(t *testing.T) {
	for _, kind := range []string{"set", "delete", "bulk"} {
		t.Run(kind, func(t *testing.T) {
			f := newAdminCacheFixture(t)
			before, gen := f.s.GetAll(), f.s.Generation()
			observed, changed := 0, 0
			f.s.ObserveChanges(func(string, *tenant.OrgConfig) { observed++ })
			f.s.SetOnChange(func(string) { changed++ })
			if f.g.Close() != nil {
				t.Fatal("gate close failed")
			}
			w := f.invoke(kind)
			var response struct {
				Error struct {
					Code string `json:"code"`
				} `json:"error"`
			}
			if json.Unmarshal(w.Body.Bytes(), &response) != nil || w.Code != http.StatusServiceUnavailable || response.Error.Code != "tenant_config_publication_refused" {
				t.Fatal("failed publication was not refused")
			}
			if len(f.callbacks) != 0 || observed != 0 || changed != 0 || f.s.Generation() != gen || f.s.Count() != 2 || !reflect.DeepEqual(before, f.s.GetAll()) {
				t.Fatal("refused producer changed data or notified")
			}
			f.assertCache(t, nil)
		})
	}
}

func TestAdminSuccessfulPublicationEvictsActualProviderCache(t *testing.T) {
	for _, kind := range []string{"set", "delete", "bulk"} {
		t.Run(kind, func(t *testing.T) {
			f := newAdminCacheFixture(t)
			gen := f.s.Generation()
			w := f.invoke(kind)
			var response struct {
				Status string `json:"status"`
			}
			if json.Unmarshal(w.Body.Bytes(), &response) != nil || w.Code != http.StatusOK || response.Status != "ok" || f.s.Generation() != gen+1 {
				t.Fatal("fresh publication did not acknowledge success")
			}
			want := map[string]bool{"orgA": true}
			if kind == "bulk" {
				want["orgB"] = true
			}
			seen := make(map[string]int)
			for _, org := range f.callbacks {
				seen[org]++
			}
			if len(seen) != len(want) || len(f.callbacks) != len(want) {
				t.Fatal("successful callbacks missing or duplicated")
			}
			for org := range want {
				if seen[org] != 1 {
					t.Fatal("successful callback selected wrong org")
				}
			}
			if kind == "delete" {
				if f.s.Get("orgA") != nil || f.s.Count() != 1 {
					t.Fatal("delete did not publish")
				}
			} else if f.s.Get("orgA") == nil || len(f.s.Get("orgA").ModelMap) != 0 || f.s.Count() != 2 {
				t.Fatal("set or bulk did not replace prior config")
			}
			if kind != "bulk" && f.s.Get("orgB").ModelMap["synthetic"] != "orgB" {
				t.Fatal("single-org mutation changed other org")
			}
			f.assertCache(t, want)
		})
	}
}
