package cache

// The cache's Redis modes work, over TLS and without it.
// No shared Redis is touched: fakeRedis is an in-memory RESP server that
// speaks what go-redis sends (HELLO is refused as by Redis 5, so the client
// falls back to AUTH), serves GET/SET/DEL, answers CLUSTER SLOTS with one
// node for every slot, and SENTINEL get-master-addr-by-name with a master's
// address. It records every command, so a test can see where the password
// went.

import (
	"bufio"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"fmt"
	"io"
	"math/big"
	"net"
	"os"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// cacheTLS is a test CA and a server certificate it signed.
type cacheTLS struct {
	roots  *x509.CertPool
	server *tls.Config
}

// newCacheTLS signs a server certificate for ips and dnsNames with a fresh CA.
func newCacheTLS(t *testing.T, ips []net.IP, dnsNames []string) *cacheTLS {
	t.Helper()
	caKey, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	ca := &x509.Certificate{
		SerialNumber:          big.NewInt(1),
		Subject:               pkix.Name{CommonName: "cache test CA"},
		NotBefore:             time.Now().Add(-time.Hour),
		NotAfter:              time.Now().Add(time.Hour),
		IsCA:                  true,
		KeyUsage:              x509.KeyUsageCertSign,
		BasicConstraintsValid: true,
	}
	caDER, err := x509.CreateCertificate(rand.Reader, ca, ca, &caKey.PublicKey, caKey)
	if err != nil {
		t.Fatal(err)
	}
	caCert, _ := x509.ParseCertificate(caDER)
	leafKey, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	leaf := &x509.Certificate{
		SerialNumber: big.NewInt(2),
		Subject:      pkix.Name{CommonName: "cache test redis"},
		NotBefore:    time.Now().Add(-time.Hour),
		NotAfter:     time.Now().Add(time.Hour),
		KeyUsage:     x509.KeyUsageDigitalSignature,
		ExtKeyUsage:  []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth},
		IPAddresses:  ips,
		DNSNames:     dnsNames,
	}
	leafDER, err := x509.CreateCertificate(rand.Reader, leaf, caCert, &leafKey.PublicKey, caKey)
	if err != nil {
		t.Fatal(err)
	}
	roots := x509.NewCertPool()
	roots.AddCert(caCert)
	return &cacheTLS{
		roots: roots,
		server: &tls.Config{
			MinVersion:   tls.VersionTLS12,
			Certificates: []tls.Certificate{{Certificate: [][]byte{leafDER}, PrivateKey: leafKey}},
		},
	}
}

func localhostTLS(t *testing.T) *cacheTLS {
	return newCacheTLS(t, []net.IP{net.ParseIP("127.0.0.1")}, []string{"localhost"})
}

type fakeRedis struct {
	ln       net.Listener
	password string // the password AUTH must carry ("" = none configured)

	// slotsAddr answers CLUSTER SLOTS: every slot on this address.
	slotsAddr string
	// masterName and masterAddr answer SENTINEL get-master-addr-by-name.
	masterName, masterAddr string

	mu    sync.Mutex
	data  map[string]string
	cmds  [][]string
	conns []net.Conn
}

// startFakeRedis listens on 127.0.0.1, over TLS when tlsCfg is set.
// configure runs before the first connection is served.
func startFakeRedis(t *testing.T, tlsCfg *tls.Config, configure func(*fakeRedis)) *fakeRedis {
	t.Helper()
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	f := &fakeRedis{ln: ln, data: map[string]string{}}
	f.slotsAddr = ln.Addr().String()
	if configure != nil {
		configure(f)
	}
	if tlsCfg != nil {
		f.ln = tls.NewListener(ln, tlsCfg)
	}
	go func() {
		for {
			c, err := f.ln.Accept()
			if err != nil {
				return
			}
			f.mu.Lock()
			f.conns = append(f.conns, c)
			f.mu.Unlock()
			go f.serve(c)
		}
	}()
	t.Cleanup(func() {
		_ = f.ln.Close()
		f.mu.Lock()
		for _, c := range f.conns {
			_ = c.Close()
		}
		f.mu.Unlock()
	})
	return f
}

func (f *fakeRedis) addr() string { return f.ln.Addr().String() }

// commands is every command received, upper-cased name first.
func (f *fakeRedis) commands() [][]string {
	f.mu.Lock()
	defer f.mu.Unlock()
	return append([][]string(nil), f.cmds...)
}

func (f *fakeRedis) sawPassword(pw string) bool {
	for _, c := range f.commands() {
		for _, a := range c {
			if a == pw {
				return true
			}
		}
	}
	return false
}

func (f *fakeRedis) saw(name string) bool {
	for _, c := range f.commands() {
		if c[0] == name {
			return true
		}
	}
	return false
}

func readRESPCommand(r *bufio.Reader) ([]string, error) {
	line, err := r.ReadString('\n')
	if err != nil {
		return nil, err
	}
	line = strings.TrimRight(line, "\r\n")
	if len(line) < 2 || line[0] != '*' {
		return nil, fmt.Errorf("not a RESP array: %q", line)
	}
	n, err := strconv.Atoi(line[1:])
	if err != nil || n < 1 {
		return nil, fmt.Errorf("bad array length %q", line)
	}
	args := make([]string, n)
	for i := range args {
		h, err := r.ReadString('\n')
		if err != nil {
			return nil, err
		}
		h = strings.TrimRight(h, "\r\n")
		if len(h) < 2 || h[0] != '$' {
			return nil, fmt.Errorf("not a bulk string: %q", h)
		}
		size, err := strconv.Atoi(h[1:])
		if err != nil || size < 0 {
			return nil, fmt.Errorf("bad bulk length %q", h)
		}
		buf := make([]byte, size+2)
		if _, err := io.ReadFull(r, buf); err != nil {
			return nil, err
		}
		args[i] = string(buf[:size])
	}
	args[0] = strings.ToUpper(args[0])
	return args, nil
}

func bulk(s string) string { return "$" + strconv.Itoa(len(s)) + "\r\n" + s + "\r\n" }

func (f *fakeRedis) serve(c net.Conn) {
	defer c.Close()
	r := bufio.NewReader(c)
	authed := f.password == ""
	for {
		args, err := readRESPCommand(r)
		if err != nil {
			return
		}
		f.mu.Lock()
		f.cmds = append(f.cmds, args)
		f.mu.Unlock()
		var out string
		switch args[0] {
		case "HELLO":
			out = "-ERR unknown command 'HELLO'\r\n"
		case "AUTH":
			switch {
			case f.password == "":
				out = "-ERR AUTH <password> called without any password configured for the default user.\r\n"
			case args[len(args)-1] == f.password:
				authed, out = true, "+OK\r\n"
			default:
				out = "-WRONGPASS invalid username-password pair or user is disabled.\r\n"
			}
		case "PING":
			out = "+PONG\r\n"
		case "QUIT":
			_, _ = io.WriteString(c, "+OK\r\n")
			return
		case "SENTINEL":
			switch {
			case len(args) == 3 && strings.EqualFold(args[1], "get-master-addr-by-name") && args[2] == f.masterName && f.masterAddr != "":
				host, port, _ := net.SplitHostPort(f.masterAddr)
				out = "*2\r\n" + bulk(host) + bulk(port)
			case len(args) == 3 && strings.EqualFold(args[1], "get-master-addr-by-name"):
				out = "*-1\r\n"
			case len(args) >= 2 && (strings.EqualFold(args[1], "sentinels") || strings.EqualFold(args[1], "replicas") || strings.EqualFold(args[1], "slaves")):
				out = "*0\r\n"
			default:
				out = "-ERR unknown sentinel subcommand\r\n"
			}
		case "SUBSCRIBE", "PSUBSCRIBE":
			kind := strings.ToLower(args[0])
			for i, ch := range args[1:] {
				out += "*3\r\n" + bulk(kind) + bulk(ch) + ":" + strconv.Itoa(i+1) + "\r\n"
			}
		default:
			if !authed {
				out = "-NOAUTH Authentication required.\r\n"
				break
			}
			out = f.answer(args)
		}
		if _, err := io.WriteString(c, out); err != nil {
			return
		}
	}
}

// answer answers the commands that need an authenticated connection.
func (f *fakeRedis) answer(args []string) string {
	f.mu.Lock()
	defer f.mu.Unlock()
	switch args[0] {
	case "SELECT", "CLIENT", "READONLY":
		return "+OK\r\n"
	case "GET":
		v, ok := f.data[args[1]]
		if !ok {
			return "$-1\r\n"
		}
		return bulk(v)
	case "SET":
		f.data[args[1]] = args[2]
		return "+OK\r\n"
	case "DEL":
		n := 0
		for _, k := range args[1:] {
			if _, ok := f.data[k]; ok {
				delete(f.data, k)
				n++
			}
		}
		return ":" + strconv.Itoa(n) + "\r\n"
	case "CLUSTER":
		if len(args) >= 2 && strings.EqualFold(args[1], "SLOTS") {
			host, port, _ := net.SplitHostPort(f.slotsAddr)
			return "*1\r\n*3\r\n:0\r\n:16383\r\n*3\r\n" + bulk(host) + ":" + port + "\r\n" + bulk("node000000000000000000000000000000000000")
		}
	}
	return "-ERR unknown command '" + args[0] + "'\r\n"
}

// roundTripResponse stores and reads back one response through b.
func roundTripResponse(t *testing.T, what string, b *RedisBackend) {
	t.Helper()
	want := &models.ChatCompletionResponse{ID: "rt-" + what, Model: "m"}
	b.Set("k-"+what, want, time.Minute)
	got, ok := b.Get("k-" + what)
	if !ok || got.ID != want.ID {
		t.Fatalf("%s: Get after Set = %v, %v", what, got, ok)
	}
	b.Delete("k-" + what)
	if _, ok := b.Get("k-" + what); ok {
		t.Errorf("%s: Get after Delete still hits", what)
	}
}

func TestRedisTLSSingleWorks(t *testing.T) {
	ca := localhostTLS(t)
	node := startFakeRedis(t, ca.server, func(f *fakeRedis) { f.password = tlsPassword })
	b, err := newRedisBackend(config.RedisCacheConfig{Address: node.addr(), Password: tlsPassword, DB: 3, TLS: true, KeyPrefix: "p:", Timeout: 2 * time.Second}, ca.roots)
	if err != nil {
		t.Fatalf("tls single: %v", err)
	}
	defer b.Close()
	roundTripResponse(t, "single", b)
	if !node.sawPassword(tlsPassword) {
		t.Errorf("the node never received the password (over TLS)")
	}
	if !node.saw("SELECT") {
		t.Errorf("db 3 was never selected")
	}
}

// The name verified is the address's host: a certificate for another name is
// refused, and the same certificate is accepted when the address names it.
func TestRedisTLSVerifiesServerName(t *testing.T) {
	ca := newCacheTLS(t, nil, []string{"localhost"})
	node := startFakeRedis(t, ca.server, func(f *fakeRedis) { f.password = tlsPassword })
	_, port, _ := net.SplitHostPort(node.addr())

	_, err := newRedisBackend(config.RedisCacheConfig{Address: "127.0.0.1:" + port, Password: tlsPassword, TLS: true, Timeout: time.Second}, ca.roots)
	if err == nil || !strings.Contains(err.Error(), "certificate") {
		t.Fatalf("a certificate for localhost accepted for 127.0.0.1: err=%v", err)
	}
	if node.sawPassword(tlsPassword) || len(node.commands()) != 0 {
		t.Fatalf("commands reached a server whose certificate did not verify: %v", node.commands())
	}

	b, err := newRedisBackend(config.RedisCacheConfig{Address: "localhost:" + port, Password: tlsPassword, TLS: true, Timeout: 2 * time.Second}, ca.roots)
	if err != nil {
		t.Fatalf("localhost with a certificate for localhost: %v", err)
	}
	defer b.Close()
	roundTripResponse(t, "server-name", b)
}

// The exported constructor verifies against the system's roots: a private CA
// it was not given is refused.
func TestRedisTLSUntrustedCertificateRefused(t *testing.T) {
	ca := localhostTLS(t)
	node := startFakeRedis(t, ca.server, func(f *fakeRedis) { f.password = tlsPassword })
	_, err := NewBackend(config.CacheConfig{Backend: "redis", Redis: config.RedisCacheConfig{Address: node.addr(), Password: tlsPassword, TLS: true, Timeout: time.Second}})
	if err == nil {
		t.Fatal("a certificate from an unknown CA was accepted")
	}
	if node.sawPassword(tlsPassword) {
		t.Error("the password reached a server that did not verify")
	}
}

func TestRedisTLSClusterWorks(t *testing.T) {
	ca := localhostTLS(t)
	a := startFakeRedis(t, ca.server, func(f *fakeRedis) { f.password = tlsPassword })
	// Node b knows the cluster too: every slot is on a.
	bNode := startFakeRedis(t, ca.server, func(f *fakeRedis) { f.password = tlsPassword; f.slotsAddr = a.ln.Addr().String() })
	b, err := newRedisBackend(config.RedisCacheConfig{Mode: "cluster", Addresses: []string{bNode.addr(), a.addr()}, Password: tlsPassword, TLS: true, Timeout: 2 * time.Second}, ca.roots)
	if err != nil {
		t.Fatalf("tls cluster: %v", err)
	}
	defer b.Close()
	roundTripResponse(t, "cluster", b)
	if !a.saw("SET") || !a.saw("GET") {
		t.Errorf("the slot owner never served the data: %v", a.commands())
	}
	if !a.saw("CLUSTER") && !bNode.saw("CLUSTER") {
		t.Errorf("no node was asked for the cluster's slots")
	}
}

func TestRedisTLSClusterRefusesADatabase(t *testing.T) {
	a := startPlainNode(t)
	_, err := NewBackend(config.CacheConfig{Backend: "redis", Redis: config.RedisCacheConfig{Mode: "cluster", Addresses: []string{a.addr()}, DB: 2, Timeout: time.Second}})
	if err == nil || !strings.Contains(err.Error(), "database 0") {
		t.Fatalf("cluster with db 2: err=%v, want a refusal naming database 0", err)
	}
	if len(a.bytes()) != 0 {
		t.Errorf("a refused config dialled the node: %q", a.bytes())
	}
}

// Sentinel: address names the master set, addresses the sentinels. The
// sentinels are asked without the password; the master gets it, over TLS.
func TestRedisTLSSentinelWorks(t *testing.T) {
	ca := localhostTLS(t)
	master := startFakeRedis(t, ca.server, func(f *fakeRedis) { f.password = tlsPassword })
	s1 := startFakeRedis(t, ca.server, func(f *fakeRedis) { f.masterName, f.masterAddr = "cache-master", master.ln.Addr().String() })
	s2 := startFakeRedis(t, ca.server, func(f *fakeRedis) { f.masterName, f.masterAddr = "cache-master", master.ln.Addr().String() })
	b, err := newRedisBackend(config.RedisCacheConfig{Mode: "sentinel", Address: "cache-master", Addresses: []string{s1.addr(), s2.addr()}, Password: tlsPassword, DB: 1, TLS: true, Timeout: 2 * time.Second}, ca.roots)
	if err != nil {
		t.Fatalf("tls sentinel: %v", err)
	}
	defer b.Close()
	roundTripResponse(t, "sentinel", b)
	if !master.sawPassword(tlsPassword) || !master.saw("SET") {
		t.Errorf("the master did not serve the data with the password: %v", master.commands())
	}
	if s1.sawPassword(tlsPassword) || s2.sawPassword(tlsPassword) {
		t.Errorf("a sentinel received the master's password")
	}
	if !s1.saw("SENTINEL") && !s2.saw("SENTINEL") {
		t.Errorf("no sentinel was asked for the master")
	}
}

func TestRedisTLSSentinelAddressIsTheMasterName(t *testing.T) {
	s := startPlainNode(t)
	for _, c := range []struct {
		address string
		want    string
	}{
		{"10.0.0.5:6379", "master set's name"},
		{"", "master set's name"},
	} {
		_, err := NewBackend(config.CacheConfig{Backend: "redis", Redis: config.RedisCacheConfig{Mode: "sentinel", Address: c.address, Addresses: []string{s.addr()}, Timeout: time.Second}})
		if err == nil || !strings.Contains(err.Error(), c.want) {
			t.Errorf("sentinel with address %q: err=%v, want it to say %q", c.address, err, c.want)
		}
	}
	_, err := NewBackend(config.CacheConfig{Backend: "redis", Redis: config.RedisCacheConfig{Mode: "sentinel", Address: "cache-master", Timeout: time.Second}})
	if err == nil || !strings.Contains(err.Error(), "sentinels") {
		t.Errorf("sentinel without addresses: err=%v", err)
	}
	if len(s.bytes()) != 0 {
		t.Errorf("a refused config dialled a sentinel: %q", s.bytes())
	}
}

// Without tls, the modes work in plaintext as configured.
func TestRedisTLSOffPlaintextModesWork(t *testing.T) {
	single := startFakeRedis(t, nil, func(f *fakeRedis) { f.password = tlsPassword })
	b, err := NewRedisBackend(single.addr(), tlsPassword, 0, "p:", true, 2, 2*time.Second)
	if err != nil {
		t.Fatalf("plaintext single (NewRedisBackend): %v", err)
	}
	roundTripResponse(t, "plain-single-gzip", b)
	b.Close()

	node := startFakeRedis(t, nil, nil)
	cb, err := NewBackend(config.CacheConfig{Backend: "redis", Redis: config.RedisCacheConfig{Mode: "cluster", Addresses: []string{node.addr()}, Timeout: 2 * time.Second}})
	if err != nil {
		t.Fatalf("plaintext cluster: %v", err)
	}
	roundTripResponse(t, "plain-cluster", cb.(*RedisBackend))
	cb.(*RedisBackend).Close()

	master := startFakeRedis(t, nil, nil)
	sentinel := startFakeRedis(t, nil, func(f *fakeRedis) { f.masterName, f.masterAddr = "m1", master.ln.Addr().String() })
	sb, err := NewBackend(config.CacheConfig{Backend: "redis", Redis: config.RedisCacheConfig{Mode: "sentinel", Address: "m1", Addresses: []string{sentinel.addr()}, Timeout: 2 * time.Second}})
	if err != nil {
		t.Fatalf("plaintext sentinel: %v", err)
	}
	roundTripResponse(t, "plain-sentinel", sb.(*RedisBackend))
	sb.(*RedisBackend).Close()
}

func TestRedisTLSUnknownModeRefused(t *testing.T) {
	_, err := NewBackend(config.CacheConfig{Backend: "redis", Redis: config.RedisCacheConfig{Mode: "ring", Address: "127.0.0.1:1"}})
	if err == nil || !strings.Contains(err.Error(), "unknown mode") {
		t.Errorf("mode ring: err=%v", err)
	}
}

// The backend against the test Redis (TEST_REDIS_ADDR, DB TEST_REDIS_DB), in
// plaintext: what HELLO 3 and RESP3 give a real server. It writes only its
// own prefixed keys and deletes them.
func TestRedisTLSOffRealRedis(t *testing.T) {
	addr := os.Getenv("TEST_REDIS_ADDR")
	if addr == "" {
		t.Skip("set TEST_REDIS_ADDR (and TEST_REDIS_DB) to run against a real Redis")
	}
	db, _ := strconv.Atoi(os.Getenv("TEST_REDIS_DB"))
	prefix := fmt.Sprintf("test:cache:%d:", time.Now().UnixNano())
	for _, compress := range []bool{false, true} {
		b, err := NewRedisBackend(addr, "", db, prefix, compress, 2, 2*time.Second)
		if err != nil {
			t.Fatalf("TEST_REDIS_ADDR=%s is set but the cache cannot reach it: %v", addr, err)
		}
		roundTripResponse(t, "real-"+strconv.FormatBool(compress), b)
		b.Close()
	}
}
