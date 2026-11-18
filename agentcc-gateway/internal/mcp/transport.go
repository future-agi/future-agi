package mcp

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"errors"
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

// MCP responses can include large tool schemas and resource payloads.
const maxStdioMessageSize = 1024 * 1024

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
	writeGate  chan struct{} // one complete JSON line at a time
	done       chan struct{} // closed by Close to wake senders
}

// NewStdioTransport creates a transport that launches a subprocess.
func NewStdioTransport(command string, args []string) *StdioTransport {
	return &StdioTransport{
		command:   command,
		args:      args,
		pending:   make(map[string]chan *Message),
		writeGate: make(chan struct{}, 1),
		done:      make(chan struct{}),
	}
}

// Start launches the subprocess and begins reading stdout. ctx only gates the
// launch; the subprocess runs until Close. Callers start it under a handshake
// timeout and cancel that once connected, which CommandContext would take as
// the signal to kill the process.
func (t *StdioTransport) Start(ctx context.Context) error {
	if err := ctx.Err(); err != nil {
		return err
	}

	t.mu.Lock()
	defer t.mu.Unlock()

	if t.closed.Load() || t.cmd != nil {
		return fmt.Errorf("stdio transport already started or closed")
	}
	cmd := stdioCommand(t.command, t.args...)

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
	t.scanner.Buffer(make([]byte, 0, 64*1024), maxStdioMessageSize)
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
	// No more responses are possible, even if the command itself is still
	// running. Wake requests and reconnect exactly as for a local close.
	if err := t.Close(); err != nil {
		slog.Warn("closing ended stdio session", "error", err)
	}
}

func (t *StdioTransport) Send(ctx context.Context, msg *Message) (*Message, error) {
	if t.closed.Load() {
		return nil, fmt.Errorf("transport closed")
	}

	data, err := json.Marshal(msg)
	if err != nil {
		return nil, fmt.Errorf("marshal: %w", err)
	}

	data = append(data, '\n')
	if msg.IsNotification() {
		return nil, t.write(ctx, data)
	}

	// Register a response channel.
	idStr := string(msg.ID)
	ch := make(chan *Message, 1)
	t.pendMu.Lock()
	t.pending[idStr] = ch
	t.pendMu.Unlock()

	defer func() {
		t.pendMu.Lock()
		if t.pending[idStr] == ch {
			delete(t.pending, idStr)
		}
		t.pendMu.Unlock()
	}()
	if err := t.write(ctx, data); err != nil {
		return nil, fmt.Errorf("write: %w", err)
	}

	select {
	case resp := <-ch:
		return resp, nil
	case <-ctx.Done():
		return nil, ctx.Err()
	case <-t.done:
		// A command may write its last complete response just before EOF.
		select {
		case resp := <-ch:
			return resp, nil
		default:
			return nil, fmt.Errorf("transport closed")
		}
	}
}

func (t *StdioTransport) write(ctx context.Context, data []byte) error {
	select {
	case t.writeGate <- struct{}{}:
		defer func() { <-t.writeGate }()
	case <-ctx.Done():
		return ctx.Err()
	case <-t.done:
		return fmt.Errorf("transport closed")
	}
	if err := ctx.Err(); err != nil {
		return err
	}
	if t.closed.Load() {
		return fmt.Errorf("transport closed")
	}
	// Unlike waiting for a response, interrupting a write may leave half a
	// JSON line on the pipe. Close the session rather than reusing that line.
	stop := context.AfterFunc(ctx, func() { t.Close() })
	_, err := t.stdin.Write(data)
	if !stop() {
		return ctx.Err()
	}
	return err
}

func (t *StdioTransport) Close() error {
	t.mu.Lock()
	defer t.mu.Unlock()
	if t.closed.Swap(true) {
		return nil // already closed
	}
	t.healthy.Store(false)
	close(t.done)

	if t.stdin != nil {
		t.stdin.Close()
	}
	if t.cmd == nil || t.cmd.Process == nil {
		return nil
	}
	err := stopStdioCommand(t.cmd)
	done := make(chan error, 1)
	go func() {
		_ = t.cmd.Wait() // a command stopped by signal has a nonzero status
		done <- reapStdioGroup(t.cmd.Process.Pid)
	}()
	select {
	case reapErr := <-done:
		return errors.Join(err, reapErr)
	case <-time.After(5 * time.Second):
		return fmt.Errorf("timed out waiting for stdio command to exit")
	}
}

func (t *StdioTransport) Healthy() bool {
	return t.healthy.Load()
}
