package validation_test

import (
	"bytes"
	"log/slog"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/guardrails/validation"
)

func TestSelectedConfigInvalidPatternLogsSanitized(t *testing.T) {
	// Invalid legacy patterns still warn and are skipped, but constructors also
	// run on selected config: neither the pattern nor regexp's raw error may leak.
	var logs bytes.Buffer
	original := slog.Default()
	slog.SetDefault(slog.New(slog.NewTextHandler(&logs, nil)))
	t.Cleanup(func() { slog.SetDefault(original) })
	for _, key := range []string{"deny_patterns", "require_patterns"} {
		logs.Reset()
		validation.New(map[string]interface{}{key: []interface{}{"[private-pattern-sentinel"}})
		if logs.Len() == 0 {
			t.Fatal("legacy invalid-pattern warning lost")
		}
		if strings.Contains(logs.String(), "private-pattern-sentinel") {
			t.Fatal("invalid-pattern log contains raw configuration")
		}
	}
}
