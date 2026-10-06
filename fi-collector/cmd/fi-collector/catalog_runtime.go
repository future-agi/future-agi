package main

import (
	"context"
	"log/slog"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/observedcatalog"
)

// runObservedReplay drains the spool with replay (Writer.Replay for Kafka,
// Writer.ReplayMerged for the direct ClickHouse sink) until ctx ends.
func runObservedReplay(ctx context.Context, replay func(context.Context, observedcatalog.Publisher) (int, error), publisher observedcatalog.Publisher, interval time.Duration, log *slog.Logger) {
	timer := time.NewTicker(interval)
	defer timer.Stop()
	for {
		count, err := replay(ctx, publisher)
		if err != nil && ctx.Err() == nil {
			log.Error("observed catalog replay failed; spool retained", "delivered", count, "err", err)
		}
		select {
		case <-ctx.Done():
			return
		case <-timer.C:
		}
	}
}
