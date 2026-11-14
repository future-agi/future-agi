package openai

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

const decisionBody = `{"model":"gpt-6-luna", "input":"private-input","questions":[{"type":"predicate","name":"resolved","instructions":"private-instructions"}],"future":{"x":1}}`

func decisionRequest(t *testing.T, body string) *models.DecisionRequest {
	t.Helper()
	req, apiErr := models.ParseDecisionRequest([]byte(body))
	if apiErr != nil {
		t.Fatal(apiErr)
	}
	return req
}

func newDecisionProvider(t *testing.T, baseURL, key string) *Provider {
	t.Helper()
	p, err := New("openai", config.ProviderConfig{BaseURL: baseURL, APIKey: key, APIFormat: "openai"})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { p.Close() })
	return p
}

func TestCreateDecisionPassesBodiesThrough(t *testing.T) {
	const answer = `{"id":"dec_1","model":"gpt-6-luna-2026-09","answers":[{"name":"resolved","value":true}],"usage":{"input_tokens":40,"output_tokens":0}}`
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost || r.URL.Path != "/v1/decisions" {
			t.Errorf("request %s %s", r.Method, r.URL.Path)
		}
		if r.Header.Get("Authorization") != "Bearer org-openai-key" {
			t.Errorf("Authorization = %q", r.Header.Get("Authorization"))
		}
		body, _ := io.ReadAll(r.Body)
		if string(body) != decisionBody {
			t.Errorf("body changed: %s", body)
		}
		io.WriteString(w, answer)
	}))
	defer ts.Close()

	resp, err := newDecisionProvider(t, ts.URL, "org-openai-key").CreateDecision(context.Background(), decisionRequest(t, decisionBody))
	if err != nil {
		t.Fatal(err)
	}
	if string(resp.Body) != answer || resp.Model != "gpt-6-luna-2026-09" || resp.Usage.InputTokens != 40 {
		t.Fatalf("resp = %+v", resp)
	}
}

// OpenAI Decisions answers map onto the same shared error table as System One.
func TestCreateDecisionSharedErrorCases(t *testing.T) {
	raw, err := os.ReadFile(filepath.Join("..", "..", "..", "..", "api_contracts", "gateway", "decision-cases.json"))
	if err != nil {
		t.Fatal(err)
	}
	var fixture struct {
		APIKey     string `json:"api_key"`
		ErrorCases []struct {
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
				RetryAfter string `json:"retry_after"`
			} `json:"returned"`
		} `json:"error_cases"`
	}
	if err := json.Unmarshal(raw, &fixture); err != nil {
		t.Fatal(err)
	}
	// The fixture's state and criteria are System One fields; this body
	// carries the same canaries in OpenAI's shape.
	body := `{"model":"gpt-6-luna","input":{"ticket":"private-state-CANARY","count":987654321},"questions":[{"type":"predicate","name":"resolved","instructions":"private-instructions-CANARY","criteria":{"true":"private-criteria-CANARY","false":"Unresolved"}}]}`
	for _, tc := range fixture.ErrorCases {
		t.Run(tc.Name, func(t *testing.T) {
			var calls atomic.Int32
			ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
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
			defer ts.Close()
			_, err := newDecisionProvider(t, ts.URL, fixture.APIKey).CreateDecision(context.Background(), decisionRequest(t, body))
			var apiErr *models.APIError
			if !errors.As(err, &apiErr) || apiErr.Status != tc.Returned.Status || apiErr.Code != tc.Returned.Code {
				t.Fatalf("error = %v, want %d %s", err, tc.Returned.Status, tc.Returned.Code)
			}
			if apiErr.RetryAfter != tc.Returned.RetryAfter {
				t.Errorf("Retry-After = %q, want %q", apiErr.RetryAfter, tc.Returned.RetryAfter)
			}
			for _, secret := range []string{fixture.APIKey, "private-state-CANARY", "private-instructions-CANARY", "private-criteria-CANARY", "987654321"} {
				if strings.Contains(apiErr.Error(), secret) {
					t.Fatalf("error %q leaked request content or the key", apiErr.Error())
				}
			}
			if calls.Load() != 1 {
				t.Errorf("upstream calls = %d, want 1", calls.Load())
			}
		})
	}
}

// The provider follows redirects for its other endpoints; a decision never.
func TestCreateDecisionDoesNotFollowRedirects(t *testing.T) {
	var followed atomic.Int32
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/followed" {
			followed.Add(1)
			io.WriteString(w, `{"model":"m"}`)
			return
		}
		http.Redirect(w, r, "/followed", http.StatusTemporaryRedirect)
	}))
	defer ts.Close()
	_, err := newDecisionProvider(t, ts.URL, "org-openai-key").CreateDecision(context.Background(), decisionRequest(t, decisionBody))
	var apiErr *models.APIError
	if !errors.As(err, &apiErr) || apiErr.Code != "upstream_error" || followed.Load() != 0 {
		t.Fatalf("err = %v followed = %d", err, followed.Load())
	}
}

func TestCreateDecisionResponseCap(t *testing.T) {
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		io.WriteString(w, `{"pad":"`+strings.Repeat("a", models.MaxDecisionResponseBytes)+`"}`)
	}))
	defer ts.Close()
	_, err := newDecisionProvider(t, ts.URL, "k").CreateDecision(context.Background(), decisionRequest(t, decisionBody))
	var apiErr *models.APIError
	if !errors.As(err, &apiErr) || apiErr.Code != "upstream_invalid_response" {
		t.Fatalf("err = %v", err)
	}
}

func TestCreateDecisionTimeout(t *testing.T) {
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		<-r.Context().Done()
	}))
	defer ts.Close()
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	_, err := newDecisionProvider(t, ts.URL, "k").CreateDecision(ctx, decisionRequest(t, decisionBody))
	var apiErr *models.APIError
	if !errors.As(err, &apiErr) || apiErr.Status != http.StatusGatewayTimeout {
		t.Fatalf("err = %v", err)
	}
}
