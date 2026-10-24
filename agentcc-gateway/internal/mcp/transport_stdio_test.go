package mcp

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"slices"
	"sort"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/privacy"
)

// --- the stdio helper: this test binary re-run as a small MCP server --------

// stdioHelperArg marks a helper run in os.Args; the flags after it configure
// the helper (see runStdioHelper).
const stdioHelperArg = "mcp-stdio-helper"

// stdioHelperCommand is the command and args that start the helper.
func stdioHelperCommand(flags ...string) (string, []string) {
	args := []string{"-test.run=^TestStdioHelperProcess$", "--", stdioHelperArg}
	return os.Args[0], append(args, flags...)
}

// TestStdioHelperProcess is not a test: it is the helper's entry point.
func TestStdioHelperProcess(t *testing.T) {
	for i, arg := range os.Args {
		if arg == stdioHelperArg {
			helperExit(runStdioHelper(os.Args[i+1:]))
		}
	}
}

// helperExit ends the helper at once. os.Exit in a -race binary sleeps 1 s
// first (GORACE atexit_sleep_ms), which would blur the shutdown timings.
func helperExit(code int) { syscall.Exit(code) }

// stdioHelper is the helper's state. Flags:
//
//	--marker <path>   append one line per lifecycle event to path
//	--ignore-eof      keep running after stdin closes
//	--ignore-sigterm  keep running after SIGTERM
//	--grandchild      start a grandchild helper (--sleeper) in the same group
//	--sleeper         do nothing but sleep (the grandchild)
//	--fail-initialize answer initialize with an error
//	--silent          answer nothing
//
// Tools: echo {text, delay_ms}, env_names, env {names}, big {bytes},
// stderr {lines, long_bytes}, pid, cwd.
type stdioHelper struct {
	marker         string
	ignoreEOF      bool
	ignoreSigterm  bool
	failInitialize bool
	silent         bool
	writeMu        sync.Mutex
	out            *bufio.Writer
}

func runStdioHelper(flags []string) int {
	h := &stdioHelper{out: bufio.NewWriter(os.Stdout)}
	grandchild, sleeper := false, false
	for i := 0; i < len(flags); i++ {
		switch flags[i] {
		case "--marker":
			i++
			h.marker = flags[i]
		case "--ignore-eof":
			h.ignoreEOF = true
		case "--ignore-sigterm":
			h.ignoreSigterm = true
		case "--grandchild":
			grandchild = true
		case "--sleeper":
			sleeper = true
		case "--fail-initialize":
			h.failInitialize = true
		case "--silent":
			h.silent = true
		}
	}
	if sleeper {
		for {
			time.Sleep(time.Hour)
		}
	}
	h.mark("start %d", os.Getpid())

	sigs := make(chan os.Signal, 1)
	signal.Notify(sigs, syscall.SIGTERM)
	go func() {
		for range sigs {
			h.mark("sigterm")
			if !h.ignoreSigterm {
				h.mark("exit-on-sigterm")
				helperExit(0)
			}
		}
	}()

	if grandchild {
		cmd := exec.Command(os.Args[0], "-test.run=^TestStdioHelperProcess$", "--", stdioHelperArg, "--sleeper")
		if err := cmd.Start(); err != nil {
			h.mark("grandchild-error %v", err)
			return 2
		}
		h.mark("grandchild %d", cmd.Process.Pid)
	}

	in := bufio.NewReaderSize(os.Stdin, 64*1024)
	for {
		line, err := in.ReadBytes('\n')
		if len(strings.TrimSpace(string(line))) > 0 {
			var msg Message
			if json.Unmarshal(line, &msg) == nil && msg.IsRequest() && !h.silent {
				go h.answer(&msg)
			}
		}
		if err != nil {
			break
		}
	}
	h.mark("stdin-closed")
	if h.ignoreEOF {
		for {
			time.Sleep(time.Hour)
		}
	}
	h.mark("graceful-exit")
	return 0
}

func (h *stdioHelper) mark(format string, args ...interface{}) {
	if h.marker == "" {
		return
	}
	f, err := os.OpenFile(h.marker, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o600)
	if err != nil {
		return
	}
	fmt.Fprintf(f, format+"\n", args...)
	f.Close()
}

func (h *stdioHelper) answer(msg *Message) {
	var result interface{}
	switch msg.Method {
	case MethodInitialize:
		if h.failInitialize {
			h.write(NewErrorResponse(msg.ID, ErrCodeInternal, "initialize refused"))
			return
		}
		result = map[string]interface{}{
			"protocolVersion": ProtocolVersion,
			"capabilities":    map[string]interface{}{"tools": map[string]interface{}{}},
			"serverInfo":      map[string]string{"name": "stdio-helper", "version": "1"},
		}
	case MethodToolsList:
		tools := []Tool{}
		for _, name := range []string{"echo", "env_names", "env", "big", "stderr", "pid", "cwd"} {
			tools = append(tools, Tool{Name: name, InputSchema: json.RawMessage(`{"type":"object"}`)})
		}
		result = ListToolsResult{Tools: tools}
	case MethodResourcesList:
		result = ListResourcesResult{Resources: []Resource{}}
	case MethodPromptsList:
		result = ListPromptsResult{Prompts: []Prompt{}}
	case MethodPing:
		result = struct{}{}
	case MethodToolsCall:
		var p struct {
			Name      string                 `json:"name"`
			Arguments map[string]interface{} `json:"arguments"`
		}
		_ = json.Unmarshal(msg.Params, &p)
		result = ToolCallResult{Content: []ContentPart{{Type: "text", Text: h.callTool(p.Name, p.Arguments)}}}
	default:
		h.write(NewErrorResponse(msg.ID, ErrCodeMethodNotFound, "unknown method"))
		return
	}
	resp, _ := NewResponse(msg.ID, result)
	h.write(resp)
}

func (h *stdioHelper) callTool(name string, args map[string]interface{}) string {
	number := func(key string) int {
		v, _ := args[key].(float64)
		return int(v)
	}
	switch name {
	case "echo":
		time.Sleep(time.Duration(number("delay_ms")) * time.Millisecond)
		text, _ := args["text"].(string)
		return text
	case "env_names":
		names := []string{}
		for _, kv := range os.Environ() {
			names = append(names, strings.SplitN(kv, "=", 2)[0])
		}
		sort.Strings(names)
		out, _ := json.Marshal(names)
		return string(out)
	case "env":
		values := map[string]string{}
		list, _ := args["names"].([]interface{})
		for _, n := range list {
			name, _ := n.(string)
			if v, ok := os.LookupEnv(name); ok {
				values[name] = v
			}
		}
		out, _ := json.Marshal(values)
		return string(out)
	case "big":
		return strings.Repeat("x", number("bytes"))
	case "stderr":
		list, _ := args["lines"].([]interface{})
		for _, l := range list {
			fmt.Fprintln(os.Stderr, l)
		}
		if n := number("long_bytes"); n > 0 {
			fmt.Fprintln(os.Stderr, strings.Repeat("y", n))
		}
		fmt.Fprintln(os.Stderr, "stderr-done")
		return "ok"
	case "pid":
		return strconv.Itoa(os.Getpid())
	case "cwd":
		dir, _ := os.Getwd()
		return dir
	}
	return "unknown tool " + name
}

func (h *stdioHelper) write(msg *Message) {
	data, _ := json.Marshal(msg)
	h.writeMu.Lock()
	defer h.writeMu.Unlock()
	h.out.Write(append(data, '\n'))
	h.out.Flush()
}

// --- test helpers -------------------------------------------------------------

// connectStdioHelper connects a Client to a fresh helper under a connect
// context that lives until the test ends, so a test fails only for the
// behaviour it names; TestStdioChildSurvivesConnectCancel cancels its own.
// The client is closed when the test ends.
func connectStdioHelper(t *testing.T, cfg ClientConfig, flags ...string) *Client {
	t.Helper()
	ctx, cancel := context.WithCancel(context.Background())
	t.Cleanup(cancel)
	return connectStdioHelperCtx(t, ctx, cfg, flags...)
}

func connectStdioHelperCtx(t *testing.T, ctx context.Context, cfg ClientConfig, flags ...string) *Client {
	t.Helper()
	cfg.TransportType = "stdio"
	cfg.Command, cfg.Args = stdioHelperCommand(flags...)
	c := NewClient(cfg)
	t.Cleanup(func() { c.Close() })
	if err := c.Connect(ctx); err != nil {
		t.Fatalf("connect %s: %v", cfg.ServerID, err)
	}
	return c
}

// callText calls a tool and returns its first text part.
func callText(t *testing.T, c *Client, name string, args map[string]interface{}) (string, error) {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	res, err := c.CallTool(ctx, name, args)
	if err != nil {
		return "", err
	}
	if res.IsError || len(res.Content) == 0 {
		return "", fmt.Errorf("tool %s: error result %+v", name, res)
	}
	return res.Content[0].Text, nil
}

// readMarker returns the helper's marker lines.
func readMarker(path string) []string {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil
	}
	return strings.Fields(strings.ReplaceAll(string(data), " ", "_"))
}

// logRecord is one captured slog record, with its attributes as strings.
type logRecord struct {
	level slog.Level
	msg   string
	attrs map[string]string
}

func (r logRecord) String() string { return fmt.Sprintf("%s %q %v", r.level, r.msg, r.attrs) }

type recordingHandler struct {
	mu    *sync.Mutex
	recs  *[]logRecord
	attrs []slog.Attr
}

func (h recordingHandler) Enabled(context.Context, slog.Level) bool { return true }

func (h recordingHandler) Handle(_ context.Context, r slog.Record) error {
	rec := logRecord{level: r.Level, msg: r.Message, attrs: map[string]string{}}
	for _, a := range h.attrs {
		rec.attrs[a.Key] = a.Value.String()
	}
	r.Attrs(func(a slog.Attr) bool {
		rec.attrs[a.Key] = a.Value.String()
		return true
	})
	h.mu.Lock()
	*h.recs = append(*h.recs, rec)
	h.mu.Unlock()
	return nil
}

func (h recordingHandler) WithAttrs(attrs []slog.Attr) slog.Handler {
	h.attrs = append(append([]slog.Attr{}, h.attrs...), attrs...)
	return h
}

func (h recordingHandler) WithGroup(string) slog.Handler { return h }

// captureSlog routes the default logger, at every level, into a recorder
// until the test ends, and returns a snapshot function.
func captureSlog(t *testing.T) func() []logRecord {
	t.Helper()
	mu := &sync.Mutex{}
	recs := &[]logRecord{}
	prev := slog.Default()
	slog.SetDefault(slog.New(recordingHandler{mu: mu, recs: recs}))
	t.Cleanup(func() { slog.SetDefault(prev) })
	return func() []logRecord {
		mu.Lock()
		defer mu.Unlock()
		return append([]logRecord(nil), *recs...)
	}
}

// --- tests --------------------------------------------------------------------

// TestStdioStderrRedactedCappedDebug: a child that prints a bearer token and
// a 64 KiB line to stderr leaves no secret and no line over the cap in the
// Debug log, logs each line with the server id and not the command line, and
// logs nothing at Warn.
func TestStdioStderrRedactedCappedDebug(t *testing.T) {
	logs := captureSlog(t)
	const serverID = "stderr-redact"
	c := connectStdioHelper(t, ClientConfig{ServerID: serverID})

	if _, err := callText(t, c, "stderr", map[string]interface{}{
		"lines":      []string{"auth failed for Bearer sk-test-123 retrying", "config api_key=sk-test-123"},
		"long_bytes": 64 * 1024,
	}); err != nil {
		t.Fatalf("stderr tool: %v", err)
	}

	sawDone := false
	for deadline := time.Now().Add(5 * time.Second); time.Now().Before(deadline) && !sawDone; {
		for _, r := range logs() {
			if strings.Contains(r.attrs["line"], "stderr-done") {
				sawDone = true
			}
		}
		time.Sleep(20 * time.Millisecond)
	}

	stderrLines := 0
	for _, r := range logs() {
		if strings.Contains(r.String(), "sk-test-123") {
			t.Errorf("secret reached the log: %s", r)
		}
		if r.level >= slog.LevelWarn && r.attrs["server"] == serverID {
			t.Errorf("record at %s for the server: %s", r.level, r)
		}
		if r.msg != "mcp stdio stderr" {
			continue
		}
		stderrLines++
		if r.level != slog.LevelDebug {
			t.Errorf("stderr line logged at %s, want DEBUG: %s", r.level, r)
		}
		if r.attrs["server"] != serverID {
			t.Errorf("stderr line without server id %q: %s", serverID, r)
		}
		if _, ok := r.attrs["cmd"]; ok {
			t.Errorf("stderr line carries the command line: %s", r)
		}
		if n := len(r.attrs["line"]); n > 1024 {
			t.Errorf("stderr line of %d bytes logged, cap is 1024", n)
		}
	}
	if !sawDone {
		t.Fatalf("the line after the 64 KiB line never reached the log (%d stderr records)", stderrLines)
	}
}

// TestStdioTransportHandlesLargeResponses: a 128 KiB result crosses the
// transport as one message.
func TestStdioTransportHandlesLargeResponses(t *testing.T) {
	command, args := stdioHelperCommand()
	transport := NewStdioTransport(command, args)

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if err := transport.Start(ctx); err != nil {
		t.Fatalf("start stdio transport: %v", err)
	}
	defer transport.Close()

	params, _ := json.Marshal(ToolCallParams{Name: "big", Arguments: map[string]interface{}{"bytes": 128 * 1024}})
	resp, err := transport.Send(ctx, &Message{JSONRPC: "2.0", ID: json.RawMessage(`1`), Method: MethodToolsCall, Params: params})
	if err != nil {
		t.Fatalf("send request: %v", err)
	}
	var result ToolCallResult
	if err := json.Unmarshal(resp.Result, &result); err != nil {
		t.Fatalf("decode response result: %v", err)
	}
	if len(result.Content) != 1 || len(result.Content[0].Text) != 128*1024 {
		t.Fatalf("expected a 128 KiB payload, got %+v", len(result.Content))
	}
}

// startHelperTransport starts a helper on a bare transport with opts.
func startHelperTransport(t *testing.T, opts stdioOptions, flags ...string) *StdioTransport {
	t.Helper()
	command, args := stdioHelperCommand(flags...)
	tr := newStdioTransport(StdioSpec{ServerID: t.Name(), Command: command, Args: args, Env: buildStdioEnv(DefaultStdioBaseEnv(), nil, nil)}, opts)
	if err := tr.Start(context.Background()); err != nil {
		t.Fatalf("start helper: %v", err)
	}
	t.Cleanup(func() { tr.Close() })
	return tr
}

// waitMarker waits until the marker has a line starting with prefix.
func waitMarker(t *testing.T, marker, prefix string) string {
	t.Helper()
	for deadline := time.Now().Add(10 * time.Second); time.Now().Before(deadline); time.Sleep(10 * time.Millisecond) {
		for _, l := range readMarker(marker) {
			if strings.HasPrefix(l, prefix) {
				return l
			}
		}
	}
	t.Fatalf("marker never showed %q: %v", prefix, readMarker(marker))
	return ""
}

// TestStdioShutdownLadder: Close closes stdin and waits the grace period, then
// SIGTERMs the process group and waits again, then SIGKILLs it; each step runs
// only when the one before did not end the child. The defaults are 5 s each.
func TestStdioShutdownLadder(t *testing.T) {
	// The lower bounds and the marker prove the ladder's order; the upper
	// bounds carry a second of slack for a loaded host.
	const grace, termWait, slack = time.Second, time.Second, time.Second
	opts := defaultStdioOptions()
	opts.grace, opts.termWait = grace, termWait

	cases := []struct {
		name     string
		flags    []string
		want     []string // marker lines after start, in order
		min, max time.Duration
	}{
		{"stdin close is enough", nil, []string{"stdin-closed", "graceful-exit"}, 0, grace + slack},
		{"SIGTERM after the grace", []string{"--ignore-eof"}, []string{"stdin-closed", "sigterm", "exit-on-sigterm"}, grace, grace + termWait + slack},
		{"SIGKILL after SIGTERM", []string{"--ignore-eof", "--ignore-sigterm", "--grandchild"}, []string{"stdin-closed", "sigterm"}, grace + termWait, grace + termWait + 3*slack},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			marker := filepath.Join(t.TempDir(), "marker")
			tr := startHelperTransport(t, opts, append([]string{"--marker", marker}, tc.flags...)...)
			waitMarker(t, marker, "start")
			grandchild := 0
			if slices.Contains(tc.flags, "--grandchild") {
				grandchild, _ = strconv.Atoi(strings.TrimPrefix(waitMarker(t, marker, "grandchild"), "grandchild_"))
			}
			pid := tr.proc.Pid()

			start := time.Now()
			tr.Close()
			took := time.Since(start)
			if took < tc.min || took > tc.max {
				t.Errorf("Close took %s, want between %s and %s", took, tc.min, tc.max)
			}
			var got []string
			for _, l := range readMarker(marker) {
				if !strings.HasPrefix(l, "start") && !strings.HasPrefix(l, "grandchild") {
					got = append(got, l)
				}
			}
			if strings.Join(got, ",") != strings.Join(tc.want, ",") {
				t.Errorf("child lifecycle = %v, want %v", got, tc.want)
			}
			if !processGone(pid, 2*time.Second) {
				t.Errorf("child %d still running after Close", pid)
			}
			if grandchild != 0 && !processGone(grandchild, 2*time.Second) {
				t.Errorf("grandchild %d in the child's group still running after Close", grandchild)
			}
		})
	}

	t.Run("defaults", func(t *testing.T) {
		d := defaultStdioOptions()
		c := NewClient(ClientConfig{})
		if d.grace != 5*time.Second || d.termWait != 5*time.Second || c.cfg.StdioGrace != 5*time.Second {
			t.Errorf("grace %s / %s, SIGTERM wait %s; want 5s each", d.grace, c.cfg.StdioGrace, d.termWait)
		}
		if d.maxMessageBytes != 16<<20 || c.cfg.MaxMessageBytes != 16<<20 {
			t.Errorf("max message bytes %d / %d, want 16 MiB", d.maxMessageBytes, c.cfg.MaxMessageBytes)
		}
		if d.stderrMaxLine != 1024 || c.cfg.StderrMaxLineBytes != 1024 {
			t.Errorf("stderr cap %d / %d, want 1024", d.stderrMaxLine, c.cfg.StderrMaxLineBytes)
		}
	})
}

// TestStdioFramingLimit: a message of exactly the limit is read; one byte more
// fails the call with upstream_message_too_large and stops the transport.
func TestStdioFramingLimit(t *testing.T) {
	const text = 8 << 10
	resp, _ := NewResponse(json.RawMessage(`1`), ToolCallResult{Content: []ContentPart{{Type: "text", Text: strings.Repeat("x", text)}}})
	line, _ := json.Marshal(resp)
	call := func(tr *StdioTransport) (*Message, error) {
		params, _ := json.Marshal(ToolCallParams{Name: "big", Arguments: map[string]interface{}{"bytes": text}})
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		return tr.Send(ctx, &Message{JSONRPC: "2.0", ID: json.RawMessage(`1`), Method: MethodToolsCall, Params: params})
	}

	opts := defaultStdioOptions()
	opts.maxMessageBytes = len(line)
	if _, err := call(startHelperTransport(t, opts)); err != nil {
		t.Fatalf("a %d-byte message at a %d-byte limit: %v", len(line), opts.maxMessageBytes, err)
	}

	opts.maxMessageBytes = len(line) - 1
	tr := startHelperTransport(t, opts)
	if _, err := call(tr); !errors.Is(err, ErrUpstreamMessageTooLarge) {
		t.Fatalf("a %d-byte message at a %d-byte limit: err = %v, want upstream_message_too_large", len(line), opts.maxMessageBytes, err)
	}
	if tr.Healthy() || !errors.Is(tr.Err(), ErrUpstreamMessageTooLarge) {
		t.Fatalf("transport after the oversize message: healthy=%v err=%v", tr.Healthy(), tr.Err())
	}
	if _, err := call(tr); !errors.Is(err, ErrUpstreamMessageTooLarge) {
		t.Fatalf("a call on the stopped transport: err = %v", err)
	}
}

// TestStdioStderrOperatorRedactorAndCutCount: the operator's redactor runs on
// every stderr line after the credential patterns, and each line cut to the
// cap is counted for the server, across reconnects.
func TestStdioStderrOperatorRedactorAndCutCount(t *testing.T) {
	logs := captureSlog(t)
	c := connectStdioHelper(t, ClientConfig{
		ServerID:           "stderr-operator",
		StderrMaxLineBytes: 64,
		StderrRedactor:     privacy.New(privacy.ModePatterns, []privacy.PatternConfig{{Name: "email", Pattern: `[a-z]+@example\.com`}}),
	})
	if _, err := callText(t, c, "stderr", map[string]interface{}{
		"lines":      []string{"user ana@example.com logged in"},
		"long_bytes": 1000,
	}); err != nil {
		t.Fatalf("stderr tool: %v", err)
	}
	waitLog := func() []logRecord {
		for deadline := time.Now().Add(5 * time.Second); time.Now().Before(deadline); time.Sleep(10 * time.Millisecond) {
			recs := logs()
			for _, r := range recs {
				if r.attrs["line"] == "stderr-done" {
					return recs
				}
			}
		}
		t.Fatal("stderr-done never logged")
		return nil
	}
	sawEmail := false
	for _, r := range waitLog() {
		if r.msg != "mcp stdio stderr" {
			continue
		}
		if strings.Contains(r.attrs["line"], "ana@example.com") {
			t.Errorf("operator pattern not applied: %s", r)
		}
		if strings.Contains(r.attrs["line"], "[REDACTED:email]") {
			sawEmail = true
		}
		if len(r.attrs["line"]) > 64 {
			t.Errorf("line of %d bytes over the 64-byte cap", len(r.attrs["line"]))
		}
	}
	if !sawEmail {
		t.Error("no redacted email line logged")
	}
	if got := c.StderrLinesCut(); got != 1 {
		t.Errorf("StderrLinesCut = %d, want 1", got)
	}
}
