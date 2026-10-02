package typesafe

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"github.com/futureagi/agentcc-gateway/internal/providers/testhttp"
	"io"
	"net/http"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

const testKey = "synthetic-typesafe-key"
const testState = "private-state-text"
const testInstructions = "private-question-instructions"

func request() *models.SystemOneRequest {
	return &models.SystemOneRequest{Model: "jev-latest", State: json.RawMessage(`"` + testState + `"`), Questions: map[string]json.RawMessage{"q": json.RawMessage(`{"type":"noul","instructions":"` + testInstructions + `","unknown":{"preserved":true}}`)}}
}

func newProvider(t *testing.T, cfg config.ProviderConfig) *Provider {
	t.Helper()
	p, err := New("typesafe", cfg)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		if err := p.Close(); err != nil {
			t.Error(err)
		}
	})
	return p
}

func checkError(t *testing.T, err error, status int, code string) *models.APIError {
	t.Helper()
	var apiErr *models.APIError
	if !errors.As(err, &apiErr) || apiErr.Status != status || apiErr.Code != code {
		t.Fatalf("error = %v, want %d %s", err, status, code)
	}
	for _, secret := range []string{testKey, testState, testInstructions} {
		if strings.Contains(err.Error(), secret) {
			t.Fatal("error leaked request content or key")
		}
	}
	return apiErr
}

func assertJSON(t *testing.T, a, b []byte) {
	t.Helper()
	var av, bv any
	if err := json.Unmarshal(a, &av); err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(b, &bv); err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(av, bv) {
		t.Fatalf("JSON changed: %s != %s", a, b)
	}
}

func TestSystemOneSuccess(t *testing.T) {
	// Concrete instances of the authoritative stage brief's map-keyed answer shapes.
	for _, tt := range []struct{ name, question, answer string }{
		{"noul", `{"type":"noul","instructions":"judge"}`, `{"type":"noul","noul":0.9}`},
		{"choice", `{"type":"choice","instructions":"judge","criteria":{"yes":null,"no":"negative"}}`, `{"type":"choice","choice":"yes","probabilities":{"yes":0.9,"no":0.1},"confidence":0.8}`},
		{"score", `{"type":"score","instructions":"judge","criteria":["bad","okay","good"]}`, `{"type":"score","score":1.05,"legend":{"0":"bad","1":"okay","2":"good"},"probabilities":{"0":0.2,"1":0.55,"2":0.25},"confidence":0.8}`},
		{"unknown_fields", `{"type":"noul","instructions":{"task":["judge"]},"unknown":{"preserved":true}}`, `{"type":"noul","noul":0.9,"future":{"nested":[1,true,null]}}`},
	} {
		t.Run(tt.name, func(t *testing.T) {
			req := request()
			req.Questions["q"] = json.RawMessage(tt.question)
			wantBody, _ := json.Marshal(req)
			fixture := `{"model":"jev-1.13.0","answers":{"q":` + tt.answer + `},"usage":{"input_tokens":17,"output_tokens":3}}`
			upstream := testhttp.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.Method != "POST" || r.URL.Path != "/v1/systemone" {
					t.Errorf("unexpected request: %s %s", r.Method, r.URL.Path)
				}
				if r.Header.Get("Authorization") != "Bearer "+testKey || r.Header.Get("Content-Type") != "application/json" {
					t.Error("incorrect upstream headers")
				}
				body, _ := io.ReadAll(r.Body)
				assertJSON(t, body, wantBody)
				io.WriteString(w, fixture)
			}))
			defer upstream.Close()
			p := newProvider(t, config.ProviderConfig{DialContext: upstream.DialContext, BaseURL: upstream.URL + "/", APIKey: testKey})
			resp, err := p.SystemOne(context.Background(), req)
			if err != nil {
				t.Fatal(err)
			}
			got, _ := json.Marshal(resp)
			assertJSON(t, got, []byte(fixture))
			if string(resp.Answers["q"]) != tt.answer {
				t.Fatalf("raw answer changed: %s", resp.Answers["q"])
			}
		})
	}
}

func TestSystemOneErrors(t *testing.T) {
	sensitive, _ := json.Marshal(map[string]any{"error": map[string]string{"message": testKey + " " + testState + " " + testInstructions}})
	for _, tt := range []struct {
		name       string
		status     int
		body       string
		wantStatus int
		code       string
	}{
		{"400", 400, string(sensitive), 400, "upstream_rejected"},
		{"422", 422, string(sensitive), 400, "upstream_rejected"},
		{"400_non_json", 400, testState + testKey, 400, "upstream_rejected"},
		{"401", 401, string(sensitive), 502, "upstream_auth"},
		{"403", 403, string(sensitive), 502, "upstream_auth"},
		{"429", 429, string(sensitive), 429, "rate_limit_exceeded"},
		{"500", 500, string(sensitive), 502, "upstream_error"},
		{"503", 503, string(sensitive), 502, "upstream_error"},
		{"malformed", 200, `{"secret":"` + testState + `"`, 500, "upstream_invalid_response"},
		{"missing_model", 200, `{"answers":{"q":{}},"usage":{}}`, 500, "upstream_invalid_response"},
		{"missing_answers", 200, `{"model":"jev-1.13.0","usage":{}}`, 500, "upstream_invalid_response"},
		{"empty_answers", 200, `{"model":"jev-1.13.0","answers":{},"usage":{}}`, 500, "upstream_invalid_response"},
		{"array_answers", 200, `{"model":"jev-1.13.0","answers":[],"usage":{}}`, 500, "upstream_invalid_response"},
		{"missing_usage", 200, `{"model":"jev-1.13.0","answers":{"q":{}}}`, 500, "upstream_invalid_response"},
		{"null_usage", 200, `{"model":"jev-1.13.0","answers":{"q":{}},"usage":null}`, 500, "upstream_invalid_response"},
	} {
		t.Run(tt.name, func(t *testing.T) {
			upstream := testhttp.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				w.Header().Set("Retry-After", "2")
				w.WriteHeader(tt.status)
				io.WriteString(w, tt.body)
			}))
			defer upstream.Close()
			p := newProvider(t, config.ProviderConfig{DialContext: upstream.DialContext, BaseURL: upstream.URL, APIKey: testKey})
			_, err := p.SystemOne(context.Background(), request())
			checkError(t, err, tt.wantStatus, tt.code)
		})
	}
}

func TestSystemOneSafeUpstreamMessage(t *testing.T) {
	upstream := testhttp.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(422)
		io.WriteString(w, `{"error":{"message":"Unsupported question type"}}`)
	}))
	defer upstream.Close()
	p := newProvider(t, config.ProviderConfig{DialContext: upstream.DialContext, BaseURL: upstream.URL})
	_, err := p.SystemOne(context.Background(), request())
	apiErr := checkError(t, err, 400, "upstream_rejected")
	if !strings.Contains(apiErr.Message, "Unsupported question type") {
		t.Fatalf("safe upstream message lost: %s", apiErr.Message)
	}
}

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
			cfg := config.ProviderConfig{DialContext: upstream.DialContext, BaseURL: upstream.URL, DefaultTimeout: time.Second, MaxConcurrent: 1}
			ctx := context.Background()
			if mode == "context" {
				var cancel context.CancelFunc
				ctx, cancel = context.WithTimeout(ctx, timeout)
				defer cancel()
			} else {
				cfg.DefaultTimeout = timeout
			}
			p := newProvider(t, cfg)
			if mode == "concurrency" {
				p.semaphore <- struct{}{}
				defer func() { <-p.semaphore }()
			}
			_, err := p.SystemOne(ctx, request())
			checkError(t, err, 504, "gateway_timeout")
		})
	}
}

func TestProviderContract(t *testing.T) {
	p := newProvider(t, config.ProviderConfig{APIKey: testKey, Models: []string{"jev-1.13.0", "jev-latest"}})
	if p.ID() != "typesafe" || p.baseURL != "https://api.typesafe.ai" || p.httpClient.Timeout != 60*time.Second {
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
	if strings.Contains(string(data), testKey) {
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

func TestSystemOneDoesNotFollowRedirect(t *testing.T) {
	target := testhttp.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { t.Error("redirect received sensitive request") }))
	defer target.Close()
	upstream := testhttp.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Redirect(w, r, target.URL, http.StatusTemporaryRedirect)
	}))
	defer upstream.Close()
	p := newProvider(t, config.ProviderConfig{DialContext: upstream.DialContext, BaseURL: upstream.URL, APIKey: testKey})
	_, err := p.SystemOne(context.Background(), request())
	checkError(t, err, 502, "upstream_error")
}

func TestSystemOneTransportErrorsAreSanitized(t *testing.T) {
	p := newProvider(t, config.ProviderConfig{BaseURL: "http://%" + testKey})
	_, err := p.SystemOne(context.Background(), request())
	checkError(t, err, 500, "internal_error")
	req := request()
	req.State = json.RawMessage(fmt.Sprintf("invalid-%s", testState))
	_, err = p.SystemOne(context.Background(), req)
	checkError(t, err, 500, "internal_error")
}

func TestSystemOneErrorsSanitizeEachSensitiveValue(t *testing.T) {
	for _, tt := range []struct{ name, state, secret string }{
		{"key", `"private-state-text"`, testKey},
		{"state", `"private-state-text"`, testState},
		{"instructions", `{}`, testInstructions},
		{"structured_state", `{"record":[{"text":"private-nested-value"}]}`, "private-nested-value"},
		{"numeric_state", `{"record":[987654321]}`, "987654321"},
	} {
		t.Run(tt.name, func(t *testing.T) {
			upstream := testhttp.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				w.WriteHeader(422)
				json.NewEncoder(w).Encode(map[string]any{"error": map[string]string{"message": "Rejected " + tt.secret}})
			}))
			defer upstream.Close()
			p := newProvider(t, config.ProviderConfig{BaseURL: upstream.URL, DialContext: upstream.DialContext, APIKey: testKey})
			req := request()
			req.State = json.RawMessage(tt.state)
			_, err := p.SystemOne(context.Background(), req)
			apiErr := checkError(t, err, 400, "upstream_rejected")
			if strings.Contains(apiErr.Message, tt.secret) {
				t.Fatal("upstream error leaked an individual sensitive value")
			}
		})
	}
}
