package server

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/providers"
)

// A timeout on /v1/messages answers in Anthropic's own terms, timeout_error,
// with the gateway_timeout code the other routes use, whether the model is
// Claude (passed through) or GPT (translated), and whether or not it streams.

// anthropicErrorPayload is an Anthropic error as a client decodes it. Code is
// a pointer so an absent code can be told from an empty one.
type anthropicErrorPayload struct {
	Type  string `json:"type"`
	Error struct {
		Type    string  `json:"type"`
		Message string  `json:"message"`
		Code    *string `json:"code"`
	} `json:"error"`
}

func decodeAnthropicError(t *testing.T, data string) anthropicErrorPayload {
	t.Helper()
	var payload anthropicErrorPayload
	if err := json.Unmarshal([]byte(data), &payload); err != nil {
		t.Fatalf("not an Anthropic error body: %v\n%s", err, data)
	}
	if payload.Type != "error" {
		t.Errorf("type = %q, want error\n%s", payload.Type, data)
	}
	return payload
}

// assertTimeoutError checks the type and the code separately: either alone
// would pass for a body that has only one of them.
func assertTimeoutError(t *testing.T, payload anthropicErrorPayload, raw string) {
	t.Helper()
	if payload.Error.Type != "timeout_error" {
		t.Errorf("error.type = %q, want timeout_error\n%s", payload.Error.Type, raw)
	}
	if payload.Error.Code == nil || *payload.Error.Code != "gateway_timeout" {
		t.Errorf("error.code = %v, want gateway_timeout\n%s", payload.Error.Code, raw)
	}
	if payload.Error.Message == "" {
		t.Errorf("error.message is empty\n%s", raw)
	}
}

// messagesRoutes are /v1/messages for a Claude model, relayed natively, and
// for a GPT model, translated.
var messagesRoutes = []streamRoute{nativeRoute, translatedRoute}

func nonStreaming(route streamRoute) streamRoute {
	route.name += " non-stream"
	route.body = strings.Replace(route.body, `"stream":true`, `"stream":false`, 1)
	return route
}

func TestMessagesTimeoutIsATimeoutError(t *testing.T) {
	for _, route := range messagesRoutes {
		t.Run(nonStreaming(route).name+", upstream stalls before answering", func(t *testing.T) {
			capture := &capturePlugin{}
			upstream, _ := streamingUpstream(t, upstreamStallsBeforeHeaders)
			gateway := streamingGateway(t, upstream.URL, capture)

			resp, body := postStream(t, gateway, nonStreaming(route), map[string]string{"x-agentcc-timeout": "300ms"})

			if resp.StatusCode != http.StatusGatewayTimeout {
				t.Errorf("status = %d, want 504\n%s", resp.StatusCode, body)
			}
			if ct := resp.Header.Get("Content-Type"); !strings.HasPrefix(ct, "application/json") {
				t.Errorf("Content-Type = %q, want application/json", ct)
			}
			assertTimeoutError(t, decodeAnthropicError(t, body), body)
			if got := capture.status.Load(); got != http.StatusGatewayTimeout {
				t.Errorf("request recorded as %d, want 504", got)
			}
		})

		t.Run(route.name+", upstream stalls mid-stream", func(t *testing.T) {
			capture := &capturePlugin{}
			upstream, _ := streamingUpstream(t, upstreamStalls)
			gateway := streamingGateway(t, upstream.URL, capture)

			resp, body := postStream(t, gateway, route, map[string]string{"x-agentcc-timeout": "300ms"})

			if resp.StatusCode != http.StatusOK {
				t.Fatalf("status = %d, want the 200 the stream started with\n%s", resp.StatusCode, body)
			}
			frames := sseFrames(body)
			if len(frames) == 0 {
				t.Fatalf("no events in the stream:\n%s", body)
			}
			last := frames[len(frames)-1]
			if last.event != "error" {
				t.Fatalf("last event = %q, want error:\n%s", last.event, body)
			}
			assertTimeoutError(t, decodeAnthropicError(t, last.data), last.data)
			assertNotFinished(t, body)
			if got := capture.status.Load(); got != http.StatusGatewayTimeout {
				t.Errorf("request recorded as %d, want 504", got)
			}
			if capture.usage.Load() == nil || !capture.estimated.Load() {
				t.Errorf("usage = %v, estimated = %v; want the output already sent priced, estimated",
					capture.usage.Load(), capture.estimated.Load())
			}
		})

		t.Run(route.name+", upstream stalls before the stream starts", func(t *testing.T) {
			capture := &capturePlugin{}
			upstream, _ := streamingUpstream(t, upstreamStallsBeforeHeaders)
			gateway := streamingGateway(t, upstream.URL, capture)

			resp, body := postStream(t, gateway, route, map[string]string{"x-agentcc-timeout": "300ms"})

			if resp.StatusCode != http.StatusGatewayTimeout {
				t.Errorf("status = %d, want 504\n%s", resp.StatusCode, body)
			}
			if ct := resp.Header.Get("Content-Type"); !strings.HasPrefix(ct, "application/json") {
				t.Errorf("Content-Type = %q, want application/json", ct)
			}
			assertTimeoutError(t, decodeAnthropicError(t, body), body)
			if got := capture.status.Load(); got != http.StatusGatewayTimeout {
				t.Errorf("request recorded as %d, want 504", got)
			}
		})
	}
}

// Errors that are not timeouts keep the types they had.
func TestMessagesOtherErrorsKeepTheirTypes(t *testing.T) {
	// An upstream 429, per route, as dev already answers it: a native response
	// is forwarded as the upstream sent it; a native stream that cannot start
	// is the gateway's 502; a translated request is the gateway's 429.
	rejections := []struct {
		route      streamRoute
		wantStatus int
		wantType   string
		wantBody   string // the whole body, when it must pass through untouched
	}{
		{nonStreaming(nativeRoute), http.StatusTooManyRequests, "rate_limit_error", anthropicUpstreamRejection},
		{nativeRoute, http.StatusBadGateway, "api_error", ""},
		{nonStreaming(translatedRoute), http.StatusTooManyRequests, "rate_limit_error", ""},
		{translatedRoute, http.StatusTooManyRequests, "rate_limit_error", ""},
	}
	for _, tc := range rejections {
		t.Run(tc.route.name+", upstream 429", func(t *testing.T) {
			upstream, _ := streamingUpstream(t, upstreamRejects)
			gateway := streamingGateway(t, upstream.URL)

			resp, body := postStream(t, gateway, tc.route, nil)

			if resp.StatusCode != tc.wantStatus {
				t.Errorf("status = %d, want %d\n%s", resp.StatusCode, tc.wantStatus, body)
			}
			if payload := decodeAnthropicError(t, body); payload.Error.Type != tc.wantType {
				t.Errorf("error.type = %q, want %q\n%s", payload.Error.Type, tc.wantType, body)
			}
			if tc.wantBody != "" && body != tc.wantBody {
				t.Errorf("body = %s, want the upstream's own %s", body, tc.wantBody)
			}
		})
	}

	for _, route := range messagesRoutes {
		for _, r := range []streamRoute{route, nonStreaming(route)} {
			t.Run(r.name+", model not allowed for the key", func(t *testing.T) {
				upstream, _ := streamingUpstream(t, upstreamFinishes)
				gateway := keyRestrictedGateway(t, upstream.URL)

				resp, body := postStream(t, gateway, r, map[string]string{"Authorization": "Bearer test-virtual-key"})

				if resp.StatusCode != http.StatusForbidden {
					t.Errorf("status = %d, want 403\n%s", resp.StatusCode, body)
				}
				payload := decodeAnthropicError(t, body)
				if payload.Error.Type != "permission_error" {
					t.Errorf("error.type = %q, want permission_error\n%s", payload.Error.Type, body)
				}
				if payload.Error.Code == nil || *payload.Error.Code != "permission_denied" {
					t.Errorf("error.code = %v, want permission_denied\n%s", payload.Error.Code, body)
				}
			})
		}
	}
}

// keyRestrictedGateway serves a gateway whose one key may use only a model
// neither messages route asks for.
func keyRestrictedGateway(t *testing.T, upstreamURL string) string {
	t.Helper()
	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{{
		Name: "restricted", Key: "test-virtual-key", Owner: "test-org", KeyType: "internal",
		Metadata: map[string]string{"access_groups": "only_gemini"},
	}}
	cfg.Routing.AccessGroups = config.AccessGroupsConfig{
		"only_gemini": {Models: []string{"gemini-3.7-flash"}},
	}
	cfg.Providers["anthropic"] = config.ProviderConfig{
		BaseURL: upstreamURL, APIKey: "test-key", APIFormat: "anthropic",
		Models: []string{"claude-sonnet-5-5"},
	}
	cfg.Providers["openai"] = config.ProviderConfig{
		BaseURL: upstreamURL, APIKey: "test-key", APIFormat: "openai",
		Models: []string{"gpt-4o"},
	}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("creating registry: %v", err)
	}
	srv := New(cfg, "", registry, pipeline.NewEngine(), nil, nil, nil, nil, testModelDBPtr(), nil, nil)
	srv.ready.Store(true)
	ts := httptest.NewServer(srv.httpServer.Handler)
	t.Cleanup(ts.Close)
	return ts.URL
}

// A GPT upstream that answers its own 504 gateway_timeout reaches the
// gateway as a provider error, a 502: that is not this gateway's timeout.
func TestMessagesUpstreamGatewayTimeoutIsNotOurs(t *testing.T) {
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !strings.HasSuffix(r.URL.Path, "/chat/completions") {
			io.WriteString(w, `{"data":[]}`)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusGatewayTimeout)
		io.WriteString(w, `{"error":{"message":"upstream timed out","type":"gateway_timeout","code":"gateway_timeout"}}`)
	}))
	t.Cleanup(upstream.Close)
	gateway := streamingGateway(t, upstream.URL)

	for _, route := range []streamRoute{translatedRoute, nonStreaming(translatedRoute)} {
		t.Run(route.name, func(t *testing.T) {
			resp, body := postStream(t, gateway, route, nil)

			if resp.StatusCode != http.StatusBadGateway {
				t.Errorf("status = %d, want 502\n%s", resp.StatusCode, body)
			}
			if payload := decodeAnthropicError(t, body); payload.Error.Type != "api_error" {
				t.Errorf("error.type = %q, want api_error\n%s", payload.Error.Type, body)
			}
		})
	}
}

func TestDeadlineAsTimeout(t *testing.T) {
	live := context.Background()
	expired, cancelExpired := context.WithDeadline(context.Background(), time.Now().Add(-time.Second))
	defer cancelExpired()
	left, leave := context.WithCancel(context.Background())
	leave()

	upstream := models.ErrUpstreamProvider(http.StatusBadGateway, "upstream broke")

	if got := deadlineAsTimeout(live, upstream); got != upstream {
		t.Errorf("live context: got %v, want the error as it was", got)
	}
	if got := deadlineAsTimeout(left, context.Canceled); !errors.Is(got, context.Canceled) {
		t.Errorf("client left: got %v, want context.Canceled as it was", got)
	}
	for _, err := range []error{context.DeadlineExceeded, upstream} {
		var apiErr *models.APIError
		if got := deadlineAsTimeout(expired, err); !errors.As(got, &apiErr) || apiErr.Type != models.ErrTypeGatewayTimeout || apiErr.Status != http.StatusGatewayTimeout {
			t.Errorf("deadline passed, err %v: got %v, want the gateway timeout", err, got)
		}
	}
}
