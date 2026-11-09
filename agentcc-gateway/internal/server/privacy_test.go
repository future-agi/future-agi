package server

import (
	"fmt"
	"net/http"
	"net/http/httptest"
	"reflect"
	"strings"
	"sync"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	"github.com/futureagi/agentcc-gateway/internal/privacy"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

const privacyOrg = "org-privacy"

// Test keys, not real credentials.
const (
	privacyPlainKey = "sk-privacy-plain"
	privacyNoneKey  = "sk-privacy-none"
	privacyMetaKey  = "sk-privacy-metadata"
)

// privacyTest is a real server whose org and keys carry content modes; it
// records the content mode each request resolved, as step 6 sees it.
type privacyTest struct {
	srv     *Server
	tenants *tenant.Store
	mu      sync.Mutex
	seen    []models.Privacy
}

func newPrivacyTest(t *testing.T, org *tenant.OrgConfig) *privacyTest {
	t.Helper()
	upstream := startHookUpstream(t, 0)
	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = true
	key := func(name, raw string, meta map[string]string) config.AuthKeyConfig {
		m := map[string]string{"org_id": privacyOrg}
		for k, v := range meta {
			m[k] = v
		}
		return config.AuthKeyConfig{Name: name, Key: raw, Owner: "t", KeyType: "byok", Metadata: m}
	}
	cfg.Auth.Keys = []config.AuthKeyConfig{
		key("plain", privacyPlainKey, nil),
		key("none", privacyNoneKey, map[string]string{privacy.KeyLogContentMetadata: "none"}),
		key("metadata", privacyMetaKey, map[string]string{privacy.KeyLogContentMetadata: "metadata"}),
	}
	cfg.Providers["openai"] = config.ProviderConfig{BaseURL: upstream.URL, APIKey: "dummy", APIFormat: "openai", Models: []string{"gpt-4o"}}
	cfg.Routing.ProviderLock = config.ProviderLockConfig{Enabled: true}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("creating registry: %v", err)
	}
	keyStore := auth.NewKeyStore(cfg.Auth)
	tenants := tenant.NewStore()
	if org != nil {
		tenants.Set(privacyOrg, org)
	}
	srv := New(cfg, "", registry, pipeline.NewEngine(authplugin.New(keyStore, true)), keyStore, nil, nil, nil, testModelDBPtr(), tenants, nil)
	srv.ready.Store(true)
	pt := &privacyTest{srv: srv, tenants: tenants}
	srv.handlers.hookObserver = func(hook, _ string, rc *models.RequestContext) {
		if hook == hookApplyRoutingOptions { // step 6, right after resolveContentMode
			pt.mu.Lock()
			pt.seen = append(pt.seen, rc.Privacy)
			pt.mu.Unlock()
		}
	}
	return pt
}

// chat sends one chat request with key and headers and returns the content
// mode the request resolved.
func (pt *privacyTest) chat(t *testing.T, key string, headers ...string) models.Privacy {
	t.Helper()
	req := httptest.NewRequest(http.MethodPost, "/v1/chat/completions",
		strings.NewReader(`{"model":"gpt-4o","messages":[{"role":"user","content":"hi"}]}`))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+key)
	for i := 0; i+1 < len(headers); i += 2 {
		req.Header.Set(headers[i], headers[i+1])
	}
	w := httptest.NewRecorder()
	pt.srv.httpServer.Handler.ServeHTTP(w, req)
	if w.Code != http.StatusOK {
		t.Fatalf("status %d: %s", w.Code, w.Body.String())
	}
	pt.mu.Lock()
	defer pt.mu.Unlock()
	if len(pt.seen) == 0 {
		t.Fatal("the request never reached step 6")
	}
	got := pt.seen[len(pt.seen)-1]
	pt.seen = nil
	return got
}

// privacyOrgConfig is the test org's config: its own key for the upstream,
// and privacy p.
func privacyOrgConfig(p *tenant.PrivacyConfig) *tenant.OrgConfig {
	return &tenant.OrgConfig{
		Providers: map[string]*tenant.ProviderConfig{
			"openai": {Enabled: true, APIKey: "org-openai-key", Models: []string{"gpt-4o"}},
		},
		Privacy: p,
	}
}

func orgWithContentMode(mode string) *tenant.OrgConfig {
	return privacyOrgConfig(&tenant.PrivacyConfig{ContentMode: mode})
}

func wantPrivacy(t *testing.T, label string, got models.Privacy, mode models.ContentMode, source string) {
	t.Helper()
	if got.ContentMode != mode || got.Source != source {
		t.Errorf("%s: rc.Privacy = %+v, want %s from %s", label, got, mode, source)
	}
}

// The hook resolves every source through privacy.ResolveContentMode: the
// effective org config, the presented key's log_content, the request header
// and the global default.
func TestContentModePrecedence(t *testing.T) {
	pt := newPrivacyTest(t, orgWithContentMode("metadata"))
	wantPrivacy(t, "org metadata, plain key", pt.chat(t, privacyPlainKey), models.ContentModeMetadata, models.PrivacySourceOrg)
	wantPrivacy(t, "org metadata, key none", pt.chat(t, privacyNoneKey), models.ContentModeNone, models.PrivacySourceKey)
	wantPrivacy(t, "org metadata, header none", pt.chat(t, privacyPlainKey, "x-agentcc-log-content", "none"),
		models.ContentModeNone, models.PrivacySourceRequest)

	pt.tenants.Set(privacyOrg, orgWithContentMode(""))
	wantPrivacy(t, "nothing set", pt.chat(t, privacyPlainKey), models.ContentModeFull, models.PrivacySourceDefault)

	restore, err := privacy.SetDefaultContentMode("metadata")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(restore)
	wantPrivacy(t, "global default metadata", pt.chat(t, privacyPlainKey), models.ContentModeMetadata, models.PrivacySourceDefault)
	wantPrivacy(t, "global default metadata, key none", pt.chat(t, privacyNoneKey), models.ContentModeNone, models.PrivacySourceKey)
}

// A key's log_content wins over an org that keeps everything.
func TestKeyModeBeatsOrgFull(t *testing.T) {
	pt := newPrivacyTest(t, orgWithContentMode("full"))
	wantPrivacy(t, "key none", pt.chat(t, privacyNoneKey), models.ContentModeNone, models.PrivacySourceKey)
	wantPrivacy(t, "key metadata", pt.chat(t, privacyMetaKey), models.ContentModeMetadata, models.PrivacySourceKey)
	wantPrivacy(t, "another key of the org", pt.chat(t, privacyPlainKey), models.ContentModeFull, models.PrivacySourceDefault)
}

// The header tightens through the handler, and never loosens an org's or a
// key's mode.
func TestRequestHeaderOnlyTightens(t *testing.T) {
	pt := newPrivacyTest(t, orgWithContentMode("metadata"))
	wantPrivacy(t, "header full under org metadata", pt.chat(t, privacyPlainKey, "x-agentcc-log-content", "full"),
		models.ContentModeMetadata, models.PrivacySourceOrg)
	wantPrivacy(t, "header metadata under key none", pt.chat(t, privacyNoneKey, "x-agentcc-log-content", "metadata"),
		models.ContentModeNone, models.PrivacySourceKey)
	wantPrivacy(t, "misspelt header", pt.chat(t, privacyPlainKey, "x-agentcc-log-content", "nonee"),
		models.ContentModeNone, models.PrivacySourceRequest)
}

// Step 5 takes the key's log_content from key_log_content when the early key
// peek copied it there, without looking the key up again: a value the peek
// left wins even when it is empty, so the peek is the only key read on the
// request path once it copies the field.
func TestKeyLogContentReadsThePeek(t *testing.T) {
	pt := newPrivacyTest(t, orgWithContentMode("full"))
	var peeked *string
	var leftAfter []string // key_log_content as step 6 sees it
	srv := pt.srv.handlers
	observe := srv.hookObserver
	srv.hookObserver = func(hook, step string, rc *models.RequestContext) {
		if hook == hookResolveOrgConfig && peeked != nil { // step 2, before step 5
			rc.Metadata[privacy.KeyLogContentRequestMetadata] = *peeked
		}
		if hook == hookApplyRoutingOptions {
			v, ok := rc.Metadata[privacy.KeyLogContentRequestMetadata]
			leftAfter = append(leftAfter, fmt.Sprintf("%v:%s", ok, v))
		}
		observe(hook, step, rc)
	}
	peek := func(v string) { peeked = &v }

	peek("metadata")
	wantPrivacy(t, "peeked metadata, plain key", pt.chat(t, privacyPlainKey), models.ContentModeMetadata, models.PrivacySourceKey)
	peek("")
	wantPrivacy(t, "peeked empty, key none", pt.chat(t, privacyNoneKey), models.ContentModeFull, models.PrivacySourceDefault)
	// A set copy stays for the log; an empty one is removed, not logged.
	if want := []string{"true:metadata", "false:"}; !reflect.DeepEqual(leftAfter, want) {
		t.Errorf("key_log_content after step 5 = %v, want %v", leftAfter, want)
	}
	peeked = nil
	wantPrivacy(t, "no peek, key none", pt.chat(t, privacyNoneKey), models.ContentModeNone, models.PrivacySourceKey)
}

// privacy.retention.store_bodies=false reads as metadata, and a content_mode
// of none still wins over it.
func TestStoreBodiesFalseIsMetadata(t *testing.T) {
	noBodies := false
	org := privacyOrgConfig(&tenant.PrivacyConfig{Retention: &tenant.PrivacyRetentionConfig{StoreBodies: &noBodies}})
	pt := newPrivacyTest(t, org)
	wantPrivacy(t, "store_bodies false", pt.chat(t, privacyPlainKey), models.ContentModeMetadata, models.PrivacySourceOrg)

	org.Privacy.ContentMode = "none"
	pt.tenants.Set(privacyOrg, org)
	wantPrivacy(t, "store_bodies false, content_mode none", pt.chat(t, privacyPlainKey), models.ContentModeNone, models.PrivacySourceOrg)
}
