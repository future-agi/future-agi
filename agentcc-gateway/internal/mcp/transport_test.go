package mcp

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

func TestHTTPTransportSend(t *testing.T) {
	// Mock MCP server.
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != "POST" {
			t.Fatalf("expected POST, got %s", r.Method)
		}
		if r.URL.Path != "/mcp" {
			t.Fatalf("expected /mcp, got %s", r.URL.Path)
		}
		if r.Header.Get("Content-Type") != "application/json" {
			t.Fatalf("expected application/json content type")
		}

		var msg Message
		if err := json.NewDecoder(r.Body).Decode(&msg); err != nil {
			t.Fatal(err)
		}

		resp, _ := NewResponse(msg.ID, map[string]string{"pong": "true"})
		w.Header().Set("Content-Type", "application/json")
		w.Header().Set("MCP-Session-Id", "test-session-123")
		json.NewEncoder(w).Encode(resp)
	}))
	defer server.Close()

	transport := NewHTTPTransport(server.URL, AuthConfig{})
	defer transport.Close()

	ctx := context.Background()
	msg := &Message{
		JSONRPC: "2.0",
		ID:      json.RawMessage(`1`),
		Method:  MethodPing,
	}

	resp, err := transport.Send(ctx, msg)
	if err != nil {
		t.Fatal(err)
	}
	if resp.Error != nil {
		t.Fatalf("unexpected error: %s", resp.Error.Message)
	}

	// Check session ID was stored.
	sid := transport.sessionID.Load()
	if sid == nil || *sid != "test-session-123" {
		t.Fatal("expected session ID to be stored")
	}
}

func TestHTTPTransportSendNotification(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusAccepted)
	}))
	defer server.Close()

	transport := NewHTTPTransport(server.URL, AuthConfig{})
	defer transport.Close()

	ctx := context.Background()
	notif := &Message{
		JSONRPC: "2.0",
		Method:  MethodInitialized,
	}

	resp, err := transport.Send(ctx, notif)
	if err != nil {
		t.Fatal(err)
	}
	if resp != nil {
		t.Fatal("expected nil response for notification")
	}
}

func TestHTTPTransportBearerAuth(t *testing.T) {
	var gotAuth string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotAuth = r.Header.Get("Authorization")
		resp, _ := NewResponse(json.RawMessage(`1`), struct{}{})
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(resp)
	}))
	defer server.Close()

	transport := NewHTTPTransport(server.URL, AuthConfig{
		Type:  "bearer",
		Token: "my-secret-token",
	})
	defer transport.Close()

	msg := &Message{JSONRPC: "2.0", ID: json.RawMessage(`1`), Method: MethodPing}
	transport.Send(context.Background(), msg)

	if gotAuth != "Bearer my-secret-token" {
		t.Fatalf("expected Bearer auth, got %s", gotAuth)
	}
}

func TestHTTPTransportAPIKeyAuth(t *testing.T) {
	var gotKey string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotKey = r.Header.Get("X-API-Key")
		resp, _ := NewResponse(json.RawMessage(`1`), struct{}{})
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(resp)
	}))
	defer server.Close()

	transport := NewHTTPTransport(server.URL, AuthConfig{
		Type:   "api_key",
		Header: "X-API-Key",
		Key:    "my-api-key",
	})
	defer transport.Close()

	msg := &Message{JSONRPC: "2.0", ID: json.RawMessage(`1`), Method: MethodPing}
	transport.Send(context.Background(), msg)

	if gotKey != "my-api-key" {
		t.Fatalf("expected API key, got %s", gotKey)
	}
}

func TestHTTPTransportSessionIDPersist(t *testing.T) {
	callCount := 0
	var sentSessionID string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		callCount++
		sentSessionID = r.Header.Get("MCP-Session-Id")

		resp, _ := NewResponse(json.RawMessage(`1`), struct{}{})
		w.Header().Set("Content-Type", "application/json")
		if callCount == 1 {
			w.Header().Set("MCP-Session-Id", "session-abc")
		}
		json.NewEncoder(w).Encode(resp)
	}))
	defer server.Close()

	transport := NewHTTPTransport(server.URL, AuthConfig{})
	defer transport.Close()

	ctx := context.Background()
	msg := &Message{JSONRPC: "2.0", ID: json.RawMessage(`1`), Method: MethodPing}

	// First call — no session ID sent.
	transport.Send(ctx, msg)
	if sentSessionID != "" {
		t.Fatal("first call should not send session ID")
	}

	// Second call — should send stored session ID.
	transport.Send(ctx, msg)
	if sentSessionID != "session-abc" {
		t.Fatalf("expected session-abc, got %s", sentSessionID)
	}
}

func TestHTTPTransportErrorResponse(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusInternalServerError)
		w.Write([]byte("internal error"))
	}))
	defer server.Close()

	transport := NewHTTPTransport(server.URL, AuthConfig{})
	defer transport.Close()

	msg := &Message{JSONRPC: "2.0", ID: json.RawMessage(`1`), Method: MethodPing}
	_, err := transport.Send(context.Background(), msg)
	if err == nil {
		t.Fatal("expected error for 500 response")
	}
}

func TestHTTPTransportTimeout(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		time.Sleep(5 * time.Second)
	}))
	defer server.Close()

	transport := NewHTTPTransport(server.URL, AuthConfig{})
	defer transport.Close()

	ctx, cancel := context.WithTimeout(context.Background(), 50*time.Millisecond)
	defer cancel()

	msg := &Message{JSONRPC: "2.0", ID: json.RawMessage(`1`), Method: MethodPing}
	_, err := transport.Send(ctx, msg)
	if err == nil {
		t.Fatal("expected timeout error")
	}
}

func TestHTTPTransportHealthy(t *testing.T) {
	transport := NewHTTPTransport("http://localhost:1", AuthConfig{})
	if !transport.Healthy() {
		t.Fatal("should be healthy initially")
	}

	// Send to unreachable server.
	msg := &Message{JSONRPC: "2.0", ID: json.RawMessage(`1`), Method: MethodPing}
	ctx, cancel := context.WithTimeout(context.Background(), 100*time.Millisecond)
	defer cancel()
	transport.Send(ctx, msg)

	if transport.Healthy() {
		t.Fatal("should be unhealthy after failed request")
	}
}

func TestHTTPTransportClose(t *testing.T) {
	transport := NewHTTPTransport("http://localhost:8080", AuthConfig{})
	if err := transport.Close(); err != nil {
		t.Fatalf("unexpected close error: %v", err)
	}
}
