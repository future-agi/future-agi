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
	"sync"
	"sync/atomic"
	"syscall"
	"time"
	"unicode/utf8"

	"github.com/futureagi/agentcc-gateway/internal/mcp/contentpolicy"
	"github.com/futureagi/agentcc-gateway/internal/privacy"
)

// Defaults of the mcp.stdio keys.
const (
	DefaultStdioGrace              = 5 * time.Second
	DefaultStdioMaxMessageBytes    = 16 << 20
	DefaultStdioStderrMaxLineBytes = 1024

	// stdioTermWait is how long Close waits after SIGTERM before SIGKILL.
	stdioTermWait = 5 * time.Second
	// stdioKillWait bounds the wait for a SIGKILLed child to be reaped.
	stdioKillWait = 5 * time.Second
	// stdioDrainWait bounds the wait for the pipe readers once the process
	// group is gone (a process outside it may still hold a pipe open).
	stdioDrainWait = time.Second
	// stderrRedactWindow is how much of one stderr line is read, and
	// redacted whole, before it is cut to the cap: a credential that starts
	// before the cap is matched in full, not as a fragment the cut left.
	stderrRedactWindow = 64 << 10
)

var (
	// ErrUpstreamMessageTooLarge fails every pending call when a stdio server
	// writes a message over the transport's limit.
	ErrUpstreamMessageTooLarge = errors.New("upstream_message_too_large")
	// ErrDuplicateRequestID refuses a request whose id is already pending.
	ErrDuplicateRequestID = errors.New("duplicate request id")

	errStdioTransportClosed = errors.New("transport closed")
	errStdioUpstreamExited  = errors.New("stdio upstream exited")
	// errStdinWriteCut stops a transport whose stdin write ended part-way:
	// the child has half a message, so the framing is broken.
	errStdinWriteCut = errors.New("stdio write to the server's stdin ended part-way")
)

// stderrMarker is what replaces a credential in a stdio server's stderr log
// line (the shared patterns, privacy.CredentialPatterns).
func stderrMarker(name string) string { return "[REDACTED:" + name + "]" }

// stdioOptions configure one stdio transport.
type stdioOptions struct {
	launcher        StdioLauncher
	grace           time.Duration // after stdin closes, before SIGTERM
	termWait        time.Duration // after SIGTERM, before SIGKILL
	maxMessageBytes int
	stderrMaxLine   int
	stderrRedactor  *privacy.Redactor // the operator's redactor, after the shared credential patterns
	stderrCut       *atomic.Int64     // lines cut to stderrMaxLine, kept per server
	onDead          func(error)       // the transport stopped on its own; runs before pending calls fail
}

func defaultStdioOptions() stdioOptions {
	return stdioOptions{
		launcher:        LocalStdioLauncher{},
		grace:           DefaultStdioGrace,
		termWait:        stdioTermWait,
		maxMessageBytes: DefaultStdioMaxMessageBytes,
		stderrMaxLine:   DefaultStdioStderrMaxLineBytes,
		stderrCut:       new(atomic.Int64),
	}
}

type stdioResult struct {
	msg *Message
	err error
}

// StdioTransport communicates with an MCP server launched as a subprocess,
// one JSON-RPC message per line.
type StdioTransport struct {
	spec StdioSpec
	opts stdioOptions
	proc StdioProcess

	writeMu sync.Mutex
	pendMu  sync.Mutex
	pending map[string]chan stdioResult // request id -> waiting caller
	deadErr error                       // why the transport stopped; set once, under pendMu

	healthy    atomic.Bool
	closed     atomic.Bool
	readDone   chan struct{}
	stderrDone chan struct{}
}

// NewStdioTransport creates a transport that launches command with the
// default options: the local launcher and the default base environment.
func NewStdioTransport(command string, args []string) *StdioTransport {
	spec := StdioSpec{Command: command, Args: args, Env: buildStdioEnv(DefaultStdioBaseEnv(), nil, nil)}
	return newStdioTransport(spec, defaultStdioOptions())
}

func newStdioTransport(spec StdioSpec, opts stdioOptions) *StdioTransport {
	return &StdioTransport{
		spec:       spec,
		opts:       opts,
		pending:    make(map[string]chan stdioResult),
		readDone:   make(chan struct{}),
		stderrDone: make(chan struct{}),
	}
}

// Start launches the subprocess. ctx bounds the start only: the child keeps
// running after ctx ends, until Close.
func (t *StdioTransport) Start(ctx context.Context) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	proc, err := t.opts.launcher.Launch(t.spec)
	if err != nil {
		return err
	}
	t.proc = proc
	t.healthy.Store(true)
	go t.readStderr()
	go t.readLoop()
	return nil
}

// Err is why the transport stopped, or nil while it runs.
func (t *StdioTransport) Err() error {
	t.pendMu.Lock()
	defer t.pendMu.Unlock()
	return t.deadErr
}

func (t *StdioTransport) readLoop() {
	defer close(t.readDone)
	scanner := bufio.NewScanner(t.proc.Stdout())
	// The buffer may grow to one byte more than the limit, for a maximal
	// line's newline; it starts no larger, since its capacity also caps a line.
	bufMax := t.opts.maxMessageBytes + 1
	scanner.Buffer(make([]byte, 0, min(64*1024, bufMax)), bufMax)
	for scanner.Scan() {
		line := scanner.Bytes()
		if len(bytes.TrimSpace(line)) == 0 {
			continue
		}
		if err := contentpolicy.Envelope(line, int64(t.opts.maxMessageBytes)); err != nil {
			t.stop(err)
			return
		}
		var msg Message
		if err := json.Unmarshal(line, &msg); err != nil {
			slog.Warn("mcp stdio: invalid JSON from server", "server", t.spec.ServerID, "error", err)
			continue
		}
		// Requests and notifications from the server are not handled yet.
		if msg.IsResponse() {
			t.deliver(&msg)
		}
	}
	cause := scanner.Err()
	drain := cause != nil
	switch {
	case errors.Is(cause, bufio.ErrTooLong):
		cause = fmt.Errorf("%w: server %q sent a message over %d bytes", ErrUpstreamMessageTooLarge, t.spec.ServerID, t.opts.maxMessageBytes)
	case cause == nil:
		cause = errStdioUpstreamExited
	default:
		cause = fmt.Errorf("stdio read: %w", cause)
	}
	t.stop(cause)
	if drain {
		// The scanner stopped with the pipe still open (a message over the
		// limit): keep reading and discard, so a child still writing is
		// never blocked on a full pipe and can see stdin close and exit.
		_, _ = io.Copy(io.Discard, t.proc.Stdout())
	}
}

func (t *StdioTransport) deliver(msg *Message) {
	id := string(msg.ID)
	t.pendMu.Lock()
	ch, ok := t.pending[id]
	delete(t.pending, id)
	t.pendMu.Unlock()
	if ok {
		ch <- stdioResult{msg: msg}
	}
}

// stop records why the transport stopped, tells the owner (unless Close
// stopped it), and then fails every pending call with the cause.
func (t *StdioTransport) stop(cause error) {
	t.pendMu.Lock()
	if t.deadErr != nil {
		t.pendMu.Unlock()
		return
	}
	t.deadErr = cause
	t.healthy.Store(false)
	pending := t.pending
	t.pending = nil
	t.pendMu.Unlock()

	if !t.closed.Load() && t.opts.onDead != nil {
		t.opts.onDead(cause)
	}
	for _, ch := range pending {
		ch <- stdioResult{err: cause}
	}
}

func (t *StdioTransport) Send(ctx context.Context, msg *Message) (*Message, error) {
	if t.closed.Load() {
		return nil, errStdioTransportClosed
	}
	data, err := json.Marshal(msg)
	if err != nil {
		return nil, fmt.Errorf("marshal: %w", err)
	}
	data = append(data, '\n')

	if msg.ID == nil { // a notification: write and return
		if err := t.Err(); err != nil {
			return nil, err
		}
		return nil, t.write(ctx, data)
	}

	id := string(msg.ID)
	ch := make(chan stdioResult, 1)
	t.pendMu.Lock()
	if t.deadErr != nil {
		t.pendMu.Unlock()
		return nil, t.deadErr
	}
	if _, dup := t.pending[id]; dup {
		t.pendMu.Unlock()
		return nil, fmt.Errorf("%w: %s is already pending", ErrDuplicateRequestID, id)
	}
	t.pending[id] = ch
	t.pendMu.Unlock()

	if err := t.write(ctx, data); err != nil {
		t.forget(id)
		return nil, fmt.Errorf("write: %w", err)
	}
	notifyDispatched(ctx) // the request is on the child's stdin
	select {
	case res := <-ch:
		return res.msg, res.err
	case <-ctx.Done():
		t.forget(id)
		return nil, ctx.Err()
	}
}

// write sends one framed message to the child's stdin, bounded by ctx: the
// pipe's write deadline is ctx's, and moves to now when ctx ends first, so a
// child that has stopped reading cannot hold the caller past its context. A
// write cut short stops the transport.
func (t *StdioTransport) write(ctx context.Context, data []byte) error {
	t.writeMu.Lock()
	defer t.writeMu.Unlock()
	stdin := t.proc.Stdin()
	if dl, ok := stdin.(interface{ SetWriteDeadline(time.Time) error }); ok {
		if d, has := ctx.Deadline(); has {
			_ = dl.SetWriteDeadline(d)
		}
		stop := context.AfterFunc(ctx, func() { _ = dl.SetWriteDeadline(time.Now()) })
		defer func() {
			stop()
			_ = dl.SetWriteDeadline(time.Time{})
		}()
	}
	n, err := stdin.Write(data)
	if err != nil && n > 0 {
		t.stop(fmt.Errorf("%w: %w", errStdinWriteCut, err))
	}
	if err != nil && ctx.Err() != nil {
		return ctx.Err()
	}
	return err
}

func (t *StdioTransport) forget(id string) {
	t.pendMu.Lock()
	delete(t.pending, id)
	t.pendMu.Unlock()
}

// Close fails every pending call and shuts the child down: close stdin, wait
// the grace period, SIGTERM the process group, wait again, then SIGKILL it.
func (t *StdioTransport) Close() error {
	if t.closed.Swap(true) {
		return nil
	}
	t.stop(errStdioTransportClosed)
	if t.proc == nil {
		return nil
	}
	t.shutdown()
	return nil
}

func (t *StdioTransport) shutdown() {
	p := t.proc
	p.Stdin().Close()
	if !waitClosed(p.Exited(), t.opts.grace) {
		slog.Info("mcp stdio: server still running after stdin closed; sending SIGTERM",
			"server", t.spec.ServerID, "grace", t.opts.grace)
		p.Signal(syscall.SIGTERM)
		if !waitClosed(p.Exited(), t.opts.termWait) {
			slog.Warn("mcp stdio: server ignored SIGTERM; sending SIGKILL", "server", t.spec.ServerID)
			p.Signal(syscall.SIGKILL)
			if !waitClosed(p.Exited(), stdioKillWait) {
				slog.Error("mcp stdio: server not reaped after SIGKILL", "server", t.spec.ServerID, "pid", p.Pid())
			}
		}
	}
	// Whatever is left in the child's group (a server that npx or uvx
	// started, say) is an orphan now. The group is signalled moments after
	// its leader is reaped, so its id has not been reused.
	p.Signal(syscall.SIGKILL)
	waitClosed(t.readDone, stdioDrainWait)
	waitClosed(t.stderrDone, stdioDrainWait)
	p.Release()
}

func waitClosed(ch <-chan struct{}, d time.Duration) bool {
	timer := time.NewTimer(d)
	defer timer.Stop()
	select {
	case <-ch:
		return true
	case <-timer.C:
		return false
	}
}

func (t *StdioTransport) Healthy() bool {
	return t.healthy.Load()
}

// readStderr logs the server's stderr at Debug, one line at a time: each is
// read up to the redaction window, redacted whole, and only then cut to the
// cap; the rest of a line past the window is discarded.
func (t *StdioTransport) readStderr() {
	defer close(t.stderrDone)
	window := max(t.opts.stderrMaxLine, stderrRedactWindow)
	r := bufio.NewReaderSize(t.proc.Stderr(), 4096)
	for {
		line, cut, err := readCappedLine(r, window)
		if len(line) > 0 || cut {
			t.logStderr(line, cut)
		}
		if err != nil {
			return
		}
	}
}

func (t *StdioTransport) logStderr(line []byte, cut bool) {
	text := string(line)
	logged := slog.Default().Enabled(context.Background(), slog.LevelDebug)
	if logged {
		text = privacy.RedactCredentials(text, stderrMarker)
		if t.opts.stderrRedactor != nil {
			text = t.opts.stderrRedactor.Redact(text)
		}
	}
	if len(text) > t.opts.stderrMaxLine {
		text = truncateUTF8(text, t.opts.stderrMaxLine)
		cut = true
	}
	if cut {
		t.opts.stderrCut.Add(1)
	}
	if logged {
		slog.Debug("mcp stdio stderr", "server", t.spec.ServerID, "line", text, "cut", cut)
	}
}

// readCappedLine reads one line and keeps at most max bytes of it; cut
// reports that the rest was discarded.
func readCappedLine(r *bufio.Reader, max int) (line []byte, cut bool, err error) {
	for {
		frag, isPrefix, err := r.ReadLine()
		if room := max - len(line); room < len(frag) {
			line = append(line, frag[:room]...)
			cut = true
		} else {
			line = append(line, frag...)
		}
		if err != nil || !isPrefix {
			return line, cut, err
		}
	}
}

// truncateUTF8 cuts s to at most n bytes without splitting a rune.
func truncateUTF8(s string, n int) string {
	if len(s) <= n {
		return s
	}
	for n > 0 && !utf8.RuneStart(s[n]) {
		n--
	}
	return s[:n]
}
