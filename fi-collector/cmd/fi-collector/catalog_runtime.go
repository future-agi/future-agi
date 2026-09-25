package main

import (
	"context"
	"log/slog"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/observedcatalog"
)

func runObservedReplay(ctx context.Context, writer *observedcatalog.Writer, publisher observedcatalog.Publisher, interval time.Duration, log *slog.Logger) {
	timer := time.NewTicker(interval)
	defer timer.Stop()
	for {
		count, err := writer.Replay(ctx, publisher)
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
