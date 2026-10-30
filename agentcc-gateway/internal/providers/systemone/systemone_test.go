package systemone

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/netguard"
	"github.com/futureagi/agentcc-gateway/internal/providers/testhttp"
)

const testKey = "synthetic-systemone-key"

const testBody = `{"model":"jev-latest", "state":{"text":"private-state"},"questions":{"q":{"type":"score","instructions":"private-instructions","criteria":["bad","good"],"future":{"keep":[1,true,null]}}},"images":[]}`

func request(t *testing.T, body string) *models.DecisionRequest {
	t.Helper()
	req, apiErr := models.ParseDecisionRequest([]byte(body))
	if apiErr != nil {
		t.Fatal(apiErr)
	}
	return req
}

func newProvider(t *testing.T, id string, cfg config.ProviderConfig) *Provider {
	t.Helper()
	p, err := New(id, cfg)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { p.Close() })
	return p
}

func checkError(t *testing.T, err error, status int, code string, secrets ...string) *models.APIError {
	t.Helper()
	var apiErr *models.APIError
	if !errors.As(err, &apiErr) || apiErr.Status != status || apiErr.Code != code {
		t.Fatalf("error = %v, want %d %s", err, status, code)
	}
	for _, secret := range append(secrets, testKey, "private-state", "private-instructions") {
		if strings.Contains(apiErr.Error(), secret) {
			t.Fatalf("error %q leaked the key or request content", apiErr.Error())
		}
	}
	return apiErr
}

func TestDialectFor(t *testing.T) {
	for _, tt := range []struct {
		id, baseURL, want string
	}{
		{"typesafe", "", "typesafe"},
		{"typesafe", "https://api.typesafe.ai", "typesafe"},
		{"typesafe_org_org-1", "", "typesafe"},
		{"anything", "https://api.typesafe.ai/", "typesafe"},
		{"anything", "https://staging.api.typesafe.ai", "typesafe"},
		{"perplexity-decider", "https://api.perplexity.ai", "perplexity"},
		{"perplexity-decider_org_org-1", "", "perplexity"},
		{"custom-systemone", "https://api.perplexity.ai", "perplexity"},
		{"typesafe", "https://typesafe-proxy.example.com", "typesafe"},
		{"custom-systemone", "https://acme.services.ai.azure.com/providers/microsoft", "generic"},
		{"strands", "http://strands.internal:8080", "generic"},
		// A host that merely contains a vendor's name is not that vendor.
		{"custom", "https://nottypesafe.ai.example.com", "generic"},
	} {
		d, err := DialectFor(tt.id, tt.baseURL)
		if err != nil || d.Name != tt.want {
			t.Errorf("DialectFor(%q, %q) = %q, %v; want %q", tt.id, tt.baseURL, d.Name, err, tt.want)
		}
	}
	if _, err := DialectFor("custom-systemone", ""); !errors.Is(err, ErrBaseURLRequired) {
		t.Errorf("a custom endpoint without base_url: err = %v", err)
	}
	if _, err := New("custom-systemone", config.ProviderConfig{APIKey: testKey}); !errors.Is(err, ErrBaseURLRequired) {
		t.Errorf("New without base_url: err = %v", err)
	}
}

func TestIsVendor(t *testing.T) {
	for _, tt := range []struct {
		id, baseURL string
		want        bool
	}{
		{"typesafe", "", true},
		{"perplexity", "https://api.perplexity.ai", true},
		{"my-judge", "https://api.typesafe.ai", true},
		{"openai", "https://api.openai.com", false},
		{"custom-systemone", "https://example.com", false},
	} {
		if got := IsVendor(tt.id, tt.baseURL); got != tt.want {
			t.Errorf("IsVendor(%q, %q) = %v", tt.id, tt.baseURL, got)
		}
	}
}

// Each dialect row decides the URL (base, version prefix, leaf) and the auth
// header. api_path_prefix holds a version segment only.
func TestDialectEndpoints(t *testing.T) {
	prefix := func(s string) *string { return &s }
	for _, tt := range []struct {
		name string
		id   string
		cfg  config.ProviderConfig
		want string
	}{
		{"typesafe_default", "typesafe", config.ProviderConfig{}, "https://api.typesafe.ai/v1/systemone"},
		{"perplexity_default", "perplexity-decider", config.ProviderConfig{}, "https://api.perplexity.ai/v1/decisions"},
		{"perplexity_by_host", "custom", config.ProviderConfig{BaseURL: "https://api.perplexity.ai/"}, "https://api.perplexity.ai/v1/decisions"},
		{"foundry", "custom-systemone", config.ProviderConfig{BaseURL: "https://acme.services.ai.azure.com/providers/microsoft"}, "https://acme.services.ai.azure.com/providers/microsoft/v1/systemone"},
		{"base_ending_in_prefix", "custom-systemone", config.ProviderConfig{BaseURL: "https://x.example.com/v1"}, "https://x.example.com/v1/systemone"},
		{"no_version", "custom-systemone", config.ProviderConfig{BaseURL: "https://x.example.com", APIPathPrefix: prefix("")}, "https://x.example.com/systemone"},
		{"other_version", "custom-systemone", config.ProviderConfig{BaseURL: "https://x.example.com", APIPathPrefix: prefix("/decisions/v1")}, "https://x.example.com/decisions/v1/systemone"},
	} {
		t.Run(tt.name, func(t *testing.T) {
			tt.cfg.APIKey = testKey
			p := newProvider(t, tt.id, tt.cfg)
			if p.endpoint != tt.want {
				t.Fatalf("endpoint = %q, want %q", p.endpoint, tt.want)
			}
			if p.dialect.AuthHeader != "Authorization" || p.dialect.AuthScheme != "Bearer " {
				t.Fatalf("auth = %q %q", p.dialect.AuthHeader, p.dialect.AuthScheme)
			}
		})
	}
	if _, err := New("custom-systemone", config.ProviderConfig{BaseURL: "ftp://x.example.com"}); err == nil {
		t.Error("a non-http base_url built a provider")
	}
}

// The body goes upstream byte for byte, to the dialect's leaf, with the key
// as a Bearer token and never as x-api-key; the 200 comes back unchanged.
func TestSystemOnePassesBodiesThrough(t *testing.T) {
	const answer = `{"model":"jev-1.13.0","answers":{"q":{"type":"score","score":1.05,"legend":{"0":"bad","1":"good"},"future":{"keep":true}}},"usage":{"input_tokens":17,"output_tokens":3},"extra":[1, 2]}`
	for _, tt := range []struct{ id, leaf string }{
		{"typesafe", "/v1/systemone"},
		{"perplexity-decider", "/v1/decisions"},
		{"custom-systemone", "/v1/systemone"},
	} {
		t.Run(tt.id, func(t *testing.T) {
			var calls atomic.Int32
			upstream := testhttp.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				calls.Add(1)
				if r.Method != http.MethodPost || r.URL.Path != tt.leaf {
					t.Errorf("request %s %s, want POST %s", r.Method, r.URL.Path, tt.leaf)
				}
				if got := r.Header.Get("Authorization"); got != "Bearer "+testKey {
					t.Errorf("Authorization = %q", got)
				}
				if r.Header.Get("x-api-key") != "" || r.Header.Get("Content-Type") != "application/json" {
					t.Error("wrong upstream headers")
				}
				body, _ := io.ReadAll(r.Body)
				if string(body) != testBody {
					t.Errorf("body changed:\n%s", body)
				}
				io.WriteString(w, answer)
			}))
			defer upstream.Close()
			// The dialect comes from the ID: the in-memory upstream's host says nothing.
			d, _ := DialectByName(strings.SplitN(tt.id, "-", 2)[0])
			if tt.id == "custom-systemone" {
				d = Generic
			}
			p, err := NewForDialect(tt.id, config.ProviderConfig{BaseURL: upstream.URL, APIKey: testKey, DialContext: upstream.DialContext}, d)
			if err != nil {
				t.Fatal(err)
			}
			defer p.Close()
			resp, err := p.SystemOne(context.Background(), request(t, testBody))
			if err != nil {
				t.Fatal(err)
			}
			if string(resp.Body) != answer || resp.Model != "jev-1.13.0" || resp.Usage != (models.DecisionUsage{InputTokens: 17, OutputTokens: 3}) {
				t.Fatalf("resp = %+v", resp)
			}
			if calls.Load() != 1 {
				t.Fatalf("upstream calls = %d", calls.Load())
			}
		})
	}
}

// errorCase is one error case from the shared fixture.
type errorCase struct {
	Name     string `json:"name"`
	Upstream struct {
		Status  int               `json:"status"`
		Headers map[string]string `json:"headers"`
		Body    json.RawMessage   `json:"body"`
		BodyRaw *string           `json:"body_raw"`
	} `json:"upstream"`
	Returned struct {
		Status     int    `json:"status"`
		Code       string `json:"code"`
		Message    string `json:"message"`
		RetryAfter string `json:"retry_after"`
	} `json:"returned"`
}

func loadErrorCases(t *testing.T) (cases []errorCase, apiKey string, req json.RawMessage) {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join("..", "..", "..", "..", "api_contracts", "gateway", "decision-cases.json"))
	if err != nil {
		t.Fatalf("reading the shared decision cases: %v", err)
	}
	var fixture struct {
		APIKey     string          `json:"api_key"`
		Request    json.RawMessage `json:"request"`
		ErrorCases []errorCase     `json:"error_cases"`
	}
	if err := json.Unmarshal(raw, &fixture); err != nil {
		t.Fatal(err)
	}
	if len(fixture.ErrorCases) == 0 {
		t.Fatal("the shared decision cases have no error cases")
	}
	return fixture.ErrorCases, fixture.APIKey, fixture.Request
}

// The vendor's answers map onto the gateway's error table, the shared one the
// Jev client also reads. No mapped error carries the key or the request.
func TestSystemOneSharedErrorCases(t *testing.T) {
	cases, apiKey, reqBody := loadErrorCases(t)
	secrets := []string{apiKey, "private-state-CANARY", "private-instructions-CANARY", "private-criteria-CANARY", "987654321"}
	for _, tc := range cases {
		t.Run(tc.Name, func(t *testing.T) {
			var calls atomic.Int32
			upstream := testhttp.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				calls.Add(1)
				for k, v := range tc.Upstream.Headers {
					w.Header().Set(k, v)
				}
				w.WriteHeader(tc.Upstream.Status)
				if tc.Upstream.BodyRaw != nil {
					io.WriteString(w, *tc.Upstream.BodyRaw)
				} else {
					w.Write(tc.Upstream.Body)
				}
			}))
			defer upstream.Close()
			p := newProvider(t, "typesafe", config.ProviderConfig{BaseURL: upstream.URL, APIKey: apiKey, DialContext: upstream.DialContext})
			_, err := p.SystemOne(context.Background(), request(t, string(reqBody)))
			apiErr := checkError(t, err, tc.Returned.Status, tc.Returned.Code, secrets...)
			if apiErr.RetryAfter != tc.Returned.RetryAfter {
				t.Errorf("Retry-After = %q, want %q", apiErr.RetryAfter, tc.Returned.RetryAfter)
			}
			if tc.Returned.Message != "" && apiErr.Message != tc.Returned.Message {
				t.Errorf("message = %q, want %q", apiErr.Message, tc.Returned.Message)
			}
			if apiErr.UpstreamStatus != tc.Upstream.Status {
				t.Errorf("upstream status = %d, want %d", apiErr.UpstreamStatus, tc.Upstream.Status)
			}
			if calls.Load() != 1 {
				t.Errorf("upstream calls = %d, want exactly 1 (no retries)", calls.Load())
			}
		})
	}
}

// A redirect is answered, not followed: the key and content go nowhere the
// provider was not configured to send them.
func TestSystemOneDoesNotFollowRedirects(t *testing.T) {
	for _, status := range []int{301, 302, 307, 308} {
		var followed atomic.Int32
		// Every connection the provider opens lands here, so a followed
		// redirect would arrive at /followed.
		upstream := testhttp.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			if r.URL.Path == "/followed" {
				followed.Add(1)
				io.WriteString(w, `{"model":"m","answers":{},"usage":{}}`)
				return
			}
			http.Redirect(w, r, "/followed", status)
		}))
		p := newProvider(t, "typesafe", config.ProviderConfig{BaseURL: upstream.URL, APIKey: testKey, DialContext: upstream.DialContext})
		_, err := p.SystemOne(context.Background(), request(t, testBody))
		checkError(t, err, 502, "upstream_error")
		upstream.Close()
		if followed.Load() != 0 {
			t.Fatalf("%d: the redirect was followed", status)
		}
	}
}

// The answer is read through a 10 MiB cap; past it the call fails rather than
// holding an unbounded body.
func TestSystemOneResponseCap(t *testing.T) {
	for _, tt := range []struct {
		name string
		size int
		ok   bool
	}{
		{"at_cap", models.MaxDecisionResponseBytes, true},
		{"over_cap", models.MaxDecisionResponseBytes + 1, false},
	} {
		t.Run(tt.name, func(t *testing.T) {
			prefix := `{"model":"jev-1.13.0","answers":{},"usage":{"input_tokens":1,"output_tokens":0},"pad":"`
			pad := strings.Repeat("a", tt.size-len(prefix)-2)
			body := prefix + pad + `"}`
			upstream := testhttp.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				io.WriteString(w, body)
			}))
			defer upstream.Close()
			p := newProvider(t, "typesafe", config.ProviderConfig{BaseURL: upstream.URL, APIKey: testKey, DialContext: upstream.DialContext})
			resp, err := p.SystemOne(context.Background(), request(t, testBody))
			if tt.ok {
				if err != nil || len(resp.Body) != tt.size {
					t.Fatalf("err = %v", err)
				}
				return
			}
			checkError(t, err, 502, "upstream_invalid_response")
		})
	}
}

// A vendor that needs a key is never called without one.
func TestSystemOneEmptyKeyMakesNoCall(t *testing.T) {
	for _, id := range []string{"typesafe", "perplexity-decider"} {
		t.Run(id, func(t *testing.T) {
			var calls atomic.Int32
			upstream := testhttp.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { calls.Add(1) }))
			defer upstream.Close()
			d, _ := DialectFor(id, "")
			p, err := NewForDialect(id, config.ProviderConfig{BaseURL: upstream.URL, DialContext: upstream.DialContext}, d)
			if err != nil {
				t.Fatal(err)
			}
			defer p.Close()
			_, err = p.SystemOne(context.Background(), request(t, testBody))
			checkError(t, err, 403, "provider_key_missing")
			if calls.Load() != 0 {
				t.Fatalf("upstream calls = %d, want 0", calls.Load())
			}
		})
	}
}

// A generic endpoint may run without a key; it then sends no Authorization.
func TestSystemOneGenericWithoutKey(t *testing.T) {
	upstream := testhttp.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "" {
			t.Error("sent an Authorization header with no key")
		}
		io.WriteString(w, `{"model":"m","answers":{},"usage":{"input_tokens":1,"output_tokens":0}}`)
	}))
	defer upstream.Close()
	p := newProvider(t, "custom-systemone", config.ProviderConfig{BaseURL: upstream.URL, DialContext: upstream.DialContext})
	if _, err := p.SystemOne(context.Background(), request(t, testBody)); err != nil {
		t.Fatal(err)
	}
}

// The timeout covers the whole call, including the wait for a slot.
func TestSystemOneTimeout(t *testing.T) {
	for _, mode := range []string{"context", "provider", "response_body", "concurrency"} {
		t.Run(mode, func(t *testing.T) {
			upstream := testhttp.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if mode == "response_body" {
					w.WriteHeader(200)
					w.(http.Flusher).Flush()
				}
				io.Copy(io.Discard, r.Body)
				<-r.Context().Done()
			}))
			defer upstream.Close()
			timeout := 25 * time.Millisecond
			cfg := config.ProviderConfig{BaseURL: upstream.URL, APIKey: testKey, DialContext: upstream.DialContext, DefaultTimeout: time.Second, MaxConcurrent: 1}
			ctx := context.Background()
			if mode == "context" {
				var cancel context.CancelFunc
				ctx, cancel = context.WithTimeout(ctx, timeout)
				defer cancel()
			} else {
				cfg.DefaultTimeout = timeout
			}
			p := newProvider(t, "typesafe", cfg)
			if mode == "concurrency" {
				p.semaphore <- struct{}{}
				defer func() { <-p.semaphore }()
			}
			_, err := p.SystemOne(ctx, request(t, testBody))
			checkError(t, err, 504, "gateway_timeout")
		})
	}
}

func TestSystemOneTransportFailure(t *testing.T) {
	upstream := testhttp.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {}))
	p := newProvider(t, "typesafe", config.ProviderConfig{BaseURL: upstream.URL, APIKey: testKey, DialContext: upstream.DialContext})
	upstream.Close()
	_, err := p.SystemOne(context.Background(), request(t, testBody))
	checkError(t, err, 502, "upstream_error")
}

// The provider takes the proxy it is given. With a checked dialer the dial
// reaches the proxy, not the vendor, so the base_url's host is checked before
// the request leaves: an internal one never reaches the proxy, and the error
// names neither the address nor the key.
func TestSystemOneProxyKeepsTheAddressCheck(t *testing.T) {
	var asked atomic.Int32
	var lastTarget atomic.Value
	proxy := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		asked.Add(1)
		lastTarget.Store(r.URL.String())
		io.WriteString(w, `{"model":"jev-1.13.0","answers":{"q":{"type":"score","score":1}},"usage":{"input_tokens":1,"output_tokens":0}}`)
	}))
	defer proxy.Close()
	proxyURL, err := url.Parse(proxy.URL)
	if err != nil {
		t.Fatal(err)
	}
	checked := func(base string) *Provider {
		dial, pick := netguard.Proxied(net.Dialer{}, false, http.ProxyURL(proxyURL))
		return newProvider(t, "custom-systemone", config.ProviderConfig{BaseURL: base, APIKey: testKey, DialContext: dial, Proxy: pick})
	}

	for _, base := range []string{"http://169.254.169.254", "https://169.254.169.254", "http://10.0.0.1:8000", "http://127.0.0.1:9", "http://metadata.google.internal"} {
		_, err := checked(base).SystemOne(context.Background(), request(t, testBody))
		checkError(t, err, http.StatusBadGateway, "upstream_error", "169.254", "10.0.0.1", "127.0.0.1")
	}
	if asked.Load() != 0 {
		t.Fatalf("an internal base_url reached the proxy %d times", asked.Load())
	}

	if _, err := checked("http://203.0.113.10").SystemOne(context.Background(), request(t, testBody)); err != nil {
		t.Fatalf("a public base_url through the proxy: %v", err)
	}
	if asked.Load() != 1 || lastTarget.Load() != "http://203.0.113.10/v1/systemone" {
		t.Fatalf("the proxy was asked %d times, last for %v", asked.Load(), lastTarget.Load())
	}
}

func TestProviderContract(t *testing.T) {
	p := newProvider(t, "typesafe", config.ProviderConfig{APIKey: testKey, Models: []string{"jev-1.13.0", "jev-latest"}})
	if p.ID() != "typesafe" || p.Dialect().Name != "typesafe" || p.timeout != 60*time.Second {
		t.Fatal("incorrect provider defaults")
	}
	list, err := p.ListModels(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if len(list) != 2 || list[0].ID != "jev-1.13.0" || list[1].ID != "jev-latest" || list[0].Object != "model" {
		t.Fatalf("models: %+v", list)
	}
	data, _ := json.Marshal(list)
	if bytes.Contains(data, []byte(testKey)) {
		t.Fatal("models leaked key")
	}
	_, err = p.ChatCompletion(context.Background(), nil)
	checkError(t, err, 501, "not_supported")
	chunks, errs := p.StreamChatCompletion(context.Background(), nil)
	if _, ok := <-chunks; ok {
		t.Fatal("unexpected chat chunk")
	}
	checkError(t, <-errs, 501, "not_supported")
	if _, ok := <-errs; ok {
		t.Fatal("error channel not closed")
	}
}
