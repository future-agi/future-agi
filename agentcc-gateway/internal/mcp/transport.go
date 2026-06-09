package mcp

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"os/exec"
	"strings"
	"sync"
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

// --- Stdio Transport ---

// StdioTransport communicates with an MCP server launched as a subprocess.
type StdioTransport struct {
	command string
	args    []string

	mu      sync.Mutex
	cmd     *exec.Cmd
	stdin   io.WriteCloser
	scanner *bufio.Scanner
	healthy atomic.Bool
	closed  atomic.Bool
	pending map[string]chan *Message // request ID → response channel
	pendMu  sync.Mutex

	stderrDone chan struct{}
}

// NewStdioTransport creates a transport that launches a subprocess.
func NewStdioTransport(command string, args []string) *StdioTransport {
	return &StdioTransport{
		command: command,
		args:    args,
		pending: make(map[string]chan *Message),
	}
}

// Start launches the subprocess and begins reading stdout.
func (t *StdioTransport) Start(ctx context.Context) error {
	t.mu.Lock()
	defer t.mu.Unlock()

	cmd := exec.CommandContext(ctx, t.command, t.args...)

	stdin, err := cmd.StdinPipe()
	if err != nil {
		return fmt.Errorf("stdin pipe: %w", err)
	}
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		stdin.Close()
		return fmt.Errorf("stdout pipe: %w", err)
	}
	stderr, err := cmd.StderrPipe()
	if err != nil {
		stdin.Close()
		stdout.Close()
		return fmt.Errorf("stderr pipe: %w", err)
	}

	if err := cmd.Start(); err != nil {
		return fmt.Errorf("start command: %w", err)
	}

	t.cmd = cmd
	t.stdin = stdin
	t.scanner = bufio.NewScanner(stdout)
	t.scanner.Buffer(make([]byte, 0, 64*1024), 64*1024) // 64KB max line
	t.stderrDone = make(chan struct{})
	t.healthy.Store(true)

	// Drain stderr to log.
	go func() {
		defer close(t.stderrDone)
		s := bufio.NewScanner(stderr)
		for s.Scan() {
			slog.Warn("mcp stdio stderr", "cmd", t.command, "line", s.Text())
		}
	}()

	// Read stdout responses.
	go t.readLoop()

	return nil
}

func (t *StdioTransport) readLoop() {
	for t.scanner.Scan() {
		line := t.scanner.Bytes()
		if len(line) == 0 {
			continue
		}

		var msg Message
		if err := json.Unmarshal(line, &msg); err != nil {
			slog.Warn("mcp stdio: invalid JSON from server", "error", err)
			continue
		}

		// Route response to waiting caller.
		if msg.IsResponse() && msg.ID != nil {
			idStr := string(msg.ID)
			t.pendMu.Lock()
			ch, ok := t.pending[idStr]
			if ok {
				delete(t.pending, idStr)
			}
			t.pendMu.Unlock()

			if ok {
				ch <- &msg
			}
		}
		// Notifications from server could be handled here in the future.
	}

	if err := t.scanner.Err(); err != nil && !t.closed.Load() {
		slog.Error("mcp stdio read error", "error", err)
	}
	t.healthy.Store(false)
}

func (t *StdioTransport) Send(ctx context.Context, msg *Message) (*Message, error) {
	if t.closed.Load() {
		return nil, fmt.Errorf("transport closed")
	}

	data, err := json.Marshal(msg)
	if err != nil {
		return nil, fmt.Errorf("marshal: %w", err)
	}

	// For notifications, just write and return.
	if msg.IsNotification() {
		t.mu.Lock()
		_, err = t.stdin.Write(append(data, '\n'))
		t.mu.Unlock()
		return nil, err
	}

	// Register a response channel.
	idStr := string(msg.ID)
	ch := make(chan *Message, 1)
	t.pendMu.Lock()
	t.pending[idStr] = ch
	t.pendMu.Unlock()

	// Write the request.
	t.mu.Lock()
	_, err = t.stdin.Write(append(data, '\n'))
	t.mu.Unlock()
	if err != nil {
		t.pendMu.Lock()
		delete(t.pending, idStr)
		t.pendMu.Unlock()
		return nil, fmt.Errorf("write: %w", err)
	}

	// Wait for response or context cancellation.
	select {
	case resp := <-ch:
		return resp, nil
	case <-ctx.Done():
		t.pendMu.Lock()
		delete(t.pending, idStr)
		t.pendMu.Unlock()
		return nil, ctx.Err()
	}
}

func (t *StdioTransport) Close() error {
	if t.closed.Swap(true) {
		return nil // already closed
	}

	t.mu.Lock()
	defer t.mu.Unlock()

	if t.stdin != nil {
		t.stdin.Close()
	}
	if t.cmd != nil && t.cmd.Process != nil {
		// Send SIGTERM, wait up to 5s, then SIGKILL.
		t.cmd.Process.Kill()
		done := make(chan error, 1)
		go func() { done <- t.cmd.Wait() }()
		select {
		case <-done:
		case <-time.After(5 * time.Second):
		}
	}

	// Clean up pending requests.
	t.pendMu.Lock()
	for id, ch := range t.pending {
		close(ch)
		delete(t.pending, id)
	}
	t.pendMu.Unlock()

	t.healthy.Store(false)
	return nil
}

func (t *StdioTransport) Healthy() bool {
	return t.healthy.Load()
}
