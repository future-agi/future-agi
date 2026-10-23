package guardrails

import (
	"context"
	"errors"
	"fmt"
	"net"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"strings"
	"syscall"
	"testing"
	"time"
)

// CallFailure turns the errors an outbound guardrail call returns into a kind
// of failure that names no URL, host, address or path, because a guardrail's
// message reaches the caller and the org's request log. The guardrail's own
// errors (an HTTP status, a response that does not parse) are kept as they are.
func TestCallFailure(t *testing.T) {
	const host = "t-operator-host.internal"
	slow := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		time.Sleep(300 * time.Millisecond)
	}))
	defer slow.Close()
	tlsSrv := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {}))
	defer tlsSrv.Close()

	do := func(client *http.Client, method, target string) error {
		req, err := http.NewRequest(method, target, nil)
		if err != nil {
			return err
		}
		resp, err := client.Do(req)
		if err == nil {
			resp.Body.Close()
		}
		return err
	}

	cases := []struct {
		name string
		err  error
		want string
	}{
		// The shape http.Client.Do returns for a refused dial, built here so
		// the case does not depend on how the host treats a closed port.
		{"connection refused", fmt.Errorf("failed after 1 attempts: %w", &url.Error{Op: "Post",
			URL: "http://127.0.0.1:9/t-path", Err: &net.OpError{Op: "dial", Net: "tcp",
				Addr: &net.TCPAddr{IP: net.IPv4(127, 0, 0, 1), Port: 9},
				Err:  &os.SyscallError{Syscall: "connect", Err: syscall.ECONNREFUSED}}}), "the connection was refused"},
		{"client timeout", do(&http.Client{Timeout: 20 * time.Millisecond}, http.MethodPost, slow.URL+"/t-path"),
			"the request timed out"},
		{"untrusted TLS certificate", do(http.DefaultClient, http.MethodPost, tlsSrv.URL+"/t-path"),
			"the TLS certificate was not accepted"},
		{"an invalid URL", do(http.DefaultClient, http.MethodPost, "http://[::1/t-path"), "the URL is not valid"},
		{"host name not found", &url.Error{Op: "Post", URL: "http://" + host + "/t-path", Err: &net.OpError{
			Op: "dial", Net: "tcp", Err: &net.DNSError{Err: "no such host", Name: host, IsNotFound: true}}},
			"the host name could not be resolved"},
		{"a reset while reading the response", fmt.Errorf("read response: %w", &net.OpError{Op: "read", Net: "tcp",
			Addr: &net.TCPAddr{IP: net.IPv4(10, 1, 2, 3), Port: 8000}, Err: syscall.ECONNRESET}), "the connection failed"},
		{"context deadline", fmt.Errorf("failed: %w", context.DeadlineExceeded), "the request timed out"},
		{"context cancelled", context.Canceled, "the request was cancelled"},
		{"the guardrail's own status error", errors.New("unexpected status 500"), "unexpected status 500"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if tc.err == nil {
				t.Fatal("the case produced no error to describe")
			}
			got := CallFailure(tc.err)
			if got != tc.want {
				t.Fatalf("CallFailure(%v) = %q, want %q", tc.err, got, tc.want)
			}
			for _, detail := range []string{"127.0.0.1", "10.1.2.3", "t-path", host, "[::1"} {
				if strings.Contains(got, detail) {
					t.Fatalf("CallFailure names %q: %q", detail, got)
				}
			}
		})
	}
	if got := CallFailure(nil); got != "" {
		t.Fatalf("CallFailure(nil) = %q, want empty", got)
	}
}
