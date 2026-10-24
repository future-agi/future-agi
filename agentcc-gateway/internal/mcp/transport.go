package mcp

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"sync/atomic"
	"time"
)

// Transport abstracts communication with an MCP server.
type Transport interface {
	// Send sends a JSON-RPC message and returns the response.
	// For notifications (no ID), the response may be nil.
	Send(ctx context.Context, msg *Message) (*Message, error)
	// Close shuts down the transport.
	Close() error
	// Healthy returns whether the transport is operational.
	Healthy() bool
}

// --- HTTP Transport ---

// HTTPTransport communicates with an MCP server over Streamable HTTP.
type HTTPTransport struct {
	client    *http.Client
	baseURL   string
	sessionID atomic.Pointer[string]
	auth      AuthConfig
	healthy   atomic.Bool
}

// AuthConfig holds authentication settings for an upstream MCP server.
type AuthConfig struct {
	Type   string // "bearer", "api_key", "none"
	Token  string // for bearer auth
	Header string // header name for api_key auth
	Key    string // header value for api_key auth
}

// NewHTTPTransport creates a transport for an HTTP-based MCP server.
func NewHTTPTransport(baseURL string, auth AuthConfig) *HTTPTransport {
	t := &HTTPTransport{
		client: &http.Client{
			Timeout: 60 * time.Second,
			Transport: &http.Transport{
				MaxIdleConns:        10,
				MaxIdleConnsPerHost: 10,
				IdleConnTimeout:     90 * time.Second,
			},
		},
		baseURL: strings.TrimRight(baseURL, "/"),
		auth:    auth,
	}
	t.healthy.Store(true)
	return t
}

func (t *HTTPTransport) Send(ctx context.Context, msg *Message) (*Message, error) {
	body, err := json.Marshal(msg)
	if err != nil {
		return nil, fmt.Errorf("marshal request: %w", err)
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodPost, t.baseURL+"/mcp", bytes.NewReader(body))
	if err != nil {
		return nil, fmt.Errorf("create request: %w", err)
	}

	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Accept", "application/json")
	t.applyAuth(req)

	// Attach session ID if we have one.
	if sid := t.sessionID.Load(); sid != nil {
		req.Header.Set("MCP-Session-Id", *sid)
	}

	resp, err := t.client.Do(req)
	if err != nil {
		t.healthy.Store(false)
		return nil, fmt.Errorf("http request: %w", err)
	}
	defer resp.Body.Close()

	// Store session ID from response.
	if sid := resp.Header.Get("MCP-Session-Id"); sid != "" {
		t.sessionID.Store(&sid)
	}

	// For notifications (no ID), server may return 202 with no body.
	if msg.IsNotification() {
		if resp.StatusCode == http.StatusAccepted || resp.StatusCode == http.StatusNoContent {
			return nil, nil
		}
	}

	if resp.StatusCode != http.StatusOK {
		respBody, _ := io.ReadAll(io.LimitReader(resp.Body, 4096))
		return nil, fmt.Errorf("http %d: %s", resp.StatusCode, string(respBody))
	}

	var result Message
	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		return nil, fmt.Errorf("decode response: %w", err)
	}

	t.healthy.Store(true)
	return &result, nil
}

func (t *HTTPTransport) Close() error {
	t.client.CloseIdleConnections()
	return nil
}

func (t *HTTPTransport) Healthy() bool {
	return t.healthy.Load()
}

func (t *HTTPTransport) applyAuth(req *http.Request) {
	switch t.auth.Type {
	case "bearer":
		req.Header.Set("Authorization", "Bearer "+t.auth.Token)
	case "api_key":
		if t.auth.Header != "" {
			req.Header.Set(t.auth.Header, t.auth.Key)
		}
	}
}
