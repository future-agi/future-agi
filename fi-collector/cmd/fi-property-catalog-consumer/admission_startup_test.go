package main

import (
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/propertycatalog"
)

func TestConsumerWaitsForAdmissionBeforeInventoryAndKafka(t *testing.T) {
	attempts := []time.Time{}
	stop := errors.New("test complete")
	consumer := &fakeConsumer{runErr: stop}
	inventory, kafka, cleanup := 0, 0, 0
	deps := dependencies{
		newSink: func(propertycatalog.ClickHouseSinkConfig) (propertycatalog.DeliverySink, error) {
			return &fakeSink{}, nil
		},
		newLoader: func(propertycatalog.ClickHouseSinkConfig, propertycatalog.CheckpointLoaderLimits) (checkpointLeaseReader, error) {
			attempts = append(attempts, time.Now())
			if len(attempts) < 3 {
				if inventory != 0 || kafka != 0 {
					t.Fatal("startup pending crossed an I/O boundary")
				}
				return nil, fmt.Errorf("descriptor %d missing: %w", len(attempts), propertycatalog.ErrWriteAdmissionPending)
			}
			return &fakeLoader{onLoad: func() { inventory++ }}, nil
		},
		newConsumer: func(propertycatalog.FranzConsumerConfig, propertycatalog.Handler, *propertycatalog.SequenceValidator) (runningConsumer, error) {
			kafka++
			return consumer, nil
		},
		close: func() { cleanup++ },
	}
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	err := run(ctx, []string{"--start-sequence-one-only"}, mapLookup(validEnvironment()), deps)
	if !errors.Is(err, stop) || len(attempts) != 3 || inventory != 1 || kafka != 1 || cleanup != 1 || consumer.closed != 1 {
		t.Fatalf("startup=%v attempts=%d inventory=%d kafka=%d cleanup=%d close=%d", err, len(attempts), inventory, kafka, cleanup, consumer.closed)
	}
	if attempts[1].Sub(attempts[0]) < admissionRetryInitial || attempts[2].Sub(attempts[1]) < 2*admissionRetryInitial {
		t.Fatal("missing publication was retried without backoff")
	}
}

func TestConsumerRealFactoryWaitsForMissingFilesWithoutStateOrKafka(t *testing.T) {
	directory := t.TempDir()
	values := validEnvironment()
	values[envRevisionFence] = filepath.Join(directory, "revision-fence.json")
	deps := defaultDependencies()
	original := deps.newLoader
	attempts := 0
	deps.newLoader = func(cfg propertycatalog.ClickHouseSinkConfig, limits propertycatalog.CheckpointLoaderLimits) (checkpointLeaseReader, error) {
		attempts++
		return original(cfg, limits)
	}
	deps.newConsumer = func(propertycatalog.FranzConsumerConfig, propertycatalog.Handler, *propertycatalog.SequenceValidator) (runningConsumer, error) {
		t.Fatal("Kafka constructed during descriptor initialization")
		return nil, nil
	}
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Millisecond)
	defer cancel()
	err := run(ctx, []string{"--start-sequence-one-only"}, mapLookup(values), deps)
	if !errors.Is(err, context.DeadlineExceeded) || !errors.Is(err, propertycatalog.ErrWriteAdmissionPending) || attempts != 1 {
		t.Fatalf("pending startup did not honor context/backoff: error=%v attempts=%d", err, attempts)
	}
	entries, err := os.ReadDir(directory)
	if err != nil || len(entries) != 0 {
		t.Fatalf("pending startup created files/journal: %v error=%v", entries, err)
	}
}

func TestConsumerAdmissionWaitCancellationAndGenuineErrorsDoNotRetry(t *testing.T) {
	for _, kind := range []string{"cancel", "corrupt", "raw-ENOENT", "pending-then-corrupt"} {
		t.Run(kind, func(t *testing.T) {
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			genuine := errors.New("corrupt admission")
			if kind == "raw-ENOENT" {
				genuine = os.ErrNotExist
			}
			attempts := 0
			_, err := loadWithAdmissionWait(ctx, propertycatalog.ClickHouseSinkConfig{}, propertycatalog.CheckpointLoaderLimits{},
				func(propertycatalog.ClickHouseSinkConfig, propertycatalog.CheckpointLoaderLimits) (checkpointLeaseReader, error) {
					attempts++
					if kind == "cancel" {
						cancel()
						return nil, propertycatalog.ErrWriteAdmissionPending
					}
					if kind == "pending-then-corrupt" && attempts == 1 {
						return nil, propertycatalog.ErrWriteAdmissionPending
					}
					return nil, genuine
				})
			wantAttempts := 1
			if kind == "pending-then-corrupt" {
				wantAttempts = 2
			}
			if attempts != wantAttempts {
				t.Fatalf("genuine error retried %d times", attempts)
			}
			if kind == "cancel" {
				if !errors.Is(err, context.Canceled) {
					t.Fatalf("shutdown became fatal: %v", err)
				}
			} else if !errors.Is(err, genuine) || errors.Is(err, propertycatalog.ErrWriteAdmissionPending) {
				t.Fatalf("genuine startup error hidden: %v", err)
			}
		})
	}
}
