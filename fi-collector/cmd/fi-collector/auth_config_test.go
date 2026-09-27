package main

import (
	"bytes"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/pem"
	"log/slog"
	"math/big"
	"net"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/auth"
	"github.com/jackc/pgx/v5/pgxpool"
)

func clearCollectorEnv(t *testing.T) {
	t.Helper()
	for _, entry := range os.Environ() {
		key, _, _ := strings.Cut(entry, "=")
		if strings.HasPrefix(key, "FI_") || strings.HasPrefix(key, "PG") {
			t.Setenv(key, "")
		}
	}
	t.Setenv("PGPASSFILE", filepath.Join(t.TempDir(), "absent.pgpass"))
	t.Setenv("PGSERVICEFILE", filepath.Join(t.TempDir(), "absent.pg_service.conf"))
}

func setPGFields(t *testing.T, prefix string) {
	t.Helper()
	for suffix, value := range map[string]string{
		"HOST": "::1", "PORT": "5432", "DATABASE": "futureagi", "USER": "collector",
	} {
		t.Setenv(prefix+"_"+suffix, value)
	}
}

func TestAuthEnvExplicitURIsTakePrecedenceWithoutLosingTLS(t *testing.T) {
	clearCollectorEnv(t)
	write := "postgres://collector:example@writer.example.test/db?sslmode=verify-full&sslrootcert=%2Fca.pem&application_name=collector"
	read := "postgres://collector:example@reader.example.test/db?sslmode=require"
	t.Setenv("FI_PG_WRITE", write)
	t.Setenv("FI_PG_READ", read)
	setPGFields(t, "FI_PG_WRITE")
	setPGFields(t, "FI_PG_READ")
	cfg := rootConfig{}
	if err := applyEnvOverrides(slog.Default(), &cfg); err != nil {
		t.Fatal(err)
	}
	if cfg.Auth.PGWrite != write || cfg.Auth.PGRead != read {
		t.Fatal("explicit URIs and their TLS/options must survive separate-field defaults")
	}
}

// Generate test-only credentials under t.TempDir, never use operator files or
// contact a database. The leaf is suitable for both offline server trust checks
// and proving that pgx loaded the configured client certificate/private key.
func pgTLSFixture(t *testing.T) (caPath, certPath, keyPath string, leaf *x509.Certificate) {
	t.Helper()
	caKey, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	clientKey, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	ca := &x509.Certificate{
		SerialNumber: big.NewInt(1), Subject: pkix.Name{CommonName: "collector test CA"},
		NotBefore: time.Now().Add(-time.Hour), NotAfter: time.Now().Add(time.Hour),
		IsCA: true, BasicConstraintsValid: true, KeyUsage: x509.KeyUsageCertSign,
	}
	caDER, err := x509.CreateCertificate(rand.Reader, ca, ca, &caKey.PublicKey, caKey)
	if err != nil {
		t.Fatal(err)
	}
	template := &x509.Certificate{
		SerialNumber: big.NewInt(2), Subject: pkix.Name{CommonName: "collector test client"},
		NotBefore: ca.NotBefore, NotAfter: ca.NotAfter,
		DNSNames: []string{"postgres.example.test"}, IPAddresses: []net.IP{net.ParseIP("2001:db8::42")},
		KeyUsage: x509.KeyUsageDigitalSignature, ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth, x509.ExtKeyUsageClientAuth},
	}
	leafDER, err := x509.CreateCertificate(rand.Reader, template, ca, &clientKey.PublicKey, caKey)
	if err != nil {
		t.Fatal(err)
	}
	leaf, err = x509.ParseCertificate(leafDER)
	if err != nil {
		t.Fatal(err)
	}
	keyDER, err := x509.MarshalPKCS8PrivateKey(clientKey)
	if err != nil {
		t.Fatal(err)
	}
	dir := t.TempDir()
	write := func(name, kind string, der []byte) string {
		path := filepath.Join(dir, name)
		if err := os.WriteFile(path, pem.EncodeToMemory(&pem.Block{Type: kind, Bytes: der}), 0o600); err != nil {
			t.Fatal(err)
		}
		return path
	}
	return write("root CA +?#%.pem", "CERTIFICATE", caDER),
		write("client cert +?#%.pem", "CERTIFICATE", leafDER),
		write("client key +?#%.pem", "PRIVATE KEY", keyDER), leaf
}

func TestAuthEnvVerifyFullCertificatesSurviveEndpointSelection(t *testing.T) {
	ca, cert, key, leaf := pgTLSFixture(t)
	for _, prefix := range []string{"FI_PG_WRITE", "FI_PG_READ"} {
		for _, source := range []string{"fields", "uri-inherits-tls-env", "uri-overrides-tls-env"} {
			t.Run(prefix+"/"+source, func(t *testing.T) {
				clearCollectorEnv(t)
				setPGFields(t, prefix)
				host := "postgres.example.test"
				if prefix == "FI_PG_READ" {
					host = "2001:db8::42"
				}
				t.Setenv(prefix+"_HOST", host)
				t.Setenv("PGSSLMODE", "verify-full")
				t.Setenv("PGSSLROOTCERT", ca)
				t.Setenv("PGSSLCERT", cert)
				t.Setenv("PGSSLKEY", key)
				var explicitURI string
				if source != "fields" {
					query := url.Values{"application_name": {"collector-test"}}
					if source == "uri-overrides-tls-env" {
						query.Set("sslmode", "verify-full")
						query.Set("sslrootcert", ca)
						query.Set("sslcert", cert)
						query.Set("sslkey", key)
						// Neither plaintext defaults nor stale certificate paths
						// may override an explicitly secured connection string.
						t.Setenv("PGSSLMODE", "disable")
						for _, name := range []string{"PGSSLROOTCERT", "PGSSLCERT", "PGSSLKEY"} {
							t.Setenv(name, filepath.Join(t.TempDir(), "absent.pem"))
						}
					}
					explicitURI = (&url.URL{Scheme: "postgres", Host: net.JoinHostPort(host, "5432"),
						User: url.User("uri-user"), Path: "/uri-db", RawQuery: query.Encode()}).String()
					t.Setenv(prefix, explicitURI)
				}
				cfg := rootConfig{}
				if err := applyEnvOverrides(slog.Default(), &cfg); err != nil {
					t.Fatal(err)
				}
				endpoint := cfg.Auth.PGWrite
				if prefix == "FI_PG_READ" {
					endpoint = cfg.Auth.PGRead
				}
				if explicitURI != "" && endpoint != explicitURI {
					t.Fatal("explicit connection string lost precedence")
				}
				parsed, err := pgxpool.ParseConfig(endpoint)
				if err != nil {
					t.Fatal(err)
				}
				tls := parsed.ConnConfig.TLSConfig
				if tls == nil || tls.InsecureSkipVerify || tls.ServerName != host || tls.RootCAs == nil {
					t.Fatal("verify-full mode, server identity or configured CA was lost")
				}
				if len(parsed.ConnConfig.Fallbacks) != 0 {
					t.Fatal("single-host strict TLS gained a fallback")
				}
				if len(tls.Certificates) != 1 || tls.Certificates[0].PrivateKey == nil ||
					!bytes.Equal(tls.Certificates[0].Certificate[0], leaf.Raw) {
					t.Fatal("configured client certificate/private key was not loaded")
				}
				if _, err := leaf.Verify(x509.VerifyOptions{Roots: tls.RootCAs, DNSName: tls.ServerName}); err != nil {
					t.Fatalf("configured CA does not verify the test server identity: %v", err)
				}
				if _, err := leaf.Verify(x509.VerifyOptions{Roots: tls.RootCAs, DNSName: "wrong.example.test"}); err == nil {
					t.Fatal("wrong server identity was accepted")
				}
			})
		}
	}
}

func TestAuthEnvFieldsAndYAMLPrecedence(t *testing.T) {
	clearCollectorEnv(t)
	original := auth.Config{PGWrite: "postgres://yaml-write/db?sslmode=verify-full", PGRead: "postgres://yaml-read/db?sslmode=require"}
	cfg := rootConfig{Auth: original}
	if err := applyEnvOverrides(slog.Default(), &cfg); err != nil {
		t.Fatal(err)
	}
	if cfg.Auth != original {
		t.Fatal("unset environment must preserve YAML")
	}
	setPGFields(t, "FI_PG_WRITE")
	if err := applyEnvOverrides(slog.Default(), &cfg); err != nil {
		t.Fatal(err)
	}
	parsed, err := url.Parse(cfg.Auth.PGWrite)
	if err != nil {
		t.Fatal(err)
	}
	if parsed.Host != "[::1]:5432" || parsed.RawQuery != "" || cfg.Auth.PGRead != original.PGRead {
		t.Fatal("field override must be IPv6-safe, preserve driver TLS and leave the other endpoint alone")
	}
	// No read endpoint: leave it empty for auth.Config.defaults to reuse write.
	cfg = rootConfig{}
	if err := applyEnvOverrides(slog.Default(), &cfg); err != nil {
		t.Fatal(err)
	}
	if !cfg.Auth.IsEnabled() || cfg.Auth.PGRead != "" {
		t.Fatal("write-only configuration must preserve the read-to-write default")
	}
}

func TestAuthEnvInvalidFieldsFailEvenWithLegacyURI(t *testing.T) {
	for _, prefix := range []string{"FI_PG_WRITE", "FI_PG_READ"} {
		for _, legacy := range []bool{false, true} {
			t.Run(prefix+"/legacy="+map[bool]string{false: "false", true: "true"}[legacy], func(t *testing.T) {
				clearCollectorEnv(t)
				if legacy {
					t.Setenv(prefix, "postgres://user:private-uri-password@legacy.example.test/db?sslmode=verify-full")
				}
				t.Setenv(prefix+"_HOST", "postgres")
				t.Setenv(prefix+"_PASSWORD", "private-field-password")
				cfg := rootConfig{Auth: auth.Config{PGWrite: "yaml-write", PGRead: "yaml-read"}}
				before := cfg.Auth
				var logs bytes.Buffer
				log := slog.New(slog.NewJSONHandler(&logs, nil))
				err := applyEnvOverrides(log, &cfg)
				if err == nil || !strings.Contains(err.Error(), prefix+"_PORT") {
					t.Fatal("incomplete endpoint must return a named startup error")
				}
				// Match main's error logging, without starting any services.
				log.Error("invalid environment override", "err", err)
				if strings.Contains(logs.String(), "private-") {
					t.Fatal("startup error exposed credentials")
				}
				if cfg.Auth != before {
					t.Fatal("invalid endpoint must not silently select a fallback")
				}
			})
		}
	}
}
