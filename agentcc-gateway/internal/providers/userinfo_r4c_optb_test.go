package providers

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// RED at 90b80504: the provider-creation Info log contains tenant URL userinfo.
// Only that publication may change; the tenant's explicit endpoint/auth stays.
func TestOptBR4CTenantURLUserinfoLog(t *testing.T) {
	for _, tc := range []struct{ name, user, password string }{
		{"plain", "r4c-log-user", "r4c-log-password"},
		{"escaped", "r4c-user@fixture", "r4c-password:/@"},
		{"username_only", "r4c-username-only", ""},
	} {
		t.Run(tc.name, func(t *testing.T) {
			wire := make(chan *http.Request, 1)
			upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				_, _ = io.Copy(io.Discard, r.Body)
				wire <- r.Clone(context.Background())
				w.WriteHeader(http.StatusBadRequest)
				_, _ = io.WriteString(w, `{"error":{"message":"synthetic refusal"}}`)
			}))
			defer upstream.Close()
			r4cPinOrgDial(t, upstream)
			const cleanURL = "http://r4c-upstream.invalid/proxy?route=fixture"
			u, err := url.Parse(cleanURL)
			if err != nil {
				t.Fatal(err)
			}
			u.User = url.User(tc.user)
			if tc.password != "" {
				u.User = url.UserPassword(tc.user, tc.password)
			}
			tenantCfg := &tenant.ProviderConfig{APIFormat: "anthropic", BaseURL: u.String()}
			before := *tenantCfg
			var output bytes.Buffer
			previous := slog.Default()
			slog.SetDefault(slog.New(slog.NewJSONHandler(&output, nil)))
			defer slog.SetDefault(previous)
			cache := NewOrgProviderCache(map[string]config.ProviderConfig{}, false)
			defer cache.EvictAll()
			p, err := cache.GetOrCreateWithTenantConfig("r4c-log-org", "anthropic", r4cTenantKey, tenantCfg)
			if err != nil {
				t.Fatal(err)
			}
			var logged map[string]any
			for _, line := range bytes.Split(bytes.TrimSpace(output.Bytes()), []byte("\n")) {
				var attrs map[string]any
				if err := json.Unmarshal(line, &attrs); err != nil {
					t.Fatal("provider creation log is not structured JSON")
				}
				if attrs["msg"] == "created per-org provider" {
					if logged != nil {
						t.Fatal("duplicate provider creation log")
					}
					logged = attrs
				}
			}
			if logged == nil {
				t.Fatal("provider creation Info log was not emitted")
			}
			// Decode JSON first so escaping cannot hide a planted credential.
			loggedURL, ok := logged["base_url"].(string)
			if !ok {
				t.Fatal("base_url attribute absent")
			}
			userPresent := strings.Contains(loggedURL, url.User(tc.user).String()) || strings.Contains(loggedURL, tc.user)
			passwordPresent := tc.password != "" && (strings.Contains(loggedURL, tc.password) || strings.Contains(loggedURL, strings.TrimPrefix(url.UserPassword("", tc.password).String(), ":")))
			t.Logf("P2-log user_present=%t password_present=%t", userPresent, passwordPresent)
			if userPresent || passwordPresent || loggedURL != cleanURL {
				t.Error("P2-log: userinfo must be absent while the logged endpoint/path/query are preserved")
			}
			if tenantCfg.BaseURL != before.BaseURL {
				t.Error("logging redaction mutated the tenant configuration")
			}
			r4cDispatch(t, p, "anthropic")
			select {
			case sent := <-wire:
				user, password, ok := sent.BasicAuth()
				if !ok || user != tc.user || password != tc.password || sent.Header.Get("X-Api-Key") != r4cTenantKey {
					t.Error("log-only redaction changed explicit tenant authentication")
				}
			default:
				t.Fatal("no outbound tenant request")
			}
		})
	}
}
