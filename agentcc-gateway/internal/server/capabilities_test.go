package server

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"reflect"
	"regexp"
	"sort"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/capabilities"
	"github.com/futureagi/agentcc-gateway/internal/catalog"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	creditsplugin "github.com/futureagi/agentcc-gateway/internal/plugins/credits"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

const (
	capAdminToken  = "test-cap-admin"
	capOrgKey      = "sk-test-cap-org"
	capACLKey      = "sk-test-cap-acl"
	capInternalKey = "sk-test-cap-internal"
)

// capCatalog is the managed provider's catalog: tools, structured outputs,
// images and reasoning on one model, tools only on the other.
var capCatalog = map[string]*catalog.Model{
	"anthropic/claude-sonnet-5.5": {ID: "anthropic/claude-sonnet-5.5", ContextLength: 1000000, MaxOutputTokens: 64000,
		InputModalities: []string{"text", "image"}, OutputModalities: []string{"text"},
		SupportedParameters: []string{"tools", "structured_outputs", "reasoning"}},
	"qwen/qwen3.7-flash": {ID: "qwen/qwen3.7-flash", ContextLength: 131072, MaxOutputTokens: 32000,
		InputModalities: []string{"text"}, OutputModalities: []string{"text"}, SupportedParameters: []string{"tools"}},
}

// newCapabilitiesTestGateway builds a gateway with a managed OpenRouter-style
// provider ("openrouter") and an operator provider that is not managed
// ("operator"), and org "org-m" with orgCfg. Keys: capOrgKey (org-m, limited
// to its org's providers), capACLKey (the same, ACL'd to openrouter) and
// capInternalKey (internal). Base URLs are public hosts and nothing is called.
func newCapabilitiesTestGateway(t *testing.T, orgCfg *tenant.OrgConfig) *Server {
	t.Helper()
	off := false
	cfg := config.DefaultConfig()
	cfg.Admin.Token = capAdminToken
	cfg.Auth.Enabled = true
	org := map[string]string{"org_id": "org-m"}
	cfg.Auth.Keys = []config.AuthKeyConfig{
		{Name: "org", Key: capOrgKey, KeyType: "byok", Metadata: org},
		{Name: "acl", Key: capACLKey, KeyType: "byok", Metadata: org, Providers: []string{"openrouter"}},
		{Name: "internal", Key: capInternalKey, KeyType: "internal"},
	}
	cfg.Providers["openrouter"] = config.ProviderConfig{
		BaseURL: "https://openrouter.ai/api", APIKey: "test-not-a-key", APIFormat: "openai",
		Type: "openrouter", Managed: true, AutoDiscover: &off,
	}
	cfg.Providers["operator"] = config.ProviderConfig{
		BaseURL: "https://api.openai.com", APIKey: "test-not-a-key", APIFormat: "openai",
		Models: []string{"gpt-4o"}, AutoDiscover: &off,
	}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("registry: %v", err)
	}
	store := catalog.NewStore()
	store.Put(&catalog.Snapshot{ProviderID: "openrouter", Source: "bundled", FetchedAt: time.Now(), Models: capCatalog})
	var mdb atomic.Pointer[modeldb.ModelDB]
	mdb.Store(BuildModelDB(nil, store))
	tenants := tenant.NewStore()
	tenants.Set("org-m", orgCfg)
	keyStore := auth.NewKeyStore(cfg.Auth)

	srv := New(cfg, "", registry, pipeline.NewEngine(), keyStore, nil, nil, nil, &mdb, tenants, nil)
	srv.EnableManaged(ManagedSettings{Catalog: store, Ledger: creditsplugin.NewOrgLedger(nil), MarkupPercent: 5})
	srv.ready.Store(true)
	return srv
}

// orgWithBYOK has its own Anthropic key (a provider the gateway does not
// configure) and its own key for the operator's provider ID.
func orgWithBYOK(managed bool) *tenant.OrgConfig {
	return &tenant.OrgConfig{
		ManagedModels: &tenant.ManagedModelsConfig{Enabled: managed},
		Providers: map[string]*tenant.ProviderConfig{
			"anthropic": {APIKey: "test-not-a-key", Enabled: true},
			"operator":  {APIKey: "test-not-a-key", Enabled: true},
			"disabled":  {APIKey: "test-not-a-key", Enabled: false},
		},
	}
}

func capGet(t *testing.T, srv *Server, path, bearer string) *httptest.ResponseRecorder {
	t.Helper()
	req := httptest.NewRequest("GET", path, nil)
	if bearer != "" {
		req.Header.Set("Authorization", "Bearer "+bearer)
	}
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)
	return w
}

func capMatrix(t *testing.T, srv *Server, path, bearer string) *capabilities.Matrix {
	t.Helper()
	w := capGet(t, srv, path, bearer)
	if w.Code != http.StatusOK {
		t.Fatalf("GET %s: %d %s", path, w.Code, w.Body)
	}
	var m capabilities.Matrix
	if err := json.Unmarshal(w.Body.Bytes(), &m); err != nil {
		t.Fatalf("GET %s: %v", path, err)
	}
	return &m
}

func capCellProviders(t *testing.T, m *capabilities.Matrix, endpoint string) []string {
	t.Helper()
	for _, row := range m.Endpoints {
		if row.Endpoint == endpoint {
			var ids []string
			for id := range row.Providers {
				ids = append(ids, id)
			}
			sort.Strings(ids)
			return ids
		}
	}
	t.Fatalf("no %s row", endpoint)
	return nil
}

func capCell(t *testing.T, m *capabilities.Matrix, endpoint, provider string) capabilities.Cell {
	t.Helper()
	c, ok := m.Lookup(endpoint, provider)
	if !ok {
		t.Fatalf("no %s cell for %s", endpoint, provider)
	}
	return c
}

func TestCapabilitiesKeyScoped(t *testing.T) {
	// An org that has not opted in reaches only its own providers.
	srv := newCapabilitiesTestGateway(t, orgWithBYOK(false))
	m := capMatrix(t, srv, "/v1/capabilities", capOrgKey)
	if got := capCellProviders(t, m, capabilities.EndpointChat); !reflect.DeepEqual(got, []string{"anthropic", "operator"}) {
		t.Fatalf("not opted in: providers %v, want the org's own anthropic and operator", got)
	}
	// The org's own Anthropic is described from its api_format, not built.
	if c := capCell(t, m, capabilities.EndpointMessages, "anthropic"); c.Status != capabilities.StatusSupported || c.CredentialType != "byok" {
		t.Errorf("org anthropic messages = %+v", c)
	}
	if c := capCell(t, m, capabilities.EndpointEmbeddings, "anthropic"); c.Status != capabilities.StatusUnsupported {
		t.Errorf("org anthropic embeddings = %s, want unsupported", c.Status)
	}
	// The org's key for the operator's ID is BYOK, on the operator's adapter.
	if c := capCell(t, m, capabilities.EndpointImages, "operator"); c.Status != capabilities.StatusSupported || c.CredentialType != "byok" || c.ManagedAllowed {
		t.Errorf("org operator images = %+v", c)
	}
	// Admin-token and global-registry endpoints are not this key's.
	for _, ep := range []string{capabilities.EndpointBatch, capabilities.EndpointAsync, capabilities.EndpointScheduled} {
		if c := capCell(t, m, ep, "operator"); c.Status != capabilities.StatusUnsupported {
			t.Errorf("%s for an org-only key = %s, want unsupported", ep, c.Status)
		}
	}

	// Opted in: the managed provider joins; the operator's FutureAGI
	// credentials never do.
	srv = newCapabilitiesTestGateway(t, &tenant.OrgConfig{ManagedModels: &tenant.ManagedModelsConfig{Enabled: true}})
	m = capMatrix(t, srv, "/v1/capabilities", capOrgKey)
	if got := capCellProviders(t, m, capabilities.EndpointChat); !reflect.DeepEqual(got, []string{"openrouter"}) {
		t.Fatalf("opted in: providers %v, want only the managed openrouter", got)
	}
	if c := capCell(t, m, capabilities.EndpointChat, "openrouter"); c.CredentialType != "managed" || c.ManagedResaleTerms != "" {
		t.Errorf("openrouter chat = %+v", c)
	}
	var tier string
	for _, row := range m.Endpoints {
		if row.Endpoint == capabilities.EndpointChat {
			tier = row.LaunchTier
		}
	}
	if tier != capabilities.TierLaunch {
		t.Errorf("chat.completions launch_tier = %s", tier)
	}

	// The key's provider ACL applies.
	srv = newCapabilitiesTestGateway(t, orgWithBYOK(true))
	if got := capCellProviders(t, capMatrix(t, srv, "/v1/capabilities", capACLKey), capabilities.EndpointChat); !reflect.DeepEqual(got, []string{"openrouter"}) {
		t.Errorf("ACL'd key: providers %v, want openrouter only", got)
	}
	// An internal key reaches every global provider, and not the org's own.
	m = capMatrix(t, srv, "/v1/capabilities", capInternalKey)
	if got := capCellProviders(t, m, capabilities.EndpointChat); !reflect.DeepEqual(got, []string{"openrouter", "operator"}) {
		t.Errorf("internal key: providers %v", got)
	}
	if c := capCell(t, m, capabilities.EndpointAsync, "operator"); c.Status != capabilities.StatusSupported {
		t.Errorf("async for an internal key = %s", c.Status)
	}
	if c := capCell(t, m, capabilities.EndpointBatch, "operator"); c.Status != capabilities.StatusUnsupported {
		t.Errorf("batch for a key = %s, want unsupported (admin route)", c.Status)
	}

	// The admin view lists every global provider; the token is required.
	if w := capGet(t, srv, "/-/capabilities", ""); w.Code != http.StatusUnauthorized {
		t.Errorf("admin view without the token: %d", w.Code)
	}
	if w := capGet(t, srv, "/-/capabilities", capOrgKey); w.Code != http.StatusUnauthorized {
		t.Errorf("admin view with a virtual key: %d", w.Code)
	}
	m = capMatrix(t, srv, "/-/capabilities", capAdminToken)
	if got := capCellProviders(t, m, capabilities.EndpointChat); !reflect.DeepEqual(got, []string{"openrouter", "operator"}) {
		t.Errorf("admin: providers %v", got)
	}
	if c := capCell(t, m, capabilities.EndpointBatch, "operator"); c.Status != capabilities.StatusSupported {
		t.Errorf("batch for the admin = %s", c.Status)
	}
	// No key, no matrix.
	if w := capGet(t, srv, "/v1/capabilities", ""); w.Code != http.StatusUnauthorized {
		t.Errorf("no key: %d", w.Code)
	}
}

func TestCapabilitiesManagedAllowedReadsInjectedPriced(t *testing.T) {
	srv := newCapabilitiesTestGateway(t, orgWithBYOK(true))
	srv.Handlers().SetInjectedDeps(InjectedDeps{Priced: func(string, string) bool { return true }})
	m := capMatrix(t, srv, "/-/capabilities", capAdminToken)
	// Managed traffic is served on chat only.
	if !capCell(t, m, capabilities.EndpointChat, "openrouter").ManagedAllowed {
		t.Error("priced managed chat must be managed_allowed")
	}
	for _, ep := range []string{capabilities.EndpointMessages, capabilities.EndpointResponses, capabilities.EndpointEmbeddings, capabilities.EndpointImages} {
		if capCell(t, m, ep, "openrouter").ManagedAllowed {
			t.Errorf("%s: managed_allowed while managedEndpoint refuses managed traffic there", ep)
		}
	}
	if capCell(t, m, capabilities.EndpointChat, "operator").ManagedAllowed {
		t.Error("a provider that is not managed is never managed_allowed")
	}
	// Swapped after the server was built: the next request sees it.
	srv.Handlers().SetInjectedDeps(InjectedDeps{Priced: func(string, string) bool { return false }})
	if capCell(t, capMatrix(t, srv, "/-/capabilities", capAdminToken), capabilities.EndpointChat, "openrouter").ManagedAllowed {
		t.Error("managed_allowed did not follow the injected priced function")
	}
}

func capModelView(t *testing.T, srv *Server, path, bearer string) *capabilities.ModelView {
	t.Helper()
	w := capGet(t, srv, path, bearer)
	if w.Code != http.StatusOK {
		t.Fatalf("GET %s: %d %s", path, w.Code, w.Body)
	}
	var v capabilities.ModelView
	if err := json.Unmarshal(w.Body.Bytes(), &v); err != nil {
		t.Fatal(err)
	}
	return &v
}

func TestCapabilitiesModelFlags(t *testing.T) {
	srv := newCapabilitiesTestGateway(t, &tenant.OrgConfig{ManagedModels: &tenant.ManagedModelsConfig{Enabled: true}})
	srv.Handlers().SetInjectedDeps(InjectedDeps{Priced: func(endpoint, model string) bool {
		return model == "anthropic/claude-sonnet-5.5"
	}})

	v := capModelView(t, srv, "/v1/capabilities?model=anthropic/claude-sonnet-5.5", capOrgKey)
	c, ok := v.Providers["openrouter"]
	if !ok || len(v.Providers) != 1 || v.Model != "anthropic/claude-sonnet-5.5" {
		t.Fatalf("model view = %+v", v)
	}
	if f := c.Flags; !f.Tools || !f.JSONSchema || !f.Vision || !f.Reasoning || f.AudioInput {
		t.Errorf("flags = %+v", f)
	}
	if c.ContextLength != 1000000 || c.MaxOutputTokens != 64000 || c.Source != "catalog" ||
		!reflect.DeepEqual(c.Modalities.Input, []string{"text", "image"}) {
		t.Errorf("model cell = %+v", c)
	}
	if e := c.Endpoints[capabilities.EndpointChat]; e.Status != capabilities.StatusSupported || !e.ManagedAllowed {
		t.Errorf("chat for the model = %+v", e)
	}
	// A model with tools only.
	c = capModelView(t, srv, "/v1/capabilities?model=qwen/qwen3.7-flash", capOrgKey).Providers["openrouter"]
	if f := c.Flags; !f.Tools || f.JSONSchema || f.Vision || f.Reasoning {
		t.Errorf("qwen flags = %+v", f)
	}
	if c.Endpoints[capabilities.EndpointChat].ManagedAllowed {
		t.Error("an unpriced model reported managed_allowed=true")
	}
	// The admin view answers per model too, from the injected priced.
	if !capModelView(t, srv, "/-/capabilities?model=openrouter/anthropic/claude-sonnet-5.5", capAdminToken).
		Providers["openrouter"].Endpoints[capabilities.EndpointChat].ManagedAllowed {
		t.Error("admin model view: managed_allowed")
	}
	// The operator's model exists, but not for this key.
	if w := capGet(t, srv, "/v1/capabilities?model=gpt-4o", capOrgKey); w.Code != http.StatusNotFound || !strings.Contains(w.Body.String(), "model_not_found") {
		t.Errorf("model the key cannot reach: %d %s", w.Code, w.Body)
	}
	if c, ok := capModelView(t, srv, "/-/capabilities?model=gpt-4o", capAdminToken).Providers["operator"]; !ok || c.CredentialType != "byok" {
		t.Errorf("admin view of the operator's model = %+v", c)
	}
	if w := capGet(t, srv, "/v1/capabilities?model=no-such-model", capOrgKey); w.Code != http.StatusNotFound {
		t.Errorf("unknown model: %d", w.Code)
	}
}

// --- capabilities-v1 golden -------------------------------------------------

var capExamplesDir = filepath.Join("..", "..", "..", "api_contracts", "gateway", "examples")

func capReadExample(t *testing.T, name string) any {
	t.Helper()
	b, err := os.ReadFile(filepath.Join(capExamplesDir, name))
	if err != nil {
		t.Fatalf("read %s: %v", name, err)
	}
	var v any
	if err := json.Unmarshal(b, &v); err != nil {
		t.Fatalf("parse %s: %v", name, err)
	}
	return v
}

// capSchemaCheck is a JSON Schema checker for the keywords capabilities-v1
// uses. Any other keyword fails, so a schema change cannot pass unchecked.
type capSchemaCheck struct {
	root map[string]any
	errs []string
}

var capSchemaNotes = map[string]bool{"$schema": true, "$id": true, "title": true, "description": true, "$defs": true}

var capRFC3339 = regexp.MustCompile(`^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$`)

func validateCapabilities(t *testing.T, doc any) []string {
	t.Helper()
	return validateCapExample(t, "capabilities-v1.schema.json", doc)
}

// validateCapabilitiesModel checks a ?model= response against
// capabilities-model-v1.
func validateCapabilitiesModel(t *testing.T, doc any) []string {
	t.Helper()
	return validateCapExample(t, "capabilities-model-v1.schema.json", doc)
}

func validateCapExample(t *testing.T, schema string, doc any) []string {
	t.Helper()
	root := capReadExample(t, schema).(map[string]any)
	c := &capSchemaCheck{root: root}
	c.check(root, doc, "$")
	return c.errs
}

func (c *capSchemaCheck) fail(path, format string, args ...any) {
	c.errs = append(c.errs, path+": "+fmt.Sprintf(format, args...))
}

func (c *capSchemaCheck) check(schema map[string]any, v any, path string) {
	if ref, ok := schema["$ref"].(string); ok {
		def := c.root["$defs"].(map[string]any)[strings.TrimPrefix(ref, "#/$defs/")]
		c.check(def.(map[string]any), v, path)
		return
	}
	for k, sv := range schema {
		switch k {
		case "type":
			if !capTypeMatches(sv, v) {
				c.fail(path, "type %v, got %T", sv, v)
				return
			}
		case "enum":
			found := false
			for _, e := range sv.([]any) {
				found = found || reflect.DeepEqual(e, v)
			}
			if !found {
				c.fail(path, "%v not in %v", v, sv)
			}
		case "minLength":
			if s, ok := v.(string); ok && len(s) < int(sv.(float64)) {
				c.fail(path, "shorter than %v", sv)
			}
		case "format":
			if s, ok := v.(string); ok && sv == "date-time" && !capRFC3339.MatchString(s) {
				c.fail(path, "%q is not a date-time", s)
			}
		case "required":
			obj, _ := v.(map[string]any)
			for _, r := range sv.([]any) {
				if _, ok := obj[r.(string)]; !ok {
					c.fail(path, "missing %s", r)
				}
			}
		case "properties":
			obj, _ := v.(map[string]any)
			for name, ps := range sv.(map[string]any) {
				if fv, ok := obj[name]; ok {
					c.check(ps.(map[string]any), fv, path+"."+name)
				}
			}
		case "additionalProperties":
			obj, _ := v.(map[string]any)
			props, _ := schema["properties"].(map[string]any)
			for name, fv := range obj {
				if _, declared := props[name]; declared {
					continue
				}
				switch ap := sv.(type) {
				case bool:
					if !ap {
						c.fail(path, "undeclared property %s", name)
					}
				case map[string]any:
					c.check(ap, fv, path+"."+name)
				}
			}
		case "items":
			arr, _ := v.([]any)
			for i, item := range arr {
				c.check(sv.(map[string]any), item, fmt.Sprintf("%s[%d]", path, i))
			}
		case "anyOf":
			ok := false
			for _, alt := range sv.([]any) {
				sub := &capSchemaCheck{root: c.root}
				sub.check(alt.(map[string]any), v, path)
				ok = ok || len(sub.errs) == 0
			}
			if !ok {
				c.fail(path, "matches no anyOf branch")
			}
		default:
			if !capSchemaNotes[k] {
				c.fail(path, "unsupported schema keyword %q", k)
			}
		}
	}
}

func capTypeMatches(want, v any) bool {
	switch want {
	case "object":
		_, ok := v.(map[string]any)
		return ok
	case "array":
		_, ok := v.([]any)
		return ok
	case "string":
		_, ok := v.(string)
		return ok
	case "boolean":
		_, ok := v.(bool)
		return ok
	case "null":
		return v == nil
	case "integer", "number":
		_, ok := v.(float64)
		return ok
	}
	return false
}

func capDecode(t *testing.T, w *httptest.ResponseRecorder) any {
	t.Helper()
	var v any
	if err := json.Unmarshal(w.Body.Bytes(), &v); err != nil {
		t.Fatalf("decode: %v", err)
	}
	return v
}

func TestCapabilitiesGolden(t *testing.T) {
	// The checker accepts the golden example and rejects what the schema forbids.
	golden := capReadExample(t, "capabilities-v1.json")
	if errs := validateCapabilities(t, golden); len(errs) > 0 {
		t.Fatalf("golden example fails its schema: %v", errs)
	}
	for name, mutate := range map[string]func(cell map[string]any){
		"qualified null":   func(c map[string]any) { c["qualified"] = nil },
		"unknown field":    func(c map[string]any) { c["provider"] = "openrouter" },
		"bad resale terms": func(c map[string]any) { c["managed_resale_terms"] = "maybe" },
		"missing features": func(c map[string]any) { delete(c, "features") },
		// Only the schema's status and source vocabulary.
		"status available": func(c map[string]any) { c["status"] = "available" },
		"source adapter":   func(c map[string]any) { c["source"] = "adapter" },
	} {
		bad := capReadExample(t, "capabilities-v1.json")
		cell := bad.(map[string]any)["endpoints"].([]any)[0].(map[string]any)["providers"].(map[string]any)["openrouter"].(map[string]any)
		mutate(cell)
		if errs := validateCapabilities(t, bad); len(errs) == 0 {
			t.Errorf("%s passed the schema", name)
		}
	}

	pathID := capReadExample(t, "capabilities-v1.json")
	pathID.(map[string]any)["endpoints"].([]any)[0].(map[string]any)["endpoint"] = "/v1/chat/completions"
	if errs := validateCapabilities(t, pathID); len(errs) == 0 {
		t.Error("a path endpoint id passed the schema")
	}
	noTerms := capReadExample(t, "capabilities-v1.json")
	delete(noTerms.(map[string]any)["endpoints"].([]any)[0].(map[string]any)["providers"].(map[string]any)["openrouter"].(map[string]any), "managed_resale_terms")
	if errs := validateCapabilities(t, noTerms); len(errs) > 0 {
		t.Errorf("managed_resale_terms is optional: %v", errs)
	}
	if errs := validateCapabilitiesModel(t, capReadExample(t, "capabilities-model-v1.json")); len(errs) > 0 {
		t.Fatalf("capabilities-model-v1 golden fails its schema: %v", errs)
	}

	// A qualified cell carries qualified_at; the rest carry null.
	qfile := filepath.Join(t.TempDir(), "qualification.json")
	if err := os.WriteFile(qfile, []byte(`{"mode":"live","ran_at":"2026-10-12T18:20:00Z","results":[`+
		`{"provider":"openrouter","endpoint":"chat.completions","feature":"stream","status":"pass"}]}`), 0o600); err != nil {
		t.Fatal(err)
	}
	srv := newCapabilitiesTestGateway(t, orgWithBYOK(true))
	routes := newCapabilityRoutes(srv)
	routes.qualificationFile = qfile
	adminReq := func() *httptest.ResponseRecorder {
		req := httptest.NewRequest("GET", "/-/capabilities", nil)
		req.Header.Set("Authorization", "Bearer "+capAdminToken)
		w := httptest.NewRecorder()
		routes.handleAdmin(w, req)
		return w
	}

	for name, w := range map[string]*httptest.ResponseRecorder{
		"admin":         adminReq(),
		"admin route":   capGet(t, srv, "/-/capabilities", capAdminToken),
		"org key":       capGet(t, srv, "/v1/capabilities", capOrgKey),
		"internal key":  capGet(t, srv, "/v1/capabilities", capInternalKey),
		"acl'd org key": capGet(t, srv, "/v1/capabilities", capACLKey),
	} {
		if w.Code != http.StatusOK {
			t.Fatalf("%s: %d %s", name, w.Code, w.Body)
		}
		doc := capDecode(t, w)
		if errs := validateCapabilities(t, doc); len(errs) > 0 {
			t.Errorf("%s fails capabilities-v1.schema.json:\n%s", name, strings.Join(errs, "\n"))
		}
		// The published endpoint set, in order.
		var ids []string
		for _, row := range doc.(map[string]any)["endpoints"].([]any) {
			ids = append(ids, row.(map[string]any)["endpoint"].(string))
		}
		want := "chat.completions,responses,messages,embeddings,models," +
			"completions,images,audio.speech,audio.transcriptions,rerank,ocr,search,genai-native,batch," +
			"assistants,threads,vector_stores,files,videos,realtime,scheduled,async"
		if strings.Join(ids, ",") != want {
			t.Errorf("%s endpoints = %v", name, ids)
		}
	}

	doc := capDecode(t, adminReq())
	var chat map[string]any
	for _, row := range doc.(map[string]any)["endpoints"].([]any) {
		if row.(map[string]any)["endpoint"] == "chat.completions" {
			chat = row.(map[string]any)["providers"].(map[string]any)
		}
	}
	or := chat["openrouter"].(map[string]any)
	if or["qualified"] != true || or["qualified_at"] != "2026-10-12T18:20:00Z" || or["source"] != "qualified" {
		t.Errorf("qualified cell = %v", or)
	}
	op := chat["operator"].(map[string]any)
	if v, present := op["qualified_at"]; !present || v != nil || op["qualified"] != false {
		t.Errorf("unqualified cell = %v, want qualified false and qualified_at null", op)
	}
}
