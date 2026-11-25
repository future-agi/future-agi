package observedcatalog

import (
	"context"
	"crypto/tls"
	"crypto/x509"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"syscall"
	"testing"
	"time"
)

// Endpoint identity and scheme errors are configuration failures and must stop
// the consumer; an unreachable server is an outage and must be retried.
func TestClickHouseTransportFailureClassification(t *testing.T) {
	tlsServer := httptest.NewTLSServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {}))
	defer tlsServer.Close()
	plainServer := httptest.NewServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {}))
	defer plainServer.Close()
	closed := httptest.NewServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {}))
	closedURL := closed.URL
	closed.Close()

	for _, tc := range []struct {
		name      string
		url       string
		transport func() http.RoundTripper
		wantRetry bool
	}{
		{"untrusted certificate", tlsServer.URL, nil, false},
		{"hostname mismatch", tlsServer.URL, func() http.RoundTripper {
			transport := tlsServer.Client().Transport.(*http.Transport).Clone()
			transport.TLSClientConfig.ServerName = "clickhouse.mismatch.invalid"
			return transport
		}, false},
		{"HTTPS to plain HTTP", strings.Replace(plainServer.URL, "http://", "https://", 1), nil, false},
		{"connection refused", closedURL, nil, true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			sink, err := NewClickHouseSink(ClickHouseConfig{URL: tc.url, Database: "test", Timeout: 2 * time.Second})
			if err != nil {
				t.Fatal(err)
			}
			if tc.transport != nil {
				sink.client.Transport = tc.transport()
			}
			err = sink.Insert(context.Background(), testBatch(t))
			var transient transientFailure
			if err == nil || errors.As(err, &transient) != tc.wantRetry {
				t.Fatalf("Insert: %v (retryable=%t)", err, errors.As(err, &transient))
			}
			if strings.Contains(err.Error(), tc.url) || strings.Contains(err.Error(), "127.0.0.1") {
				t.Fatalf("error exposes the endpoint: %v", err)
			}
		})
	}
}

// The INSERT request classifies transport errors the same way as the topology
// query that precedes it.
type insertRejectingTransport struct{ err error }

func (rt insertRejectingTransport) RoundTrip(r *http.Request) (*http.Response, error) {
	if strings.HasPrefix(r.URL.Query().Get("query"), "SELECT") {
		return &http.Response{StatusCode: http.StatusOK, Header: http.Header{}, Request: r,
			Body: io.NopCloser(strings.NewReader(`{"engine":"AggregatingMergeTree"}` + "\n"))}, nil
	}
	return nil, rt.err
}

func TestClickHouseInsertTransportFailureClassification(t *testing.T) {
	for _, tc := range []struct {
		name      string
		err       error
		wantRetry bool
	}{
		{"untrusted certificate", &tls.CertificateVerificationError{Err: x509.UnknownAuthorityError{}}, false},
		{"connection reset", syscall.ECONNRESET, true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			sink, err := NewClickHouseSink(ClickHouseConfig{URL: "https://clickhouse.invalid", Database: "test"})
			if err != nil {
				t.Fatal(err)
			}
			sink.client.Transport = insertRejectingTransport{tc.err}
			err = sink.Insert(context.Background(), testBatch(t))
			var transient transientFailure
			if err == nil || !strings.Contains(err.Error(), "ClickHouse request failed") || errors.As(err, &transient) != tc.wantRetry {
				t.Fatalf("Insert: %v (retryable=%t)", err, errors.As(err, &transient))
			}
		})
	}
}
