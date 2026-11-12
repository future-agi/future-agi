package providers

import (
	"net"
	"syscall"
	"testing"
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
