package gauth

import (
	"net/http"
	"testing"
)

// UseTokenTransport routes the token exchange through rt for the rest of one test, so a test
// can answer it from a local server instead of Google. It is compiled into tests only.
func UseTokenTransport(t *testing.T, rt http.RoundTripper) {
	t.Helper()
	old := tokenClient
	tokenClient = &http.Client{Timeout: old.Timeout, Transport: rt}
	t.Cleanup(func() { tokenClient = old })
}
