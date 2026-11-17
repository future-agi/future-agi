//go:build cataloguepreflighttest

package cataloguepreflight

import (
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/sha256"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/hex"
	"errors"
	"math/big"
	"net"
	"net/http"
	"sync"
	"time"
)

// Certificates are ephemeral synthetic fixtures, generated in memory. No real
// credentials, root store override, IP SAN, or InsecureSkipVerify is used.
type Certificates struct {
	Certificate tls.Certificate
	Roots       *x509.CertPool
	CA, Leaf    *x509.Certificate
}

func NewCertificates(name string) (*Certificates, error) {
	if _, err := DNSQuestion(name, 1); err != nil {
		return nil, err
	}
	caKey, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		return nil, err
	}
	leafKey, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		return nil, err
	}
	now := time.Now()
	ca := &x509.Certificate{SerialNumber: big.NewInt(1), Subject: pkix.Name{CommonName: "catalogue synthetic CA"},
		NotBefore: now.Add(-time.Minute), NotAfter: now.Add(time.Hour), IsCA: true,
		BasicConstraintsValid: true, KeyUsage: x509.KeyUsageCertSign | x509.KeyUsageDigitalSignature}
	der, err := x509.CreateCertificate(rand.Reader, ca, ca, &caKey.PublicKey, caKey)
	if err != nil {
		return nil, err
	}
	ca, err = x509.ParseCertificate(der)
	if err != nil {
		return nil, err
	}
	leaf := &x509.Certificate{SerialNumber: big.NewInt(2), DNSNames: []string{name},
		NotBefore: now.Add(-time.Minute), NotAfter: now.Add(time.Hour),
		KeyUsage: x509.KeyUsageDigitalSignature, ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth}}
	der, err = x509.CreateCertificate(rand.Reader, leaf, ca, &leafKey.PublicKey, caKey)
	if err != nil {
		return nil, err
	}
	leaf, err = x509.ParseCertificate(der)
	if err != nil {
		return nil, err
	}
	roots := x509.NewCertPool()
	roots.AddCert(ca)
	return &Certificates{tls.Certificate{Certificate: [][]byte{der}, PrivateKey: leafKey, Leaf: leaf}, roots, ca, leaf}, nil
}

func Digest(b []byte) string { sum := sha256.Sum256(b); return hex.EncodeToString(sum[:]) }

type TLSWitness struct {
	SNI        string `json:"sni"`
	Method     string `json:"method"`
	Path       string `json:"path"`
	AuthSHA256 string `json:"auth_sha256"`
}

type TLSFixture struct {
	listener  net.Listener
	server    *http.Server
	done      chan error
	mu        sync.Mutex
	witnesses []TLSWitness
	hellos    []string
}

func StartTLS(certs *Certificates, handler http.Handler) (*TLSFixture, error) {
	if certs == nil || handler == nil {
		return nil, errors.New("certificate and real mock handler required")
	}
	listener, err := net.Listen("tcp4", "127.0.0.1:0")
	if err != nil {
		return nil, err
	}
	f := &TLSFixture{listener: listener, done: make(chan error, 1)}
	f.server = &http.Server{ReadHeaderTimeout: 5 * time.Second, Handler: http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		f.mu.Lock()
		f.witnesses = append(f.witnesses, TLSWitness{r.TLS.ServerName, r.Method, r.URL.Path, Digest([]byte(r.Header.Get("Authorization")))})
		f.mu.Unlock()
		handler.ServeHTTP(w, r)
	})}
	serverTLS := &tls.Config{Certificates: []tls.Certificate{certs.Certificate}, MinVersion: tls.VersionTLS12, NextProtos: []string{"http/1.1"}}
	serverTLS.GetConfigForClient = func(hello *tls.ClientHelloInfo) (*tls.Config, error) {
		f.mu.Lock()
		f.hellos = append(f.hellos, hello.ServerName)
		f.mu.Unlock()
		return nil, nil // Observe only; retain the original fixture TLS configuration.
	}
	tlsListener := tls.NewListener(listener, serverTLS)
	go func() { f.done <- f.server.Serve(tlsListener) }()
	return f, nil
}

func (f *TLSFixture) Address() string { return f.listener.Addr().String() }
func (f *TLSFixture) Close()          { f.server.Close(); <-f.done }
func (f *TLSFixture) ClientHellos() []string {
	f.mu.Lock()
	defer f.mu.Unlock()
	return append([]string{}, f.hellos...)
}

func (f *TLSFixture) Witnesses() []TLSWitness {
	f.mu.Lock()
	defer f.mu.Unlock()
	return append([]TLSWitness{}, f.witnesses...)
}
