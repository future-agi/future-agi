// Command fi-property-catalog-consumer consumes only the observed-attribute
// protocol. Older sequenced catalog records require their original consumer.
package main

import (
	"context"
	"fmt"
	"log/slog"
	"os"
	"os/signal"
	"syscall"

	"github.com/future-agi/future-agi/fi-collector/pkg/observedcatalog"
)

func run(ctx context.Context, getenv func(string) string) error {
	for _, key := range []string{"FI_CATALOG_MODE", "FI_PROPERTY_CATALOG_MODE"} {
		if v := getenv(key); v != "" && v != "disabled" {
			return fmt.Errorf("%s is obsolete", key)
		}
	}
	kafka, err := observedcatalog.KafkaConfigFromEnv(getenv)
	if err != nil {
		return err
	}
	cfg, err := observedcatalog.ClickHouseConfigFromEnv(getenv)
	if err != nil {
		return err
	}
	sink, err := observedcatalog.NewClickHouseSink(cfg)
	if err != nil {
		return err
	}
	consumer, err := observedcatalog.NewConsumer(kafka, sink)
	if err != nil {
		return err
	}
	defer consumer.Close()
	return consumer.Run(ctx)
}

func main() {
	// Reject old sequencer/ledger flags instead of silently reinterpreting them.
	if len(os.Args) != 1 {
		slog.Error("observed consumer accepts environment configuration only")
		os.Exit(1)
	}
	ctx, cancel := signal.NotifyContext(context.Background(), syscall.SIGTERM, syscall.SIGINT)
	defer cancel()
	if err := run(ctx, os.Getenv); err != nil && ctx.Err() == nil {
		slog.Error("observed consumer stopped without committing failed work", "err", err)
		os.Exit(1)
	}
}
