package providers

import (
	"context"
	"encoding/json"
	"errors"
	"go/ast"
	"go/parser"
	"go/token"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"reflect"
	"sort"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers/speech"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
	"github.com/gorilla/websocket"
)

// config/config.go:2198-2200 names the seven managed formats; its speech
// route roots at :2479 and registry.go:158-176 add deepgram and cartesia.
var r4cFormats = []string{"anthropic", "azure", "bedrock", "cartesia", "cohere", "deepgram", "gemini", "google", "openai"}

const r4cTenantKey = "r4c-synthetic-tenant-key"

// Fail if the factory grows beyond this matrix, including non-managed formats.
func TestOptBR4CAPIFormatCoverage(t *testing.T) {
	file, err := parser.ParseFile(token.NewFileSet(), "registry.go", nil, 0)
	if err != nil {
		t.Fatal(err)
	}
	var formats []string
	for _, decl := range file.Decls {
		fn, ok := decl.(*ast.FuncDecl)
		if !ok || fn.Name.Name != "createProvider" {
			continue
		}
		ast.Inspect(fn.Body, func(n ast.Node) bool {
			if clause, ok := n.(*ast.CaseClause); ok {
				for _, expr := range clause.List {
					lit, ok := expr.(*ast.BasicLit)
					if !ok || lit.Kind != token.STRING {
						t.Fatal("provider format case is no longer a string literal")
					}
					value, err := strconv.Unquote(lit.Value)
					if err != nil {
						t.Fatal(err)
					}
					formats = append(formats, value)
				}
			}
			return true
		})
	}
	sort.Strings(formats)
	if !reflect.DeepEqual(formats, r4cFormats) {
		t.Fatalf("update the all-format header matrix: factory=%v matrix=%v", formats, r4cFormats)
	}
	for _, preset := range config.KnownProviders {
		found := false
		for _, format := range r4cFormats {
			found = found || preset.APIFormat == format
		}
		if !found {
			t.Fatalf("config preset format %q is not covered", preset.APIFormat)
		}
	}
}

func r4cDispatch(t *testing.T, p Provider, format string) {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	var err error
	if format == "deepgram" || format == "cartesia" {
		path := "/v1/listen"
		if format == "cartesia" {
			path = "/stt/websocket"
		}
		session, openErr := p.(*speech.Provider).Open(path, url.Values{"encoding": {"linear16"}, "sample_rate": {"16000"}}, http.Header{})
		if openErr != nil {
			t.Fatal(openErr)
		}
		dialer := websocket.Dialer{HandshakeTimeout: 5 * time.Second}
		conn, resp, dialErr := dialer.DialContext(ctx, session.URL, session.Header)
		if conn != nil {
			conn.Close()
		}
		if resp != nil {
			resp.Body.Close()
		}
		err = dialErr // The loopback fixture deliberately refuses the upgrade.
	} else {
		_, err = p.ChatCompletion(ctx, &models.ChatCompletionRequest{Model: "fixture", Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"fixture"`)}}})
	}
	if err == nil {
		t.Fatal("fixture refusal was not returned")
	}
}

// GREEN controls at 90b80504: inherited credential values must not reach the
// actual HTTP/WebSocket request. Adapter-generated tenant auth is still present.
func TestOptBR4CForbiddenHeadersEveryFormat(t *testing.T) {
	for _, format := range r4cFormats {
		for _, mixed := range []bool{false, true} {
			casing := "lower"
			if mixed {
				casing = "mixed"
			}
			t.Run(format+"/"+casing, func(t *testing.T) {
				wire := make(chan http.Header, 1)
				upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					_, _ = io.Copy(io.Discard, r.Body)
					wire <- r.Header.Clone()
					w.WriteHeader(http.StatusBadRequest)
					_, _ = io.WriteString(w, `{"error":{"message":"synthetic refusal"}}`)
				}))
				defer upstream.Close()
				blocked := []string{"authorization", "proxy-authorization", "api-key", "x-api-key", "x-goog-api-key", "cookie", "x-r4c-unknown"}
				headers := map[string]string{"user-agent": "r4c-reviewed-default"}
				for _, name := range blocked {
					key := name
					if mixed {
						key = http.CanonicalHeaderKey(name)
					}
					headers[key] = "r4c-node-value-for-" + name
				}
				base := config.ProviderConfig{APIFormat: format, BaseURL: upstream.URL, Headers: headers}
				tenantCfg := &tenant.ProviderConfig{APIKey: r4cTenantKey, AWSRegion: "eu-west-3", AWSAccessKeyID: "r4c-tenant-access", AWSSecretAccessKey: "r4c-tenant-secret"}
				cache := NewOrgProviderCache(map[string]config.ProviderConfig{format: base}, false)
				defer cache.EvictAll()
				p, err := cache.GetOrCreateWithTenantConfig("r4c-org", format, r4cTenantKey, tenantCfg)
				if err != nil {
					t.Fatal(err)
				}
				r4cDispatch(t, p, format)
				var sent http.Header
				select {
				case sent = <-wire:
				default:
					t.Fatal("no outbound request reached the loopback fixture")
				}
				resolved := resolveOrgConfig(base, r4cTenantKey, tenantCfg)
				for _, name := range blocked {
					for key := range resolved.Headers {
						if strings.EqualFold(key, name) {
							t.Errorf("inherited %s survived in the org configuration", name)
						}
					}
					for _, values := range sent {
						for _, value := range values {
							if strings.Contains(value, "r4c-node-value-for-"+name) {
								t.Errorf("inherited %s reached the outbound request", name)
							}
						}
					}
				}
				authHeader, authValue := "Authorization", "Bearer "+r4cTenantKey
				switch format {
				case "anthropic", "cartesia":
					authHeader, authValue = "X-Api-Key", r4cTenantKey
				case "azure":
					authHeader, authValue = "Api-Key", r4cTenantKey
				case "gemini", "google":
					authHeader, authValue = "X-Goog-Api-Key", r4cTenantKey
				case "deepgram":
					authValue = "Token " + r4cTenantKey
				case "bedrock":
					authValue = sent.Get("Authorization")
					if !strings.Contains(authValue, "Credential=r4c-tenant-access/") || !strings.Contains(authValue, "/eu-west-3/bedrock/aws4_request") {
						t.Error("outbound SigV4 is not signed by the tenant in the tenant region")
					}
				}
				for _, name := range blocked {
					want := ""
					if strings.EqualFold(name, authHeader) {
						want = authValue
					}
					if sent.Get(name) != want {
						t.Errorf("outbound %s differs from tenant-only authentication", name)
					}
				}
			})
		}
	}
}

func TestOptBR4CDuplicateKeptHeaderEveryFamily(t *testing.T) {
	for _, format := range r4cFormats {
		t.Run(format, func(t *testing.T) {
			name := map[string]string{"azure": "auth-type", "gemini": "x-gcp-project", "google": "x-gcp-location", "bedrock": "x-aws-region", "cohere": "x-cohere-input-type"}[format]
			if name == "" {
				name = "user-agent"
			}
			base := config.ProviderConfig{APIFormat: format, BaseURL: "https://fixture.invalid", Headers: map[string]string{name: "r4c-first-private-value", http.CanonicalHeaderKey(name): "r4c-second-private-value"}}
			cache := NewOrgProviderCache(map[string]config.ProviderConfig{format: base}, false)
			defer cache.EvictAll()
			_, err := cache.GetOrCreate("r4c-org", format, r4cTenantKey)
			var typed *ConfigurationError
			if !errors.As(err, &typed) || typed.Problem != "inherited provider settings contain duplicate names" {
				t.Fatal("ambiguous retained header did not produce the typed duplicate-name refusal")
			}
			for _, value := range []string{"r4c-first-private-value", "r4c-second-private-value", r4cTenantKey, "fixture.invalid"} {
				if strings.Contains(err.Error(), value) {
					t.Error("duplicate-name refusal contains a configuration value")
				}
			}
			if cache.Count() != 0 {
				t.Error("ambiguous configuration entered the org cache")
			}
		})
	}
}
