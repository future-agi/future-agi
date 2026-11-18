package server

import (
	"bytes"
	"context"
	"crypto"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/pem"
	"log/slog"
	"math/big"
	"net"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"
)

// testPKI is a throwaway root CA and intermediate, generated per test. Leaves
// are issued by the intermediate, so a client that trusts only the root needs
// the intermediate served with the leaf.
type testPKI struct {
	rootPool *x509.CertPool
	inter    *x509.Certificate
	interKey crypto.Signer
	interPEM []byte
}

func newTestPKI(t *testing.T) *testPKI {
	t.Helper()
	rootKey := mustKey(t)
	rootTmpl := &x509.Certificate{
		SerialNumber:          big.NewInt(1),
		Subject:               pkix.Name{CommonName: "agentcc test root"},
		NotBefore:             time.Now().Add(-time.Hour),
		NotAfter:              time.Now().Add(24 * time.Hour),
		IsCA:                  true,
		BasicConstraintsValid: true,
		KeyUsage:              x509.KeyUsageCertSign,
	}
	rootDER, err := x509.CreateCertificate(rand.Reader, rootTmpl, rootTmpl, rootKey.Public(), rootKey)
	if err != nil {
		t.Fatal(err)
	}
	root, _ := x509.ParseCertificate(rootDER)

	interKey := mustKey(t)
	interTmpl := &x509.Certificate{
		SerialNumber:          big.NewInt(2),
		Subject:               pkix.Name{CommonName: "agentcc test intermediate"},
		NotBefore:             time.Now().Add(-time.Hour),
		NotAfter:              time.Now().Add(24 * time.Hour),
		IsCA:                  true,
		BasicConstraintsValid: true,
		KeyUsage:              x509.KeyUsageCertSign,
	}
	interDER, err := x509.CreateCertificate(rand.Reader, interTmpl, root, interKey.Public(), rootKey)
	if err != nil {
		t.Fatal(err)
	}
	inter, _ := x509.ParseCertificate(interDER)

	pool := x509.NewCertPool()
	pool.AddCert(root)
	return &testPKI{
		rootPool: pool,
		inter:    inter,
		interKey: interKey,
		interPEM: pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: interDER}),
	}
}

func mustKey(t *testing.T) *ecdsa.PrivateKey {
	t.Helper()
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	return key
}

// issue returns a full-chain cert PEM (leaf, then intermediate) for
// localhost and 127.0.0.1 with the given serial, and its key PEM.
func (p *testPKI) issue(t *testing.T, serial int64) (certPEM, keyPEM []byte) {
	t.Helper()
	key := mustKey(t)
	tmpl := &x509.Certificate{
		SerialNumber: big.NewInt(serial),
		Subject:      pkix.Name{CommonName: "localhost"},
		DNSNames:     []string{"localhost"},
		IPAddresses:  []net.IP{net.IPv4(127, 0, 0, 1)},
		NotBefore:    time.Now().Add(-time.Hour),
		NotAfter:     time.Now().Add(24 * time.Hour),
		KeyUsage:     x509.KeyUsageDigitalSignature,
		ExtKeyUsage:  []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth},
	}
	der, err := x509.CreateCertificate(rand.Reader, tmpl, p.inter, key.Public(), p.interKey)
	if err != nil {
		t.Fatal(err)
	}
	keyDER, err := x509.MarshalPKCS8PrivateKey(key)
	if err != nil {
		t.Fatal(err)
	}
	certPEM = append(pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der}), p.interPEM...)
	return certPEM, pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: keyDER})
}

// issueExpired is issue for a certificate that expired an hour ago.
func (p *testPKI) issueExpired(t *testing.T, serial int64) (certPEM, keyPEM []byte) {
	t.Helper()
	key := mustKey(t)
	tmpl := &x509.Certificate{
		SerialNumber: big.NewInt(serial),
		Subject:      pkix.Name{CommonName: "localhost"},
		DNSNames:     []string{"localhost"},
		NotBefore:    time.Now().Add(-48 * time.Hour),
		NotAfter:     time.Now().Add(-time.Hour),
		KeyUsage:     x509.KeyUsageDigitalSignature,
		ExtKeyUsage:  []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth},
	}
	der, err := x509.CreateCertificate(rand.Reader, tmpl, p.inter, key.Public(), p.interKey)
	if err != nil {
		t.Fatal(err)
	}
	keyDER, err := x509.MarshalPKCS8PrivateKey(key)
	if err != nil {
		t.Fatal(err)
	}
	return append(pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der}), p.interPEM...),
		pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: keyDER})
}

// writePair writes a cert and key into dir as tls.crt and tls.key.
func writePair(t *testing.T, dir string, certPEM, keyPEM []byte) (certFile, keyFile string) {
	t.Helper()
	certFile, keyFile = filepath.Join(dir, "tls.crt"), filepath.Join(dir, "tls.key")
	if certPEM != nil {
		writeFile(t, certFile, certPEM)
	}
	if keyPEM != nil {
		writeFile(t, keyFile, keyPEM)
	}
	return certFile, keyFile
}

// writeFile replaces path atomically, as a rotation tool would.
func writeFile(t *testing.T, path string, data []byte) {
	t.Helper()
	tmp := path + ".tmp"
	if err := os.WriteFile(tmp, data, 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.Rename(tmp, path); err != nil {
		t.Fatal(err)
	}
}

func servedSerial(t *testing.T, r *certReloader) int64 {
	t.Helper()
	cert, err := r.GetCertificate(nil)
	if err != nil {
		t.Fatal(err)
	}
	return cert.Leaf.SerialNumber.Int64()
}

// captureLogs sends slog's default logger to a buffer for the test.
func captureLogs(t *testing.T) *syncBuffer {
	t.Helper()
	buf := &syncBuffer{}
	prev := slog.Default()
	slog.SetDefault(slog.New(slog.NewTextHandler(buf, nil)))
	t.Cleanup(func() { slog.SetDefault(prev) })
	return buf
}

type syncBuffer struct {
	mu sync.Mutex
	b  bytes.Buffer
}

func (s *syncBuffer) Write(p []byte) (int, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.b.Write(p)
}

func (s *syncBuffer) String() string {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.b.String()
}

// checkNoKeyMaterial fails when the logs carry a PEM armour line or any
// base64 line of the given PEM files.
func checkNoKeyMaterial(t *testing.T, logs string, pems ...[]byte) {
	t.Helper()
	if strings.Contains(logs, "-----BEGIN") {
		t.Errorf("logs carry PEM material:\n%s", logs)
	}
	for _, p := range pems {
		for _, line := range strings.Split(string(p), "\n") {
			if len(line) > 16 && !strings.HasPrefix(line, "-----") && strings.Contains(logs, line) {
				t.Errorf("logs carry a line of PEM data %q", line)
			}
		}
	}
}

func TestCertReloaderLoadsTheChainAndLogsItsIdentity(t *testing.T) {
	logs := captureLogs(t)
	pki := newTestPKI(t)
	certFile, keyFile := writePair(t, t.TempDir(), nil, nil)
	cert, key := pki.issue(t, 11)
	writePair(t, filepath.Dir(certFile), cert, key)

	r := newCertReloader(certFile, keyFile, time.Minute)
	if err := r.Load(); err != nil {
		t.Fatalf("Load: %v", err)
	}
	served, _ := r.GetCertificate(nil)
	if len(served.Certificate) != 2 {
		t.Errorf("chain length = %d, want leaf and intermediate", len(served.Certificate))
	}
	out := logs.String()
	for _, want := range []string{"TLS certificate loaded", "serial=b", "not_after=", "sha256=", `subject="CN=localhost"`, "chain_length=2"} {
		if !strings.Contains(out, want) {
			t.Errorf("load log lacks %q:\n%s", want, out)
		}
	}
	checkNoKeyMaterial(t, out, cert, key)
}

func TestCertReloaderPicksUpARotation(t *testing.T) {
	pki := newTestPKI(t)
	dir := t.TempDir()
	cert, key := pki.issue(t, 1)
	certFile, keyFile := writePair(t, dir, cert, key)
	r := newCertReloader(certFile, keyFile, time.Minute)
	if err := r.Load(); err != nil {
		t.Fatal(err)
	}

	if changed, err := r.reload(false); changed || err != nil {
		t.Fatalf("unchanged files: changed=%v err=%v, want nothing to do", changed, err)
	}
	cert, key = pki.issue(t, 2)
	writePair(t, dir, cert, key)
	if changed, err := r.reload(false); !changed || err != nil {
		t.Fatalf("rotated files: changed=%v err=%v", changed, err)
	}
	if got := servedSerial(t, r); got != 2 {
		t.Errorf("serial = %d after the rotation, want 2", got)
	}
}

// A Kubernetes Secret volume rotates by swapping a ..data symlink; the paths
// the gateway was given never change.
func TestCertReloaderFollowsASecretVolumeSymlinkSwap(t *testing.T) {
	pki := newTestPKI(t)
	dir := t.TempDir()
	for i, serial := range []int64{1, 2} {
		cert, key := pki.issue(t, serial)
		data := filepath.Join(dir, "..data_"+string(rune('a'+i)))
		if err := os.Mkdir(data, 0o700); err != nil {
			t.Fatal(err)
		}
		writePair(t, data, cert, key)
	}
	link := func(target string) {
		tmp := filepath.Join(dir, "..data_tmp")
		if err := os.Symlink(target, tmp); err != nil {
			t.Fatal(err)
		}
		if err := os.Rename(tmp, filepath.Join(dir, "..data")); err != nil {
			t.Fatal(err)
		}
	}
	link("..data_a")
	for _, name := range []string{"tls.crt", "tls.key"} {
		if err := os.Symlink(filepath.Join("..data", name), filepath.Join(dir, name)); err != nil {
			t.Fatal(err)
		}
	}
	r := newCertReloader(filepath.Join(dir, "tls.crt"), filepath.Join(dir, "tls.key"), time.Minute)
	if err := r.Load(); err != nil {
		t.Fatal(err)
	}
	link("..data_b")
	if changed, err := r.reload(false); !changed || err != nil {
		t.Fatalf("after the symlink swap: changed=%v err=%v", changed, err)
	}
	if got := servedSerial(t, r); got != 2 {
		t.Errorf("serial = %d, want 2", got)
	}
}

func TestCertReloaderKeepsTheLastGoodCertificate(t *testing.T) {
	pki := newTestPKI(t)
	otherCert, otherKey := pki.issue(t, 99)
	tests := []struct {
		name      string
		cert, key []byte
		want      string
	}{
		{"garbage cert", []byte("not a certificate"), nil, "failed to find any PEM data in certificate input"},
		{"garbage key", nil, []byte("-----BEGIN NOTHING-----\nAAAA\n-----END NOTHING-----\n"), "failed to find"},
		{"key of another certificate", otherCert, nil, "private key does not match public key"},
		{"empty files", []byte{}, []byte{}, "failed to find any PEM data"},
	}
	_ = otherKey
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			logs := captureLogs(t)
			dir := t.TempDir()
			cert, key := pki.issue(t, 1)
			certFile, keyFile := writePair(t, dir, cert, key)
			r := newCertReloader(certFile, keyFile, time.Minute)
			if err := r.Load(); err != nil {
				t.Fatal(err)
			}

			writePair(t, dir, tt.cert, tt.key)
			changed, err := r.reload(false)
			if changed || err == nil || !strings.Contains(err.Error(), tt.want) {
				t.Fatalf("reload: changed=%v err=%v, want an error containing %q", changed, err, tt.want)
			}
			if got := servedSerial(t, r); got != 1 {
				t.Errorf("serial = %d after a bad pair, want the previous 1", got)
			}
			out := logs.String()
			if !strings.Contains(out, "TLS certificate not reloaded; still serving the previous one") || !strings.Contains(out, certFile) {
				t.Errorf("no error log naming the file:\n%s", out)
			}
			checkNoKeyMaterial(t, out, cert, key, tt.cert, tt.key)

			// The same bad pair is reported once, not on every tick.
			before := strings.Count(out, "not reloaded")
			r.reload(false)
			if after := strings.Count(logs.String(), "not reloaded"); after != before {
				t.Errorf("an unchanged bad pair was reported again (%d -> %d)", before, after)
			}
		})
	}
}

// A rotation that writes the cert before the key leaves a moment where the
// two do not match. The old pair stays until both files are the new ones.
func TestCertReloaderWaitsOutAHalfUpdatedPair(t *testing.T) {
	pki := newTestPKI(t)
	dir := t.TempDir()
	cert, key := pki.issue(t, 1)
	certFile, keyFile := writePair(t, dir, cert, key)
	r := newCertReloader(certFile, keyFile, time.Minute)
	if err := r.Load(); err != nil {
		t.Fatal(err)
	}

	newCert, newKey := pki.issue(t, 2)
	writePair(t, dir, newCert, nil)
	if _, err := r.reload(false); err == nil {
		t.Fatal("new cert with the old key loaded")
	}
	if got := servedSerial(t, r); got != 1 {
		t.Fatalf("serial = %d mid-rotation, want the previous 1", got)
	}
	writePair(t, dir, nil, newKey)
	if changed, err := r.reload(false); !changed || err != nil {
		t.Fatalf("completed rotation: changed=%v err=%v", changed, err)
	}
	if got := servedSerial(t, r); got != 2 {
		t.Errorf("serial = %d after the rotation completed, want 2", got)
	}
}

func TestCertReloaderRunPollsUntilCancelled(t *testing.T) {
	pki := newTestPKI(t)
	dir := t.TempDir()
	cert, key := pki.issue(t, 1)
	certFile, keyFile := writePair(t, dir, cert, key)
	r := newCertReloader(certFile, keyFile, 10*time.Millisecond)
	if err := r.Load(); err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	go func() { r.Run(ctx); close(done) }()

	cert, key = pki.issue(t, 2)
	writePair(t, dir, cert, key)
	deadline := time.Now().Add(5 * time.Second)
	for servedSerial(t, r) != 2 {
		if time.Now().After(deadline) {
			t.Fatal("the rotation was not picked up")
		}
		time.Sleep(5 * time.Millisecond)
	}
	cancel()
	select {
	case <-done:
	case <-time.After(time.Second):
		t.Fatal("Run did not return after cancel")
	}
}

func TestCertReloaderStartupErrorsNameTheFile(t *testing.T) {
	pki := newTestPKI(t)
	cert, key := pki.issue(t, 1)
	otherCert, _ := pki.issue(t, 2)
	tests := []struct {
		name      string
		cert, key []byte // nil: file absent
		want      func(certFile, keyFile string) []string
	}{
		{"missing cert", nil, key, func(c, _ string) []string { return []string{"server.tls: reading " + c, "no such file"} }},
		{"missing key", cert, nil, func(_, k string) []string { return []string{"server.tls: reading " + k, "no such file"} }},
		{"unparsable cert", []byte("garbage"), key, func(c, _ string) []string { return []string{"server.tls: loading " + c, "failed to find any PEM data"} }},
		{"key mismatch", otherCert, key, func(c, _ string) []string { return []string{c, "private key does not match public key"} }},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			certFile, keyFile := writePair(t, t.TempDir(), tt.cert, tt.key)
			r := newCertReloader(certFile, keyFile, time.Minute)
			err := r.Load()
			if err == nil {
				t.Fatal("Load succeeded")
			}
			for _, want := range tt.want(certFile, keyFile) {
				if !strings.Contains(err.Error(), want) {
					t.Errorf("error %q lacks %q", err, want)
				}
			}
			if _, err := r.GetCertificate(nil); err == nil {
				t.Error("a certificate is served after a failed load")
			}
		})
	}
}

// tlsVersionName is for test messages.
func tlsVersionName(v uint16) string { return tls.VersionName(v) }

// An expired certificate fails every verifying client, so it is refused at
// startup and never rotated to.
func TestCertReloaderRefusesAnExpiredCertificate(t *testing.T) {
	pki := newTestPKI(t)
	expiredCert, expiredKey := pki.issueExpired(t, 7)

	certFile, keyFile := writePair(t, t.TempDir(), expiredCert, expiredKey)
	err := newCertReloader(certFile, keyFile, time.Minute).Load()
	if err == nil || !strings.Contains(err.Error(), certFile+" expired at") {
		t.Fatalf("Load of an expired certificate = %v, want an error naming the file", err)
	}

	dir := t.TempDir()
	cert, key := pki.issue(t, 1)
	certFile, keyFile = writePair(t, dir, cert, key)
	r := newCertReloader(certFile, keyFile, time.Minute)
	if err := r.Load(); err != nil {
		t.Fatal(err)
	}
	writePair(t, dir, expiredCert, expiredKey)
	if changed, err := r.reload(false); changed || err == nil {
		t.Fatalf("rotation to an expired certificate: changed=%v err=%v", changed, err)
	}
	if got := servedSerial(t, r); got != 1 {
		t.Errorf("serial = %d, want the previous 1", got)
	}
}
