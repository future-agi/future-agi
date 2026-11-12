package credtemplate_test

import (
	"context"
	"errors"
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"sync/atomic"
	"syscall"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/mcp/credtemplate"
)

// refusedAddr reserves a loopback TCP port without listening. No SO_REUSEADDR
// is set: connections are refused and no listener can claim it until cleanup.
func refusedAddr(t *testing.T) string {
	t.Helper()
	syscall.ForkLock.RLock()
	fd, err := syscall.Socket(syscall.AF_INET, syscall.SOCK_STREAM, 0)
	if err == nil {
		syscall.CloseOnExec(fd)
	}
	syscall.ForkLock.RUnlock()
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		if err := syscall.Close(fd); err != nil {
			t.Error(err)
		}
	})
	if err := syscall.Bind(fd, &syscall.SockaddrInet4{Addr: [4]byte{127, 0, 0, 1}}); err != nil {
		t.Fatal(err)
	}
	sa, err := syscall.Getsockname(fd)
	if err != nil {
		t.Fatal(err)
	}
	bound, ok := sa.(*syscall.SockaddrInet4)
	if !ok {
		t.Fatalf("bound socket has address type %T, want IPv4", sa)
	}
	return (&net.TCPAddr{IP: net.IPv4(127, 0, 0, 1), Port: bound.Port}).String()
}

func TestRefusedAddrIsDeterministic(t *testing.T) {
	addr := refusedAddr(t)
	conn, err := net.Dial("tcp", addr)
	if conn != nil {
		conn.Close()
	}
	cause := err
	for cause != nil {
		wrapped, ok := cause.(interface{ Unwrap() error })
		if !ok {
			break
		}
		cause = wrapped.Unwrap()
	}
	if cause != syscall.ECONNREFUSED {
		t.Errorf("dial %s: %v, want connection refused", addr, err)
	}
	listener, err := net.Listen("tcp", addr)
	if err == nil {
		listener.Close()
		t.Fatalf("fixture address %s was reused by a listener", addr)
	}
}

// dialCutover admits normal-route dials and publishes refusal under one lock.
// refuse cancels and drains every admitted dial before its caller may release
// the original listener. A connect that beats cancellation is closed, never
// handed to the HTTP pool; the caller instead gets the real refused dial.
// Keep the package-local copy in ../refused_addr_test.go equivalent.
type dialCutover struct {
	mu       sync.Mutex
	refused  bool
	active   map[context.Context]context.CancelFunc
	draining sync.WaitGroup
}

func (d *dialCutover) refuse() {
	d.mu.Lock()
	d.refused = true
	for _, cancel := range d.active {
		cancel()
	}
	d.mu.Unlock()
	d.draining.Wait()
}

func (d *dialCutover) dial(ctx context.Context, live, refused func(context.Context) (net.Conn, error)) (net.Conn, error) {
	d.mu.Lock()
	if d.refused {
		d.mu.Unlock()
		return refused(ctx)
	}
	liveCtx, cancel := context.WithCancel(ctx)
	if d.active == nil {
		d.active = make(map[context.Context]context.CancelFunc)
	}
	d.active[liveCtx] = cancel
	// No Add can follow refuse's publication, so Wait cannot miss a dial.
	d.draining.Add(1)
	d.mu.Unlock()

	conn, err := live(liveCtx)
	cancel()
	d.mu.Lock()
	delete(d.active, liveCtx)
	stopped := d.refused
	if stopped && conn != nil {
		conn.Close()
	}
	d.mu.Unlock()
	d.draining.Done()
	if stopped {
		return refused(ctx)
	}
	return conn, err
}

type cutoverDialFunc func(context.Context, string, string) (net.Conn, error)

type cutoverObservedConn struct {
	net.Conn
	closed atomic.Bool
}

func (c *cutoverObservedConn) Close() error {
	err := c.Conn.Close()
	c.closed.Store(true)
	return err
}

// assertOverlappingCutover parks a selected normal-route dial until either
// cancellation (the barrier) or listener release (the old, unsafe cutover).
// On cancellation it deliberately completes a real connect anyway, proving
// that a late successful connection is closed before the listener is released.
func assertOverlappingCutover(t *testing.T, makeCaller func(string, string, cutoverDialFunc) (func() error, func())) {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusNoContent)
	}))
	t.Cleanup(srv.Close)
	address := strings.TrimPrefix(srv.URL, "http://")
	parked, released := make(chan struct{}), make(chan struct{})
	connected := make(chan *cutoverObservedConn, 1)
	var afterRelease atomic.Bool
	var normalDials atomic.Int32
	realDial := (&net.Dialer{}).DialContext
	dial := func(ctx context.Context, network, addr string) (net.Conn, error) {
		if addr != address {
			return realDial(ctx, network, addr)
		}
		if normalDials.Add(1) != 1 {
			afterRelease.Store(true)
			return nil, errors.New("unexpected additional original-address dial")
		}
		close(parked)
		select {
		case <-ctx.Done():
		case <-released:
		}
		select {
		case <-released:
			afterRelease.Store(true)
			// Do not actually contact a port that another test could now own.
			return nil, errors.New("normal-route dial reached the released original address")
		default:
		}
		// Model a connect that won the race with cancellation, while the
		// original listener is still held. No address rebinding is needed.
		conn, err := realDial(context.Background(), network, addr)
		if err != nil {
			return nil, err
		}
		observed := &cutoverObservedConn{Conn: conn}
		connected <- observed
		return observed, nil
	}
	call, refuse := makeCaller(address, refusedAddr(t), dial)
	result := make(chan error, 1)
	go func() { result <- call() }()
	<-parked
	refuse()
	select {
	case conn := <-connected:
		if !conn.closed.Load() {
			t.Error("cutover returned without closing the late normal-route connection")
		}
	default:
		t.Error("cutover returned without draining the parked normal-route dial")
	}
	srv.Close()
	close(released)
	if err := <-result; !errors.Is(err, syscall.ECONNREFUSED) {
		t.Errorf("overlapping dial: %v, want connection refused", err)
	}
	if err := call(); !errors.Is(err, syscall.ECONNREFUSED) {
		t.Errorf("post-cutover dial: %v, want connection refused", err)
	}
	if afterRelease.Load() {
		t.Error("a dial reached the released original address")
	}
}

func TestDisappearingTemplatedOverlappingCutover(t *testing.T) {
	assertOverlappingCutover(t, func(address, refused string, dial cutoverDialFunc) (func() error, func()) {
		rawURL := "http://" + address
		tpl, err := credtemplate.ForSecret(rawURL, credtemplate.Secret{Type: "api_key", Header: "X-Cutover-Fixture", Value: "cutover-fixture"})
		if err != nil {
			t.Fatal(err)
		}
		d := &disappearingTemplated{templated: templated{guard(t), tpl}, refused: refused, dial: dial}
		client := d.HTTPClient(0)
		t.Cleanup(client.CloseIdleConnections)
		return func() error {
			resp, err := client.Get(rawURL)
			if resp != nil {
				resp.Body.Close()
			}
			return err
		}, d.refuse
	})
}
