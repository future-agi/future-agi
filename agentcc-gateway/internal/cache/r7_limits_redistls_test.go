package cache

// R7-LIMITS (NG-46): a cache Redis with tls: true never talks plaintext. The
// probe (no-gimmick gateway-b2, G10) saw tls: true with mode: cluster dial
// one node in plaintext and send AUTH with the password in clear. Plaintext
// listeners stand in for the nodes and record every byte the gateway sends:
// they must see a TLS ClientHello (first byte 0x16) or nothing, and never the
// password.

import (
	"bytes"
	"net"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

const r7TLSPassword = "r7-limits-cache-secret-password"

// r7PlainNode is a plaintext TCP listener that answers every read with a
// RESP "+OK" (as a plaintext Redis would accept AUTH) and records all bytes.
type r7PlainNode struct {
	ln    net.Listener
	mu    sync.Mutex
	got   []byte
	dials int
}

func startR7PlainNode(t *testing.T) *r7PlainNode {
	t.Helper()
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	n := &r7PlainNode{ln: ln}
	go func() {
		for {
			c, err := ln.Accept()
			if err != nil {
				return
			}
			n.mu.Lock()
			n.dials++
			n.mu.Unlock()
			go func(c net.Conn) {
				defer c.Close()
				buf := make([]byte, 4096)
				for {
					_ = c.SetReadDeadline(time.Now().Add(3 * time.Second))
					k, err := c.Read(buf)
					if k > 0 {
						n.mu.Lock()
						n.got = append(n.got, buf[:k]...)
						n.mu.Unlock()
						_, _ = c.Write([]byte("+OK\r\n"))
					}
					if err != nil {
						return
					}
				}
			}(c)
		}
	}()
	t.Cleanup(func() { _ = ln.Close() })
	return n
}

func (n *r7PlainNode) addr() string { return n.ln.Addr().String() }

func (n *r7PlainNode) bytes() []byte {
	n.mu.Lock()
	defer n.mu.Unlock()
	return append([]byte(nil), n.got...)
}

// r7AssertNoPlaintext fails when a node saw anything but a TLS handshake, or
// the password in any form.
func r7AssertNoPlaintext(t *testing.T, what string, nodes ...*r7PlainNode) {
	t.Helper()
	for i, n := range nodes {
		b := n.bytes()
		if bytes.Contains(b, []byte(r7TLSPassword)) {
			t.Errorf("%s: node %d received the password in plaintext: %q", what, i+1, b)
		}
		if bytes.Contains(bytes.ToUpper(b), []byte("AUTH")) || bytes.Contains(bytes.ToUpper(b), []byte("PING")) {
			t.Errorf("%s: node %d received a plaintext Redis command: %q", what, i+1, b)
		}
		if len(b) > 0 && b[0] != 0x16 {
			t.Errorf("%s: node %d's first byte is %#x, want a TLS ClientHello (0x16): %q", what, i+1, b[0], b)
		}
	}
}

func r7TLSCache(mode, address string, addresses ...string) config.CacheConfig {
	return config.CacheConfig{Enabled: true, Backend: "redis", Redis: config.RedisCacheConfig{
		Address:   address,
		Addresses: addresses,
		Password:  r7TLSPassword,
		Mode:      mode,
		TLS:       true,
		Timeout:   time.Second,
	}}
}

// The probe's configuration: tls: true, mode: cluster, two addresses.
func TestR7RedisTLSClusterNeverPlaintext(t *testing.T) {
	a, b := startR7PlainNode(t), startR7PlainNode(t)
	be, err := NewBackend(r7TLSCache("cluster", "", a.addr(), b.addr()))
	if err == nil {
		t.Errorf("NewBackend succeeded against plaintext nodes with tls: true (backend %T)", be)
	}
	time.Sleep(100 * time.Millisecond)
	r7AssertNoPlaintext(t, "tls cluster", a, b)
	if len(a.bytes()) == 0 && len(b.bytes()) == 0 {
		t.Errorf("no node was dialled: tls: true must still try TLS on the cluster's addresses")
	}
}

func TestR7RedisTLSSingleNeverPlaintext(t *testing.T) {
	a := startR7PlainNode(t)
	_, err := NewBackend(r7TLSCache("", a.addr()))
	if err == nil {
		t.Errorf("NewBackend succeeded against a plaintext node with tls: true")
	} else if !strings.Contains(strings.ToLower(err.Error()), "tls") {
		t.Errorf("error %q does not say TLS failed", err)
	}
	time.Sleep(100 * time.Millisecond)
	r7AssertNoPlaintext(t, "tls single", a)
	if len(a.bytes()) == 0 {
		t.Errorf("the node was not dialled")
	}
}

func TestR7RedisTLSSentinelNeverPlaintext(t *testing.T) {
	s1, s2 := startR7PlainNode(t), startR7PlainNode(t)
	_, err := NewBackend(r7TLSCache("sentinel", "mymaster", s1.addr(), s2.addr()))
	if err == nil {
		t.Errorf("NewBackend succeeded against plaintext sentinels with tls: true")
	}
	time.Sleep(100 * time.Millisecond)
	r7AssertNoPlaintext(t, "tls sentinel", s1, s2)
}
