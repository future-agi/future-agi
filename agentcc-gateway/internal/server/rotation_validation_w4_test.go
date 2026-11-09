package server

import (
	"errors"
	"fmt"
	"net/url"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers/common"
)

func TestR7W4ValidationNeverPublishesDiagnostics(t *testing.T) {
	const secret = "opaque-held-credential"
	transport := &url.Error{Op: "Get", URL: "http://fixture.invalid/models?key=" + secret, Err: errors.New("dial failed " + secret)}
	safe := common.ExactError(transport, secret)
	var recovered *url.Error
	if errors.As(safe, &recovered) {
		t.Fatal("sanitizer exposes original transport")
	}
	for _, err := range []error{safe, fmt.Errorf("wrapped: %w", safe), common.ExactError(safe, secret)} {
		got := keyFreeError(err, secret).Error()
		if !strings.Contains(got, "got no answer from the provider") || strings.Contains(got, "http") || strings.Contains(got, "dial failed") || strings.Contains(got, secret) {
			t.Fatalf("unsafe classification: %q", got)
		}
	}
	for _, err := range []error{errors.New("unknown " + secret), &models.APIError{Status: 401, Type: secret, Code: secret, Message: secret}} {
		if got := keyFreeError(err, secret).Error(); strings.Contains(got, secret) || strings.Contains(got, "unknown ") {
			t.Fatalf("diagnostics escaped: %q", got)
		}
	}
}
