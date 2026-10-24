package main

import (
	"net"
	"os"
	"runtime"
	"strconv"
	"strings"
	"testing"
)

// A test starts a gateway on the port freePort picked, and the gateway binds
// that port only once it has started: a second or more later for a child
// process under -race. Until then the kernel hands the port to no other
// listener: no listener that asks for any free port (an httptest server, in
// this binary or in another package's test binary running beside it) gets
// it, so the gateway's own bind succeeds and the test talks to the gateway.
//
// The test asks the kernel for listeners on 127.0.0.1, holding each, until
// it holds a quarter of the ephemeral port range and a few more. Linux hands
// a listener that asks for any port a free one from the lower half of the
// range, one port parity first: freePort's own port is one of those, so with
// a port only picked and released the kernel hands it back before that
// quarter runs out (by the 6,802nd listener in each of 14 runs). The picked
// port must never be among them. Then a listener
// that names the port, as the gateway's does, must still bind it.
func TestFreePortIsNotHandedToAnotherListener(t *testing.T) {
	if runtime.GOOS != "linux" {
		t.Skip("pins how Linux, where the gates run, chooses a free port")
	}
	low, high := ephemeralPortRange(t)
	limit := (high-low+1)/4 + 64

	port := freePort(t)

	var held []net.Listener
	defer func() {
		for _, l := range held {
			l.Close()
		}
	}()
	var listenErr error
	for len(held) < limit {
		l, err := net.Listen("tcp", "127.0.0.1:0")
		if err != nil {
			listenErr = err
			break
		}
		held = append(held, l)
		if l.Addr().(*net.TCPAddr).Port == port {
			t.Fatalf("freePort picked port %d, and listener %d that asked the kernel for any free port was handed it: "+
				"a gateway started on that port would lose its bind to that listener", port, len(held))
		}
	}
	if len(held) < limit {
		t.Skipf("only %d of %d listeners could be held (%v): too few to show the port is kept", len(held), limit, listenErr)
	}
	for _, l := range held {
		l.Close()
	}
	held = nil

	l, err := net.Listen("tcp", "127.0.0.1:"+strconv.Itoa(port))
	if err != nil {
		t.Fatalf("a listener naming port %d, as the gateway's does, could not bind it: %v", port, err)
	}
	l.Close()
}

// ephemeralPortRange reads the range Linux picks free ports from.
func ephemeralPortRange(t *testing.T) (low, high int) {
	t.Helper()
	b, err := os.ReadFile("/proc/sys/net/ipv4/ip_local_port_range")
	if err != nil {
		t.Skipf("reading the ephemeral port range: %v", err)
	}
	f := strings.Fields(string(b))
	if len(f) != 2 {
		t.Skipf("ephemeral port range %q", b)
	}
	low, err1 := strconv.Atoi(f[0])
	high, err2 := strconv.Atoi(f[1])
	if err1 != nil || err2 != nil || high <= low {
		t.Skipf("ephemeral port range %q", b)
	}
	return low, high
}
