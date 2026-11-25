package observedcatalog

import (
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
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

func TestTransportFailureRetriesTimeouts(t *testing.T) {
	var transient transientFailure
	if err := transportFailure(context.DeadlineExceeded, "request failed"); !errors.As(err, &transient) {
		t.Fatalf("timeout not retried: %v", err)
	}
}
