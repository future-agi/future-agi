package main

import (
	"context"
	"errors"
	"log/slog"
	"path/filepath"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/catalogkafka"
	"github.com/future-agi/future-agi/fi-collector/pkg/catalogwriter"
)

const (
	defaultCatalogReplayInterval = time.Second

	// Version-3 records are hard-capped at 768 KiB. Rows are base64 encoded
	// inside the integrity-bound envelope, so a 384 KiB job leaves room for
	// that expansion and fixed metadata.
	catalogV3MaxJobEncodedBytes      = 384 << 10
	catalogV3MaxChunkEncodedBytes    = 256 << 10
	kafkaPublisherStateDirectoryName = "kafka-publisher-state"
)

// newDirectCatalogWriter constructs the same transport-only WAL and durable
// per-project version-3 sequencer used by Kafka mode. Its terminal publisher
// is a bounded ClickHouse handler instead of a broker producer.
func newDirectCatalogWriter(
	cfg catalogwriter.RuntimeConfig,
) (*catalogwriter.Writer, *catalogkafka.SpoolPublisher, error) {
	mode, err := cfg.SelectedMode()
	if err != nil {
		return nil, nil, err
	}
	if mode != catalogwriter.ModeDirect {
		return nil, nil, errors.New("catalog runtime: direct writer requires direct mode")
	}
	sink, err := catalogwriter.NewClickHouseSink(cfg.ClickHouse)
	if err != nil {
		return nil, nil, err
	}
	directPublisher, err := catalogkafka.NewDirectClickHouseEnvelopePublisher(
		sink, cfg.ClickHouse.RequestTimeout,
	)
	if err != nil {
		return nil, nil, err
	}
	writerCfg := catalogV3WriterConfig(cfg)
	writer, err := catalogwriter.NewTransportWriter(writerCfg)
	if err != nil {
		return nil, nil, err
	}
	publisher, err := catalogkafka.NewSpoolPublisher(catalogkafka.PublisherConfig{
		StateDirectory: filepath.Join(
			cfg.SpoolDir, catalogkafka.DirectPublisherStateDirectoryName,
		),
		ProducerStreamID: cfg.ProducerStream,
		MaxChunkRows:     writerCfg.MaxChunkRows,
		MaxChunkBytes:    catalogV3MaxChunkEncodedBytes,
	}, directPublisher)
	if err != nil {
		return nil, nil, err
	}
	return writer, publisher, nil
}

// newKafkaCatalogWriter constructs only the transport WAL and its crash-safe
// sequencer. The supplied publisher must synchronously acknowledge Kafka.
func newKafkaCatalogWriter(
	cfg catalogwriter.RuntimeConfig,
	producer catalogkafka.EnvelopePublisher,
) (*catalogwriter.Writer, *catalogkafka.SpoolPublisher, error) {
	mode, err := cfg.SelectedMode()
	if err != nil {
		return nil, nil, err
	}
	if mode != catalogwriter.ModeKafka {
		return nil, nil, errors.New("catalog runtime: Kafka writer requires Kafka mode")
	}
	if producer == nil {
		return nil, nil, errors.New("catalog runtime: Kafka writer requires a synchronous producer")
	}

	writerCfg := catalogV3WriterConfig(cfg)
	writer, err := catalogwriter.NewTransportWriter(writerCfg)
	if err != nil {
		return nil, nil, err
	}
	publisher, err := catalogkafka.NewSpoolPublisher(catalogkafka.PublisherConfig{
		StateDirectory:   filepath.Join(cfg.SpoolDir, kafkaPublisherStateDirectoryName),
		ProducerStreamID: cfg.ProducerStream,
		MaxChunkRows:     writerCfg.MaxChunkRows,
		MaxChunkBytes:    catalogV3MaxChunkEncodedBytes,
	}, producer)
	if err != nil {
		return nil, nil, err
	}
	return writer, publisher, nil
}

func catalogV3WriterConfig(cfg catalogwriter.RuntimeConfig) catalogwriter.Config {
	writerCfg := catalogwriter.DefaultConfig()
	writerCfg.Enabled = true
	writerCfg.CatalogEpoch = cfg.CatalogEpoch
	writerCfg.SpoolDir = cfg.SpoolDir
	writerCfg.MaxJobProjects = 1
	writerCfg.MaxJobEncodedBytes = catalogV3MaxJobEncodedBytes
	writerCfg.MaxChunkEncodedBytes = catalogV3MaxChunkEncodedBytes
	return writerCfg
}

func runDirectCatalogReplay(
	ctx context.Context,
	writer *catalogwriter.Writer,
	publisher *catalogkafka.SpoolPublisher,
	interval time.Duration,
	log *slog.Logger,
) {
	runCatalogReplay(ctx, writer, publisher, interval, log, "direct")
}

func runKafkaCatalogReplay(
	ctx context.Context,
	writer *catalogwriter.Writer,
	publisher *catalogkafka.SpoolPublisher,
	interval time.Duration,
	log *slog.Logger,
) {
	runCatalogReplay(ctx, writer, publisher, interval, log, "Kafka")
}

// runCatalogReplay drains only the catalog WAL. ReplayTo deletes an outer WAL
// record only after its durable sequencer and destination have acknowledged it.
func runCatalogReplay(
	ctx context.Context,
	writer *catalogwriter.Writer,
	publisher *catalogkafka.SpoolPublisher,
	interval time.Duration,
	log *slog.Logger,
	transport string,
) {
	if writer == nil || !writer.Enabled() || publisher == nil {
		return
	}
	if interval <= 0 {
		interval = defaultCatalogReplayInterval
	}
	ticker := time.NewTicker(interval)
	defer ticker.Stop()
	for {
		result, err := writer.ReplayTo(ctx, publisher)
		if err != nil && ctx.Err() == nil {
			log.Warn(
				"attribute catalog replay failed", "transport", transport,
				"attempted", result.Attempted, "delivered", result.Delivered,
				"quarantined", result.Quarantined, "err", err,
			)
		} else if result.Delivered > 0 {
			log.Info("attribute catalog replay", "transport", transport, "delivered", result.Delivered)
		}
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
		}
	}
}

func logCatalogSubmissionGaps(
	ctx context.Context,
	submitter *catalogwriter.AsyncSubmitter,
	log *slog.Logger,
) {
	if submitter == nil {
		return
	}
	for {
		select {
		case <-ctx.Done():
			return
		case err := <-submitter.Gaps():
			log.Error("attribute catalog WAL gap; epoch remains unqualified", "err", err)
		case <-submitter.OverflowWake():
			if summary, ok := submitter.TakeOverflow(); ok {
				logCatalogSubmissionOverflow(log, summary)
			}
		}
	}
}

func logCatalogSubmissionOverflow(log *slog.Logger, summary catalogwriter.SubmissionGapOverflow) {
	log.Error(
		"attribute catalog WAL gap overflow; epoch remains unqualified",
		"suppressed_count", summary.Suppressed,
		"first_err", summary.First,
		"last_err", summary.Last,
	)
}

func drainCatalogSubmissionGaps(
	submitter *catalogwriter.AsyncSubmitter,
	log *slog.Logger,
) {
	if submitter == nil {
		return
	}
	for {
		select {
		case err := <-submitter.Gaps():
			log.Error("attribute catalog WAL gap; epoch remains unqualified", "err", err)
		case <-submitter.OverflowWake():
			if summary, ok := submitter.TakeOverflow(); ok {
				logCatalogSubmissionOverflow(log, summary)
			}
		default:
			return
		}
	}
}
