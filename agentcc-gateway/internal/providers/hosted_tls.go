package providers

import (
	"crypto/tls"
	"fmt"
	"net"
	"net/url"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

// verifyHostedOneKeyTLS runs before discovery or provider registration. It sends
// no HTTP, credentials or paid requests. Hosted activation fails closed if any
// funded endpoint's certificate chain or hostname is not trusted. The system
// trust store (including standard SSL_CERT_FILE/SSL_CERT_DIR) is the only trust
// source, just as for the OpenAI adapter; there is no insecure retry.
// Config validation/-check-config remains network-free.
func verifyHostedOneKeyTLS(cfg *config.Config) error {
	if !cfg.IsHosted() {
		return nil
	}
	for _, p := range cfg.Providers {
		if !p.Managed {
			continue
		}
		u, err := url.Parse(p.BaseURL)
		if err != nil || u.Scheme != "https" || u.Hostname() == "" || p.SkipTLS {
			return fmt.Errorf("hosted one-key provider TLS verification failed")
		}
		port := u.Port()
		if port == "" {
			port = "443"
		}
		conn, err := tls.DialWithDialer(&net.Dialer{Timeout: 5 * time.Second}, "tcp",
			net.JoinHostPort(u.Hostname(), port), &tls.Config{MinVersion: tls.VersionTLS12})
		if err != nil {
			// Do not log provider URLs, credentials or operator-controlled values.
			return fmt.Errorf("hosted one-key provider TLS verification failed")
		}
		_ = conn.Close()
	}
	return nil
}
