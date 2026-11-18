package server

import (
	"bytes"
	"context"
	"encoding/binary"
	"fmt"
	"io"
	"net"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"golang.org/x/net/http2"
	"golang.org/x/net/http2/hpack"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/connectivity"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/stats"
	"google.golang.org/grpc/status"
)

// At the connection cap, shutting down must not wait for clients to hang up.
// grpc-go's GracefulStop waits for Serve to return before it drains the open
// connections, so a capped listener's Accept has to give up as soon as the
// listener is closed, even while every slot is taken.
func TestShutdownAtTheConnectionCap(t *testing.T) {
	const capConns = 2
	addr := testTCPAddr(t)
	s := New(Config{GRPCAddr: addr, MaxConnections: capConns}, nil, nil, nil, nil)
	s.cfg.HTTPAddr = ""
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() { done <- s.Run(ctx) }()
	if !waitPort(addr, 2*time.Second) {
		cancel()
		t.Fatalf("server didn't listen on %s", addr)
	}

	// capConns connections take every slot; one more makes Serve wait in
	// Accept.
	for i := 0; i < capConns+1; i++ {
		conn, err := grpc.NewClient(addr, grpc.WithTransportCredentials(insecure.NewCredentials()))
		if err != nil {
			t.Fatal(err)
		}
		t.Cleanup(func() { conn.Close() })
		conn.Connect()
		if i < capConns {
			wctx, wcancel := context.WithTimeout(context.Background(), 5*time.Second)
			for st := conn.GetState(); st != connectivity.Ready; st = conn.GetState() {
				if !conn.WaitForStateChange(wctx, st) {
					wcancel()
					t.Fatalf("connection %d never became ready", i)
				}
			}
			wcancel()
		}
	}
	time.Sleep(100 * time.Millisecond)

	cancel()
	select {
	case <-done:
	case <-time.After(10 * time.Second):
		t.Fatal("Run did not return within 10 s of shutdown with the connection cap reached")
	}
}

// readyConn is a gRPC client connection to addr that has connected.
func readyConn(t *testing.T, addr string) *grpc.ClientConn {
	t.Helper()
	conn, err := grpc.NewClient(addr, grpc.WithTransportCredentials(insecure.NewCredentials()))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { conn.Close() })
	conn.Connect()
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	for st := conn.GetState(); st != connectivity.Ready; st = conn.GetState() {
		if !conn.WaitForStateChange(ctx, st) {
			t.Fatalf("connection never became ready (state %v)", st)
		}
	}
	return conn
}

// Past FI_OTLP_MAX_CONNECTIONS, a new connection is closed at once, so its
// client gets an error it retries instead of waiting in the accept queue
// until its deadline. (Before, it waited to be accepted for as long as the
// open connections stayed open.)
func TestConnectionPastTheCapIsClosedAtOnce(t *testing.T) {
	s, _, _ := startGRPCServer(t, Config{MaxConnections: 1}, nil)
	first := readyConn(t, s.cfg.GRPCAddr)
	if err := exportRaw(first, context.Background(), emptySpans(t, 1)); err != nil {
		t.Fatalf("Export on the first connection: %v", err)
	}
	second, err := grpc.NewClient(s.cfg.GRPCAddr, grpc.WithTransportCredentials(insecure.NewCredentials()))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { second.Close() })
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	start := time.Now()
	err = exportRaw(second, ctx, emptySpans(t, 1))
	if status.Code(err) != codes.Unavailable {
		t.Fatalf("Export past the connection cap: %v, want UNAVAILABLE", err)
	}
	if waited := time.Since(start); waited > 2*time.Second {
		t.Errorf("refused after %v, want at once", waited)
	}
	if got := s.grpcConnsRefused.Load(); got == 0 {
		t.Error("the refused connection was not counted")
	}
}

// A gRPC connection with no call in flight is closed after the idle time,
// and its slot under the connection cap is free for another client; the
// idle client reconnects when it next calls. (Before, an idle connection
// kept its slot for as long as its client kept it open.)
func TestIdleGRPCConnectionIsClosedAndItsSlotReused(t *testing.T) {
	s, _, _ := startGRPCServer(t, Config{MaxConnections: 1}, func(s *Server) { s.grpcIdle = 300 * time.Millisecond })
	idle := readyConn(t, s.cfg.GRPCAddr)
	if err := exportRaw(idle, context.Background(), emptySpans(t, 1)); err != nil {
		t.Fatalf("Export: %v", err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	for st := idle.GetState(); st == connectivity.Ready; st = idle.GetState() {
		if !idle.WaitForStateChange(ctx, st) {
			t.Fatal("the idle connection was never closed")
		}
	}
	other := readyConn(t, s.cfg.GRPCAddr)
	if err := exportRaw(other, context.Background(), emptySpans(t, 1)); err != nil {
		t.Fatalf("Export on another client after the idle one was closed: %v", err)
	}
	other.Close()
	// Close on the client does not synchronously free the server's slot.
	// Wait for reconnect readiness within the deadline, not for one immediate
	// connection attempt to win that race.
	body := emptySpans(t, 1)
	var response []byte
	if err := idle.Invoke(ctx, exportMethod, &body, &response, grpc.ForceCodec(rawProtoCodec{}), grpc.WaitForReady(true)); err != nil {
		t.Fatalf("the idle client's next Export: %v", err)
	}
}

// A connection that never finishes its HTTP/2 handshake is closed after the
// handshake timeout, and so is one that finishes it and then sends nothing,
// after the idle time (plus grpc-go's 5 s wait for the client to answer the
// GOAWAY's ping, which this client never does).
func TestGRPCConnectionsThatSendNothingAreClosed(t *testing.T) {
	s, _, _ := startGRPCServer(t, Config{}, func(s *Server) {
		s.grpcHandshake = 300 * time.Millisecond
		s.grpcIdle = 300 * time.Millisecond
	})
	for name, preface := range map[string]string{"no preface": "", "preface only": http2ClientPreface + "\x00\x00\x00\x04\x00\x00\x00\x00\x00"} {
		t.Run(name, func(t *testing.T) {
			c, err := net.Dial("tcp", s.cfg.GRPCAddr)
			if err != nil {
				t.Fatal(err)
			}
			defer c.Close()
			if preface != "" {
				if _, err := c.Write([]byte(preface)); err != nil {
					t.Fatal(err)
				}
			}
			_ = c.SetReadDeadline(time.Now().Add(15 * time.Second))
			start := time.Now()
			_, err = io.Copy(io.Discard, c) // until the server closes it
			if err != nil {
				t.Fatalf("connection not closed by the server: %v", err)
			}
			if waited := time.Since(start); waited > 8*time.Second {
				t.Errorf("closed after %v", waited)
			}
		})
	}
}

// h2Call is one Export call written frame by frame on a raw HTTP/2
// connection, so a test can choose its DATA frames.
type h2Call struct {
	t       *testing.T
	c       net.Conn
	fr      *http2.Framer
	mu      sync.Mutex
	cond    *sync.Cond
	connW   int64
	streamW int64
	dead    error
}

func dialH2Call(t *testing.T, addr string, md map[string]string) *h2Call {
	t.Helper()
	c, err := net.Dial("tcp", addr)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { c.Close() })
	h := &h2Call{t: t, c: c, fr: http2.NewFramer(c, c), connW: 65535, streamW: 65535}
	h.cond = sync.NewCond(&h.mu)
	if _, err := c.Write([]byte(http2.ClientPreface)); err != nil {
		t.Fatal(err)
	}
	if err := h.fr.WriteSettings(); err != nil {
		t.Fatal(err)
	}
	go h.readLoop()
	var hb bytes.Buffer
	enc := hpack.NewEncoder(&hb)
	fields := [][2]string{{":method", "POST"}, {":scheme", "http"}, {":path", exportMethod}, {":authority", addr},
		{"content-type", "application/grpc"}, {"te", "trailers"}}
	for k, v := range md {
		fields = append(fields, [2]string{k, v})
	}
	for _, kv := range fields {
		_ = enc.WriteField(hpack.HeaderField{Name: kv[0], Value: kv[1]})
	}
	h.mu.Lock()
	err = h.fr.WriteHeaders(http2.HeadersFrameParam{StreamID: 1, BlockFragment: hb.Bytes(), EndHeaders: true})
	h.mu.Unlock()
	if err != nil {
		t.Fatal(err)
	}
	return h
}

func (h *h2Call) readLoop() {
	rf := http2.NewFramer(nil, h.c)
	for {
		f, err := rf.ReadFrame()
		h.mu.Lock()
		if err != nil {
			h.dead = err
			h.cond.Broadcast()
			h.mu.Unlock()
			return
		}
		switch f := f.(type) {
		case *http2.SettingsFrame:
			if !f.IsAck() {
				if v, ok := f.Value(http2.SettingInitialWindowSize); ok {
					h.streamW += int64(v) - 65535
				}
				_ = h.fr.WriteSettingsAck()
			}
		case *http2.WindowUpdateFrame:
			if f.StreamID == 0 {
				h.connW += int64(f.Increment)
			} else {
				h.streamW += int64(f.Increment)
			}
		case *http2.HeadersFrame:
			if f.StreamEnded() {
				h.dead = fmt.Errorf("stream ended")
			}
		case *http2.GoAwayFrame:
			h.dead = fmt.Errorf("GOAWAY %v", f.ErrCode)
		case *http2.RSTStreamFrame:
			h.dead = fmt.Errorf("RST_STREAM %v", f.ErrCode)
		}
		h.cond.Broadcast()
		h.mu.Unlock()
	}
}

// send writes msg as one gRPC message, with the compressed flag as given,
// in DATA frames of frame bytes, as flow control allows. It returns the
// error that ended the stream or connection, if one did.
func (h *h2Call) send(msg []byte, compressed bool, frame int) error {
	payload := make([]byte, 5+len(msg))
	if compressed {
		payload[0] = 1
	}
	binary.BigEndian.PutUint32(payload[1:5], uint32(len(msg)))
	copy(payload[5:], msg)
	for i := 0; i < len(payload); i += frame {
		end := min(i+frame, len(payload))
		n := int64(end - i)
		h.mu.Lock()
		for h.dead == nil && (h.connW < n || h.streamW < n) {
			h.cond.Wait()
		}
		if h.dead != nil {
			err := h.dead
			h.mu.Unlock()
			return err
		}
		h.connW -= n
		h.streamW -= n
		err := h.fr.WriteData(1, end == len(payload), payload[i:end])
		h.mu.Unlock()
		if err != nil {
			return err
		}
	}
	return nil
}

// ended waits for the server to end the stream or the connection.
func (h *h2Call) ended(d time.Duration) error {
	timer := time.AfterFunc(d, func() { h.mu.Lock(); h.cond.Broadcast(); h.mu.Unlock() })
	defer timer.Stop()
	deadline := time.Now().Add(d)
	h.mu.Lock()
	defer h.mu.Unlock()
	for h.dead == nil && time.Now().Before(deadline) {
		h.cond.Wait()
	}
	return h.dead
}

// A connection whose stream sends many tiny DATA frames is closed by
// frameGuardConn, independent of its credentials.
func TestGRPCConnectionSendingTinyDataFramesIsClosed(t *testing.T) {
	var allow atomic.Bool
	s, _, _ := startGRPCServer(t, Config{}, func(s *Server) {
		s.requireCreds = true
		s.grpcKnown = func(metadata.MD) bool { return false }
		s.grpcAuth = func(ctx context.Context) (context.Context, error) {
			if allow.Load() {
				return ctx, nil
			}
			<-ctx.Done() // never decides: the frames queue while it waits
			return nil, ctx.Err()
		}
	})
	h := dialH2Call(t, s.cfg.GRPCAddr, map[string]string{"x-api-key": "k", "x-secret-key": "s"})
	err := h.send(emptySpans(t, 1<<12), false, 1)
	if err == nil {
		err = h.ended(2 * time.Second)
	}
	if err == nil {
		t.Fatal("a connection sending 1-byte DATA frames was not closed")
	}
	if got := s.smallFrameConns.Load(); got != 1 {
		t.Errorf("connections closed for small frames: %d, want 1", got)
	}
	// Stock clients (16 KiB frames) are unaffected.
	conn := readyConn(t, s.cfg.GRPCAddr)
	allow.Store(true)
	if err := exportRaw(conn, withKeys(context.Background()), tracesOfSize(t, "stock", 1<<20)); err != nil {
		t.Fatalf("stock client Export: %v", err)
	}
}

// frameGuardConn's accounting, frame by frame: each stream may send a few
// small DATA frames, plus one per smallFrameEvery bytes it has sent; a
// stream that ends gives its credit back.
func TestFrameGuardCounts(t *testing.T) {
	frame := func(typ, flags byte, id uint32, payload int) []byte {
		b := []byte{byte(payload >> 16), byte(payload >> 8), byte(payload), typ, flags,
			byte(id >> 24), byte(id >> 16), byte(id >> 8), byte(id)}
		return append(b, make([]byte, payload)...)
	}
	newGuard := func() *frameGuardConn {
		return &frameGuardConn{preface: len(http2ClientPreface), streams: map[uint32]int64{}}
	}
	feed := func(g *frameGuardConn, frames ...[]byte) error {
		b := []byte(http2ClientPreface)
		for _, f := range frames {
			b = append(b, f...)
		}
		// One byte at a time: frame headers may arrive split.
		for i := range b {
			if err := g.observe(b[i : i+1]); err != nil {
				return err
			}
		}
		return nil
	}
	small := frame(http2FrameData, 0, 1, 1)
	var frames [][]byte
	for i := 0; i < smallFrameCredit; i++ {
		frames = append(frames, small)
	}
	if err := feed(newGuard(), frames...); err != nil {
		t.Errorf("%d small frames: %v, want allowed", smallFrameCredit, err)
	}
	if err := feed(newGuard(), append(frames, small)...); err == nil {
		t.Errorf("%d small frames: allowed, want refused", smallFrameCredit+1)
	}
	if err := feed(newGuard(), append(frames, frame(http2FrameData, 0, 1, smallFrameEvery), small)...); err != nil {
		t.Errorf("a small frame after a full one: %v, want allowed", err)
	}
	if err := feed(newGuard(), append(frames, frame(http2FrameData, http2FlagEndStream, 1, 1), frame(http2FrameData, 0, 3, 1))...); err != nil {
		t.Errorf("a small frame on a new stream after the first ended: %v, want allowed", err)
	}
	padded := frame(http2FrameData, http2FlagPadded, 1, smallDataFrame)
	var pads [][]byte
	for i := 0; i <= smallFrameCredit; i++ {
		pads = append(pads, padded)
	}
	if err := feed(newGuard(), pads...); err == nil {
		t.Error("padded frames: allowed, want counted as small")
	}
	big := frame(http2FrameData, 0, 1, smallDataFrame)
	var bigs [][]byte
	for i := 0; i < 4*smallFrameCredit; i++ {
		bigs = append(bigs, big)
	}
	if err := feed(newGuard(), bigs...); err != nil {
		t.Errorf("1 KiB frames: %v, want allowed", err)
	}
}

// Refused call contexts end with their connection; after enough unverified
// tap refusals the connection is retired. Known-client capacity refusals are
// covered separately by TestAuthenticatedBusyRefusalsKeepAcceptedCall.
func TestGRPCRefusedCallsAreReleasedWithTheirConnection(t *testing.T) {
	t.Run("connection context", func(t *testing.T) {
		h := grpcConnTracker{s: New(Config{}, nil, nil, nil, nil)}
		ctx := h.TagConn(context.Background(), &stats.ConnTagInfo{RemoteAddr: &net.TCPAddr{}})
		call, cancel := context.WithTimeout(ctx, time.Hour) // as grpc-go makes a call's context
		defer cancel()
		h.HandleConn(ctx, &stats.ConnEnd{})
		select {
		case <-call.Done():
		case <-time.After(time.Second):
			t.Fatal("a call's context outlived its connection")
		}
	})
	t.Run("closed after many refusals", func(t *testing.T) {
		var allow atomic.Bool
		s, conn, _ := startGRPCServer(t, Config{}, func(s *Server) {
			s.requireCreds = true
			s.grpcAuth = func(ctx context.Context) (context.Context, error) {
				if allow.Load() {
					return ctx, nil
				}
				return nil, status.Error(codes.Unauthenticated, "authentication failed")
			}
		})
		for i := 0; i < grpcMaxTapRefusals+8; i++ {
			ctx, cancel := context.WithTimeout(context.Background(), time.Hour)
			err := exportRaw(conn, ctx, emptySpans(t, 1))
			cancel()
			if c := status.Code(err); c != codes.Unauthenticated && c != codes.Unavailable {
				t.Fatalf("call %d without keys: %v", i, err)
			}
		}
		if got := s.IngestStats().ConnectionsClosed["grpc_refused_calls"]; got != 1 {
			t.Errorf("connections closed for refused calls: %d, want 1", got)
		}
		allow.Store(true)
		if err := exportRaw(conn, withKeys(context.Background()), emptySpans(t, 1)); err != nil {
			t.Errorf("the client's next call, on a new connection: %v", err)
		}
	})
}

// delayProxy forwards TCP connections to target, delaying each direction by
// oneWay.
func delayProxy(t *testing.T, target string, oneWay time.Duration) string {
	t.Helper()
	l, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { l.Close() })
	pipe := func(dst, src net.Conn) {
		type chunk struct {
			at time.Time
			b  []byte
		}
		ch := make(chan chunk, 1<<12)
		go func() {
			defer close(ch)
			for {
				buf := make([]byte, 32<<10)
				n, err := src.Read(buf)
				if n > 0 {
					ch <- chunk{time.Now(), buf[:n]}
				}
				if err != nil {
					return
				}
			}
		}()
		for c := range ch {
			time.Sleep(time.Until(c.at.Add(oneWay)))
			if _, err := dst.Write(c.b); err != nil {
				break
			}
		}
		dst.Close()
	}
	go func() {
		for {
			c, err := l.Accept()
			if err != nil {
				return
			}
			s, err := net.Dial("tcp", target)
			if err != nil {
				c.Close()
				continue
			}
			go pipe(s, c)
			go pipe(c, s)
		}
	}()
	return l.Addr().String()
}

// A large message is not slowed by round trips: the connection window holds
// a message at the receive cap, and a stream's window opens to the message
// once its handler reads it. (Before, a 1 MiB connection window let at most
// 1 MiB through per round trip: 15 MiB at 150 ms RTT took more than 2 s.)
func TestGRPCLargeMessageIsNotSlowedByRoundTrips(t *testing.T) {
	if raceEnabled || testing.Short() {
		t.Skip("times a 15 MiB call")
	}
	s, _, _ := startGRPCServer(t, Config{}, nil)
	conn, err := grpc.NewClient(delayProxy(t, s.cfg.GRPCAddr, 75*time.Millisecond),
		grpc.WithTransportCredentials(insecure.NewCredentials()),
		grpc.WithDefaultCallOptions(grpc.MaxCallSendMsgSize(64<<20)))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { conn.Close() })
	if err := exportRaw(conn, context.Background(), emptySpans(t, 1)); err != nil { // connect
		t.Fatal(err)
	}
	body := tracesOfSize(t, "big", 15<<20)
	start := time.Now()
	if err := exportRaw(conn, context.Background(), body); err != nil {
		t.Fatalf("Export: %v", err)
	}
	if took := time.Since(start); took > 1500*time.Millisecond {
		t.Errorf("a 15 MiB Export at 150 ms RTT took %v, want a few round trips", took)
	}
}
