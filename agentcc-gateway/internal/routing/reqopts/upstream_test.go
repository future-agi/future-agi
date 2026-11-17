package reqopts

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"regexp"
	"strings"
	"sync"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

var upstreamUserShape = regexp.MustCompile(`^fa_org_[0-9a-f]{24}$`)

func TestHashOrgID(t *testing.T) {
	key := []byte("test-hash-key")
	keyed := hashOrgID(key, "org-1")
	m := hmac.New(sha256.New, key)
	m.Write([]byte("org-1"))
	if want := "fa_org_" + hex.EncodeToString(m.Sum(nil))[:24]; keyed != want {
		t.Errorf("keyed = %s, want %s", keyed, want)
	}
	sum := sha256.Sum256([]byte("agentcc:org-1"))
	if unkeyed, want := hashOrgID(nil, "org-1"), "fa_org_"+hex.EncodeToString(sum[:])[:24]; unkeyed != want {
		t.Errorf("unkeyed = %s, want %s", unkeyed, want)
	}
	for _, u := range []string{keyed, hashOrgID(nil, "org-1"), hashOrgID(key, "")} {
		if !upstreamUserShape.MatchString(u) {
			t.Errorf("%q does not match %s", u, upstreamUserShape)
		}
	}
	if hashOrgID(key, "org-1") == hashOrgID(key, "org-2") || hashOrgID(key, "org-1") == hashOrgID([]byte("other"), "org-1") {
		t.Error("the hash must depend on both the org and the key")
	}
	if UpstreamUser("org-1") != UpstreamUser("org-1") || !upstreamUserShape.MatchString(UpstreamUser("org-1")) {
		t.Error("UpstreamUser must be stable and fa_org_-shaped")
	}
}

// resetUpstreamUser forgets the configured key and the lazy read, and
// restores them when the test ends.
func resetUpstreamUser(t *testing.T) {
	t.Helper()
	saved := userKey.Load()
	forget := func() {
		userKey.Store(nil)
		userKeyLazy = sync.Once{}
	}
	forget()
	t.Cleanup(func() {
		forget()
		userKey.Store(saved)
	})
}

// TestUpstreamUserConfigWins: without configuration the key is read from
// AGENTCC_MANAGED_USER_HASH_KEY; ConfigureUpstreamUser wins over it, before
// or after that read, and an empty configured key selects the unkeyed form.
func TestUpstreamUserConfigWins(t *testing.T) {
	resetUpstreamUser(t)
	t.Setenv(upstreamUserEnv, "env-key")
	if got, want := UpstreamUser("org-1"), hashOrgID([]byte("env-key"), "org-1"); got != want {
		t.Fatalf("unconfigured: %s, want the env key's %s", got, want)
	}
	ConfigureUpstreamUser("config-key")
	if got, want := UpstreamUser("org-1"), hashOrgID([]byte("config-key"), "org-1"); got != want {
		t.Fatalf("configured after the env read: %s, want the config key's %s", got, want)
	}

	resetUpstreamUser(t)
	ConfigureUpstreamUser("config-key")
	if got, want := UpstreamUser("org-1"), hashOrgID([]byte("config-key"), "org-1"); got != want {
		t.Fatalf("configured first: %s, want the config key's %s", got, want)
	}

	resetUpstreamUser(t)
	ConfigureUpstreamUser("")
	if got, want := UpstreamUser("org-1"), hashOrgID(nil, "org-1"); got != want {
		t.Fatalf("configured empty: %s, want the unkeyed %s (the env is not consulted)", got, want)
	}
}

// TestSlugsAreUpstreamNames: provider.only, ignore and order name upstream
// slugs, matched case-insensitively with an optional quantization, and a
// direct provider's slug comes from its preset, never its gateway id.
func TestSlugsAreUpstreamNames(t *testing.T) {
	for _, c := range []struct {
		name, upstream, quant string
		want                  bool
	}{
		{"anthropic", "anthropic", "", true},
		{"Anthropic", "anthropic", "fp8", true},
		{"deepinfra/fp8", "deepinfra", "fp8", true},
		{"deepinfra/FP8", "deepinfra", "fp8", true},
		{"deepinfra/fp8", "deepinfra", "bf16", false},
		{"deepinfra", "together", "", false},
		{"operator-openai", "openai", "", false},
	} {
		if got := SlugMatches(c.name, c.upstream, c.quant); got != c.want {
			t.Errorf("SlugMatches(%q, %q, %q) = %v, want %v", c.name, c.upstream, c.quant, got, c.want)
		}
	}
	for _, c := range []struct {
		cfg  config.ProviderConfig
		want string
	}{
		{config.ProviderConfig{APIFormat: "openai"}, "openai"},
		{config.ProviderConfig{APIFormat: "anthropic"}, "anthropic"},
		{config.ProviderConfig{APIFormat: "bedrock"}, "amazon-bedrock"},
		{config.ProviderConfig{APIFormat: "gemini", BaseURL: "https://us-central1-aiplatform.googleapis.com"}, "google-vertex"},
		{config.ProviderConfig{APIFormat: "gemini", BaseURL: "https://generativelanguage.googleapis.com"}, "google-ai-studio"},
		{config.ProviderConfig{APIFormat: "azure"}, "azure"},
		{config.ProviderConfig{APIFormat: "openai", Type: "deepinfra"}, "deepinfra"},
		{config.ProviderConfig{APIFormat: "openai", UpstreamSlug: "fireworks"}, "fireworks"},
	} {
		if got := UpstreamSlug(c.cfg); got != c.want {
			t.Errorf("UpstreamSlug(%+v) = %q, want %q", c.cfg, got, c.want)
		}
	}
}

func TestSummaryBoundedAndPrivate(t *testing.T) {
	req := request(t, withMessages(`"providerOptions":{"gateway":{"user":"alice@example.com","sessionId":"sess-secret"}},
		"session_id":"body-session","plugins":[{"id":"web"}]`))
	res, apiErr := Parse(req)
	if apiErr != nil {
		t.Fatal(apiErr.Message)
	}
	s := Summary(res)
	if strings.Contains(s, "alice") || strings.Contains(s, "sess-secret") || strings.Contains(s, "body-session") {
		t.Errorf("summary carries end-user or session ids: %s", s)
	}
	var parsed map[string]any
	if err := json.Unmarshal([]byte(s), &parsed); err != nil || parsed["plugins"] == nil {
		t.Errorf("summary %s is not the expected JSON (%v)", s, err)
	}

	big := `"plugins":[{"id":"` + strings.Repeat("x", 2000) + `"}],"models":["a"]`
	res, apiErr = Parse(request(t, withMessages(big)))
	if apiErr != nil {
		t.Fatal(apiErr.Message)
	}
	s = Summary(res)
	if len(s) > MaxSummaryBytes || !strings.Contains(s, `"truncated":true`) || !strings.Contains(s, `"models"`) {
		t.Errorf("long summary = %d bytes %q, want at most %d, truncated, with the field names", len(s), s, MaxSummaryBytes)
	}
}
