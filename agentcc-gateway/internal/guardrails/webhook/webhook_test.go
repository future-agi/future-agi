package webhook

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

func makeInput(content string) *guardrails.CheckInput {
	raw, _ := json.Marshal(content)
	return &guardrails.CheckInput{
		Request: &models.ChatCompletionRequest{
			Model: "gpt-4o",
			Messages: []models.Message{
				{Role: "user", Content: raw},
			},
		},
		Metadata: map[string]string{"key": "value"},
	}
}

func TestWebhook_Pass(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		json.NewEncoder(w).Encode(webhookResponse{
			Pass:    true,
			Score:   0.0,
			Message: "OK",
		})
	}))
	defer srv.Close()

	g := New("test-hook", map[string]interface{}{"url": srv.URL})
	r := g.Check(context.Background(), makeInput("Hello"))
	if !r.Pass {
		t.Fatal("expected pass")
	}
}

func TestWebhook_Block(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		json.NewEncoder(w).Encode(webhookResponse{
			Pass:    false,
			Score:   0.95,
			Message: "Blocked by custom policy",
		})
	}))
	defer srv.Close()

	g := New("test-hook", map[string]interface{}{"url": srv.URL})
	r := g.Check(context.Background(), makeInput("Bad content"))
	if r.Pass {
		t.Fatal("expected block")
	}
	if r.Score != 0.95 {
		t.Errorf("expected score 0.95, got %f", r.Score)
	}
}

func TestWebhook_CustomHeaders(t *testing.T) {
	var receivedAuth string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		receivedAuth = r.Header.Get("Authorization")
		json.NewEncoder(w).Encode(webhookResponse{Pass: true})
	}))
	defer srv.Close()

	g := New("test-hook", map[string]interface{}{
		"url": srv.URL,
		"headers": map[string]interface{}{
			"Authorization": "Bearer my-token",
		},
	})
	g.Check(context.Background(), makeInput("Hello"))

	if receivedAuth != "Bearer my-token" {
		t.Errorf("expected auth header, got %q", receivedAuth)
	}
}

func TestWebhook_ReceivesPayload(t *testing.T) {
	var received webhookRequest
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		json.NewDecoder(r.Body).Decode(&received)
		json.NewEncoder(w).Encode(webhookResponse{Pass: true})
	}))
	defer srv.Close()

	g := New("test-hook", map[string]interface{}{"url": srv.URL})
	g.Check(context.Background(), makeInput("Test message"))

	if received.Metadata["key"] != "value" {
		t.Error("expected metadata to be passed")
	}
	if received.Request == nil {
		t.Error("expected request to be passed")
	}
}

func TestWebhook_ServerError_Retry(t *testing.T) {
	callCount := 0
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		callCount++
		if callCount < 3 {
			w.WriteHeader(http.StatusInternalServerError)
			return
		}
		json.NewEncoder(w).Encode(webhookResponse{Pass: true})
	}))
	defer srv.Close()

	g := New("test-hook", map[string]interface{}{
		"url":   srv.URL,
		"retry": 2,
	})
	r := g.Check(context.Background(), makeInput("Test"))
	if !r.Pass {
		t.Fatal("expected pass after retry")
	}
	if callCount != 3 {
		t.Errorf("expected 3 attempts, got %d", callCount)
	}
}

func TestWebhook_AllRetriesFail(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusInternalServerError)
	}))
	defer srv.Close()

	g := New("test-hook", map[string]interface{}{
		"url":   srv.URL,
		"retry": 1,
	})
	r := g.Check(context.Background(), makeInput("Test"))
	if r.Pass {
		t.Fatal("expected fail after all retries exhausted")
	}
	if !strings.Contains(r.Message, "failed after") {
		t.Errorf("expected failure message, got %q", r.Message)
	}
}

func TestWebhook_Timeout(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		time.Sleep(5 * time.Second)
		json.NewEncoder(w).Encode(webhookResponse{Pass: true})
	}))
	defer srv.Close()

	ctx, cancel := context.WithTimeout(context.Background(), 100*time.Millisecond)
	defer cancel()

	g := New("test-hook", map[string]interface{}{"url": srv.URL})
	r := g.Check(ctx, makeInput("Test"))
	if r.Pass {
		t.Fatal("expected fail on timeout")
	}
}

func TestWebhook_EmptyURL(t *testing.T) {
	g := New("test-hook", nil)
	r := g.Check(context.Background(), makeInput("Test"))
	if !r.Pass {
		t.Fatal("empty URL should pass")
	}
}

func TestWebhook_InvalidJSON_Response(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Write([]byte("not json"))
	}))
	defer srv.Close()

	g := New("test-hook", map[string]interface{}{"url": srv.URL})
	r := g.Check(context.Background(), makeInput("Test"))
	if r.Pass {
		t.Fatal("expected fail on invalid JSON response")
	}
}

func TestWebhook_Name(t *testing.T) {
	g := New("my-custom-guard", nil)
	if g.Name() != "my-custom-guard" {
		t.Errorf("expected custom name, got %s", g.Name())
	}
}

func TestWebhook_Details(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		json.NewEncoder(w).Encode(webhookResponse{
			Pass:    false,
			Score:   0.8,
			Message: "Blocked",
			Details: map[string]interface{}{
				"reason": "custom_policy",
			},
		})
	}))
	defer srv.Close()

	g := New("test-hook", map[string]interface{}{"url": srv.URL})
	r := g.Check(context.Background(), makeInput("Test"))
	if r.Details["reason"] != "custom_policy" {
		t.Error("expected custom details passed through")
	}
}

func TestIsWebhookConfig(t *testing.T) {
	if !IsWebhookConfig(map[string]interface{}{"url": "https://example.com"}) {
		t.Error("should detect webhook config")
	}
	if IsWebhookConfig(map[string]interface{}{"entities": []interface{}{"email"}}) {
		t.Error("non-webhook config should return false")
	}
	if IsWebhookConfig(nil) {
		t.Error("nil config should return false")
	}
}
