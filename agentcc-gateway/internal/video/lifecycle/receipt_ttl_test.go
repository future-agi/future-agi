package lifecycle

import (
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

func TestReceiptTTLIsRetentionPlusSettlementWindow(t *testing.T) {
	cfg := config.DefaultConfig().Video
	if got := ReceiptTTL(cfg); got != 744*time.Hour {
		t.Fatalf("default receipt ttl=%s, want 744h", got)
	}
	cfg.Retention.JobMetadata = 48 * time.Hour
	if got := ReceiptTTL(cfg); got != 72*time.Hour {
		t.Fatalf("receipt ttl=%s, want 72h", got)
	}
}
