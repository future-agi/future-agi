package server

import (
	"context"
	"crypto/sha256"
	"crypto/tls"
	"crypto/x509"
	"encoding/hex"
	"fmt"
	"log/slog"
	"os"
	"strings"
	"sync/atomic"
	"time"
)

// certReloader serves the gateway's TLS certificate and picks up a new one
// when its files change, with no restart.
//
// It hashes the files' contents rather than watching mtimes: a Kubernetes
// Secret volume swaps a ..data symlink, which leaves the visible paths'
// mtimes alone. A pair that does not load — garbage, a key that does not
// match, or a half-written rotation with a new cert and the old key — is
// logged and skipped; the last good certificate stays in use, and the next
// tick tries again.
type certReloader struct {
	certFile, keyFile string
	interval          time.Duration

	cert atomic.Pointer[tls.Certificate]
	// sum is the hash of the last pair read, loaded or not, so a bad pair is
	// reported once rather than on every tick.
	sum string
	// now is for tests.
	now func() time.Time
}

func newCertReloader(certFile, keyFile string, interval time.Duration) *certReloader {
	return &certReloader{certFile: certFile, keyFile: keyFile, interval: interval, now: time.Now}
}

// Load reads the pair. It is the startup check: the error names the file and
// says what is wrong with it, and nothing is served until it succeeds.
func (r *certReloader) Load() error {
	_, err := r.reload(true)
	return err
}

// GetCertificate is tls.Config.GetCertificate.
func (r *certReloader) GetCertificate(*tls.ClientHelloInfo) (*tls.Certificate, error) {
	cert := r.cert.Load()
	if cert == nil {
		return nil, fmt.Errorf("server.tls: no certificate loaded")
	}
	return cert, nil
}

// Run re-reads the pair every interval until ctx is done.
func (r *certReloader) Run(ctx context.Context) {
	ticker := time.NewTicker(r.interval)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			r.reload(false)
		}
	}
}

// reload loads the pair when its contents changed since the last read, or
// always when force is set. It reports whether a new certificate is served.
func (r *certReloader) reload(force bool) (bool, error) {
	certPEM, err := os.ReadFile(r.certFile)
	if err != nil {
		return false, r.failed(force, "", fmt.Errorf("server.tls: reading %s: %w", r.certFile, err))
	}
	keyPEM, err := os.ReadFile(r.keyFile)
	if err != nil {
		return false, r.failed(force, "", fmt.Errorf("server.tls: reading %s: %w", r.keyFile, err))
	}
	h := sha256.New()
	h.Write(certPEM)
	h.Write([]byte{0})
	h.Write(keyPEM)
	sum := hex.EncodeToString(h.Sum(nil))
	if !force && sum == r.sum {
		return false, nil
	}

	cert, err := tls.X509KeyPair(certPEM, keyPEM)
	if err != nil {
		return false, r.failed(force, sum, fmt.Errorf("server.tls: loading %s and %s: %w", r.certFile, r.keyFile, err))
	}
	leaf := cert.Leaf
	if leaf == nil {
		if leaf, err = x509.ParseCertificate(cert.Certificate[0]); err != nil {
			return false, r.failed(force, sum, fmt.Errorf("server.tls: parsing %s: %w", r.certFile, err))
		}
		cert.Leaf = leaf
	}
	// An expired certificate fails every verifying client: refuse it at
	// startup, and keep the previous one rather than rotate to it.
	if now := r.now(); now.After(leaf.NotAfter) {
		return false, r.failed(force, sum, fmt.Errorf("server.tls: %s expired at %s",
			r.certFile, leaf.NotAfter.UTC().Format(time.RFC3339)))
	}
	r.sum = sum
	r.cert.Store(&cert)

	fingerprint := sha256.Sum256(leaf.Raw)
	attrs := []any{
		"cert_file", r.certFile,
		"subject", leaf.Subject.String(),
		"serial", leaf.SerialNumber.Text(16),
		"not_after", leaf.NotAfter.UTC().Format(time.RFC3339),
		"sha256", strings.ToUpper(hex.EncodeToString(fingerprint[:])),
		"chain_length", len(cert.Certificate),
	}
	if force {
		slog.Info("TLS certificate loaded", attrs...)
	} else {
		slog.Info("TLS certificate reloaded", attrs...)
	}
	return true, nil
}

// failed reports a pair that did not load. At startup that is the error;
// afterwards it is logged once per distinct content and the served
// certificate is kept.
func (r *certReloader) failed(startup bool, sum string, err error) error {
	if startup {
		return err
	}
	if sum != "" && sum == r.sum {
		return err
	}
	r.sum = sum
	slog.Error("TLS certificate not reloaded; still serving the previous one",
		"cert_file", r.certFile, "key_file", r.keyFile, "error", err.Error())
	return err
}
