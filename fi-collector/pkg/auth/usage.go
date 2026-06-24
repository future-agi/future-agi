package auth

import (
	"context"
	"fmt"
	"log/slog"
	"strconv"
	"time"

	"github.com/google/uuid"
	"github.com/redis/go-redis/v9"
)

const (
	usageStreamKey = "usage:events"
	usageMaxLen    = 1_000_000
)

// UsageEmitter writes billing events to the Redis Stream consumed by
// the Temporal UsageConsumerWorkflow. Same stream + schema as the Python
// emitter (ee/usage/services/emitter.py).
type UsageEmitter struct {
	rdb *redis.Client
	log *slog.Logger
}

// NewUsageEmitter creates an emitter. Returns nil if rdb is nil (disabled).
func NewUsageEmitter(rdb *redis.Client, log *slog.Logger) *UsageEmitter {
	if rdb == nil {
		return nil
	}
	return &UsageEmitter{rdb: rdb, log: log}
}

// EmitIngestion records trace and storage usage for a batch of spans.
// Fire-and-forget — errors are logged but never returned.
func (u *UsageEmitter) EmitIngestion(orgID string, numTraces, numSpans int, payloadBytes int64) {
	if u == nil {
		return
	}

	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()

	now := time.Now().UTC().Format(time.RFC3339)

	if numTraces > 0 {
		u.xadd(ctx, map[string]any{
			"event_id":   uuid.New().String(),
			"org_id":     orgID,
			"event_type": "tracing_event",
			"timestamp":  now,
			"amount":     strconv.Itoa(numTraces),
			"properties": fmt.Sprintf(`{"traces":%d,"source":"fi-collector"}`, numTraces),
		})
	}

	if payloadBytes > 0 {
		u.xadd(ctx, map[string]any{
			"event_id":   uuid.New().String(),
			"org_id":     orgID,
			"event_type": "observe_add",
			"timestamp":  now,
			"amount":     strconv.FormatInt(payloadBytes, 10),
			"properties": fmt.Sprintf(`{"spans":%d,"source":"fi-collector"}`, numSpans),
		})
	}
}

func (u *UsageEmitter) xadd(ctx context.Context, fields map[string]any) {
	err := u.rdb.XAdd(ctx, &redis.XAddArgs{
		Stream: usageStreamKey,
		MaxLen: usageMaxLen,
		Approx: true,
		Values: fields,
	}).Err()
	if err != nil {
		u.log.Warn("usage event emit failed", "err", err)
	}
}
