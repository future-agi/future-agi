package main

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/propertycatalog"
)

func mapLookup(values map[string]string) lookupEnvFunc {
	return func(name string) (string, bool) {
		value, present := values[name]
		return value, present
	}
}

func validSequencerEnvironment(t *testing.T) map[string]string {
	t.Helper()
	return map[string]string{
		envMode:              string(propertycatalog.RuntimeSequencer),
		envEnvironment:       propertycatalog.DevelopmentEnvironment,
		envDevAck:            propertycatalog.DevelopmentAcknowledgement,
		envEpoch:             "3",
		envProjection:        "1",
		envStreamID:          "44444444-4444-4444-8444-444444444444",
		envScopeMode:         string(propertycatalog.WorkspaceScopeRevisionFence),
		envFenceFile:         filepath.Join(t.TempDir(), "revision-fence.json"),
		envSpoolDir:          t.TempDir(),
		envOutputBrokers:     "kafka-1:9092,kafka-2:9092",
		envOutputTopic:       "futureagi.dev.property-catalog.ordered.v1",
		envTransactionalID:   "futureagi-property-catalog-sequencer-dev-v1",
		envCandidateBrokers:  "kafka-1:9092,kafka-2:9092",
		envCandidateTopic:    "futureagi.dev.property-catalog.candidates.v1",
		envCandidateGroup:    "futureagi-property-catalog-sequencer-dev-v1",
		envCandidateInstance: "futureagi-property-catalog-sequencer-dev-v1",
	}
}

func TestSequencerConfigRequiresTwoTopicsAndFixedOwnerIdentities(t *testing.T) {
	values := validSequencerEnvironment(t)
	cfg, err := loadConfig(mapLookup(values))
	if err != nil {
		t.Fatal(err)
	}
	if cfg.runtime.Mode != propertycatalog.RuntimeSequencer ||
		cfg.runtime.WorkspaceScopeMode != propertycatalog.WorkspaceScopeRevisionFence ||
		cfg.output.TransactionalID != values[envTransactionalID] ||
		cfg.candidate.InstanceID != values[envCandidateInstance] ||
		cfg.candidate.Topic == cfg.output.Topic ||
		cfg.receipts.Directory != filepath.Join(values[envSpoolDir], "candidate-receipts") ||
		cfg.startupTimeout != defaultStartupTimeout {
		t.Fatalf("sequencer config=%+v", cfg)
	}
	if _, err := os.Stat(cfg.receipts.Directory); !os.IsNotExist(err) {
		t.Fatalf("configuration parsing mutated durable state: %v", err)
	}
}

func TestSequencerConfigFailsClosedBeforeRuntimeConstruction(t *testing.T) {
	for name, mutate := range map[string]func(map[string]string){
		"missing transaction identity":  func(values map[string]string) { delete(values, envTransactionalID) },
		"missing static group identity": func(values map[string]string) { delete(values, envCandidateInstance) },
		"same input and output topic":   func(values map[string]string) { values[envCandidateTopic] = values[envOutputTopic] },
		"collector candidate mode":      func(values map[string]string) { values[envMode] = string(propertycatalog.RuntimeKafka) },
		"bad transaction timeout":       func(values map[string]string) { values[envTransactionTimeout] = "121s" },
		"bad startup timeout":           func(values map[string]string) { values[envStartupTimeout] = "0s" },
		"dynamic Kafka member":          func(values map[string]string) { values[envCandidateInstance] = "" },
		"unknown workspace scope":       func(values map[string]string) { values[envScopeMode] = "all" },
	} {
		t.Run(name, func(t *testing.T) {
			values := validSequencerEnvironment(t)
			mutate(values)
			if _, err := loadConfig(mapLookup(values)); err == nil {
				t.Fatal("unsafe sequencer configuration was accepted")
			}
		})
	}
}

func TestProductionSequencerRequiresExactProductionGate(t *testing.T) {
	values := validSequencerEnvironment(t)
	values[envEnvironment] = propertycatalog.ProductionEnvironment
	delete(values, envDevAck)
	values[envProdAck] = propertycatalog.ProductionAcknowledgement
	values[envOutputTopic] = "futureagi.prod.property-catalog.ordered.v1"
	values[envCandidateTopic] = "futureagi.prod.property-catalog.candidates.v1"
	cfg, err := loadConfig(mapLookup(values))
	if err != nil {
		t.Fatal(err)
	}
	if cfg.runtime.Environment != propertycatalog.ProductionEnvironment ||
		cfg.runtime.ProductionAcknowledgement != propertycatalog.ProductionAcknowledgement {
		t.Fatalf("production config=%+v", cfg.runtime)
	}

	for _, bad := range []string{"", propertycatalog.DevelopmentAcknowledgement, "yes"} {
		broken := validSequencerEnvironment(t)
		broken[envEnvironment] = propertycatalog.ProductionEnvironment
		delete(broken, envDevAck)
		if bad != "" {
			broken[envProdAck] = bad
		}
		if _, err := loadConfig(mapLookup(broken)); err == nil ||
			!strings.Contains(err.Error(), "production") {
			t.Fatalf("production acknowledgement %q error=%v", bad, err)
		}
	}
}

func TestSequencerOperationalBoundsAreEnvironmentDriven(t *testing.T) {
	values := validSequencerEnvironment(t)
	values[envStartupTimeout] = "8s"
	values[envTransactionTimeout] = "45s"
	values[envReceiptFiles] = "123"
	values[envReceiptBytes] = "456789"
	values[envRecentCandidateIDs] = "321"
	values[envReplayInterval] = "2s"
	cfg, err := loadConfig(mapLookup(values))
	if err != nil {
		t.Fatal(err)
	}
	if cfg.startupTimeout != 8*time.Second || cfg.output.TransactionTimeout != 45*time.Second ||
		cfg.receipts.MaxPendingFiles != 123 || cfg.receipts.MaxPendingBytes != 456789 ||
		cfg.receipts.MaxRecentIDs != 321 || cfg.runtime.ReplayInterval != 2*time.Second {
		t.Fatalf("operational config=%+v", cfg)
	}
}

func managedSequencerEnvironment(t *testing.T) map[string]string {
	t.Helper()
	values := validSequencerEnvironment(t)
	delete(values, envEpoch)
	delete(values, envProjection)
	delete(values, envStreamID)
	return values
}

func sequencerIdentityFixture(t *testing.T) []byte {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join("..", "..", "pkg", "propertycatalog", "testdata", "runtime_identity_v1.json"))
	if err != nil {
		t.Fatal(err)
	}
	return raw
}

func sequencerIdentityPath(values map[string]string) string {
	return filepath.Join(filepath.Dir(values[envFenceFile]), propertycatalog.InstallationIdentityFilename)
}

func publishSequencerIdentity(t *testing.T, values map[string]string, raw []byte) {
	t.Helper()
	path := sequencerIdentityPath(values)
	// Simulate the lifecycle's complete-file atomic publication. No production
	// Go identity encoder or writer is available to the sequencer.
	if err := os.WriteFile(path+".pending", raw, 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.Rename(path+".pending", path); err != nil {
		t.Fatal(err)
	}
}

func requireSequencerSpoolUntouched(t *testing.T, values map[string]string) {
	t.Helper()
	entries, err := os.ReadDir(values[envSpoolDir])
	if err != nil || len(entries) != 0 {
		t.Fatalf("startup configuration touched spool: entries=%v err=%v", entries, err)
	}
}

func TestManagedSequencerLoadsPersistedIdentityAndLegacyMatchesIt(t *testing.T) {
	for _, legacy := range []bool{false, true} {
		t.Run(fmt.Sprintf("legacy=%t", legacy), func(t *testing.T) {
			values := validSequencerEnvironment(t)
			if !legacy {
				values = managedSequencerEnvironment(t)
			}
			raw := sequencerIdentityFixture(t)
			publishSequencerIdentity(t, values, raw)
			for range 2 {
				cfg, err := loadConfig(mapLookup(values))
				if err != nil {
					t.Fatal(err)
				}
				if cfg.runtime.CatalogEpoch != 3 || cfg.runtime.ProjectionVersion != 1 ||
					cfg.runtime.ProducerStreamID != "44444444-4444-4444-8444-444444444444" {
					t.Fatalf("persisted identity not applied: %+v", cfg.runtime)
				}
			}
			actual, err := os.ReadFile(sequencerIdentityPath(values))
			if err != nil || !bytes.Equal(actual, raw) {
				t.Fatalf("configuration mutated identity: err=%v", err)
			}
			requireSequencerSpoolUntouched(t, values)
		})
	}
}

func TestSequencerRejectsPartialOrEmptyLegacyIdentityEvenWithDescriptor(t *testing.T) {
	keys := []string{envEpoch, envProjection, envStreamID}
	for _, persisted := range []bool{false, true} {
		for mask := 1; mask < 7; mask++ {
			t.Run(fmt.Sprintf("persisted=%t mask=%d", persisted, mask), func(t *testing.T) {
				values := validSequencerEnvironment(t)
				for index, key := range keys {
					if mask&(1<<index) == 0 {
						delete(values, key)
					}
				}
				if persisted {
					publishSequencerIdentity(t, values, sequencerIdentityFixture(t))
				}
				if _, err := loadConfig(mapLookup(values)); err == nil || errors.Is(err, propertycatalog.ErrInstallationIdentityMissing) {
					t.Fatalf("partial legacy configuration err=%v", err)
				}
			})
		}
		for _, key := range keys {
			t.Run(fmt.Sprintf("persisted=%t empty=%s", persisted, key), func(t *testing.T) {
				values := validSequencerEnvironment(t)
				values[key] = ""
				if persisted {
					publishSequencerIdentity(t, values, sequencerIdentityFixture(t))
				}
				if _, err := loadConfig(mapLookup(values)); err == nil {
					t.Fatal("empty legacy identity variable accepted")
				}
			})
		}
	}
}

func TestSequencerPersistedIdentityRequiresExactDestinationAndLegacyTriple(t *testing.T) {
	for _, legacy := range []bool{false, true} {
		for _, key := range []string{envEnvironment, envCandidateTopic, envOutputTopic, envEpoch, envProjection, envStreamID} {
			if !legacy && (key == envEpoch || key == envProjection || key == envStreamID) {
				continue
			}
			t.Run(fmt.Sprintf("legacy=%t key=%s", legacy, key), func(t *testing.T) {
				values := validSequencerEnvironment(t)
				if !legacy {
					values = managedSequencerEnvironment(t)
				}
				publishSequencerIdentity(t, values, sequencerIdentityFixture(t))
				switch key {
				case envEnvironment:
					values[key] = propertycatalog.ProductionEnvironment
					delete(values, envDevAck)
					values[envProdAck] = propertycatalog.ProductionAcknowledgement
				case envEpoch, envProjection:
					values[key] = "2"
				case envStreamID:
					values[key] = "55555555-5555-4555-8555-555555555555"
				default:
					values[key] += ".other"
				}
				if _, err := loadConfig(mapLookup(values)); err == nil || !strings.Contains(err.Error(), "conflicts") {
					t.Fatalf("identity mismatch err=%v", err)
				}
				requireSequencerSpoolUntouched(t, values)
			})
		}
	}
}

func TestLegacySequencerStillValidatesIdentityWithoutDescriptor(t *testing.T) {
	for _, test := range []struct{ key, value string }{
		{envEpoch, "0"}, {envEpoch, "-1"}, {envEpoch, "65536"}, {envEpoch, "1.0"},
		{envProjection, "0"}, {envProjection, "65536"}, {envProjection, " 1"},
		{envStreamID, "no-uuid"}, {envStreamID, "00000000-0000-0000-0000-000000000000"},
	} {
		values := validSequencerEnvironment(t)
		values[test.key] = test.value
		if _, err := loadConfig(mapLookup(values)); err == nil {
			t.Fatalf("invalid legacy %s=%q accepted", test.key, test.value)
		}
	}
	values := validSequencerEnvironment(t)
	values[envFenceFile] = filepath.Join(t.TempDir(), "not-initialized", "revision-fence.json")
	if _, err := loadConfig(mapLookup(values)); err != nil {
		t.Fatalf("legacy startup without descriptor directory regressed: %v", err)
	}
}

func TestManagedSequencerWaitsForLifecyclePublication(t *testing.T) {
	values := managedSequencerEnvironment(t)
	values[envStartupTimeout] = "2s"
	if _, err := loadConfig(mapLookup(values)); !errors.Is(err, propertycatalog.ErrInstallationIdentityMissing) {
		t.Fatalf("missing identity error=%v", err)
	}
	attempts := 0
	lookup := func(name string) (string, bool) {
		if name == envMode {
			attempts++
			if attempts == 2 {
				requireSequencerSpoolUntouched(t, values)
				if _, err := os.Lstat(sequencerIdentityPath(values)); !errors.Is(err, os.ErrNotExist) {
					t.Fatalf("reader initialized identity: %v", err)
				}
				publishSequencerIdentity(t, values, sequencerIdentityFixture(t))
			}
		}
		return mapLookup(values)(name)
	}
	cfg, err := loadConfigWithRetry(context.Background(), lookup)
	if err != nil || attempts != 2 || cfg.runtime.CatalogEpoch != 3 {
		t.Fatalf("late lifecycle publication cfg=%+v attempts=%d err=%v", cfg, attempts, err)
	}
	requireSequencerSpoolUntouched(t, values)
}

func TestManagedSequencerMissingIdentityHasBoundedCancellableStartup(t *testing.T) {
	values := managedSequencerEnvironment(t)
	values[envStartupTimeout] = "20ms"
	started := time.Now()
	err := run(context.Background(), mapLookup(values))
	if !errors.Is(err, context.DeadlineExceeded) || !errors.Is(err, propertycatalog.ErrInstallationIdentityMissing) || time.Since(started) > time.Second {
		t.Fatalf("missing identity startup was not bounded: %v", err)
	}
	if _, err := os.Lstat(sequencerIdentityPath(values)); !errors.Is(err, os.ErrNotExist) {
		t.Fatalf("missing identity was created: %v", err)
	}
	requireSequencerSpoolUntouched(t, values)

	ctx, cancel := context.WithCancel(context.Background())
	values[envStartupTimeout] = "2s"
	attempts := 0
	lookup := func(name string) (string, bool) {
		if name == envMode {
			attempts++
			cancel()
		}
		return mapLookup(values)(name)
	}
	started = time.Now()
	err = run(ctx, lookup)
	if !errors.Is(err, context.Canceled) || attempts != 1 || time.Since(started) > time.Second {
		t.Fatalf("missing identity startup ignored cancellation: attempts=%d err=%v", attempts, err)
	}
	requireSequencerSpoolUntouched(t, values)
	// A pre-cancelled legacy startup must also avoid opening local/Kafka state.
	legacy := validSequencerEnvironment(t)
	if err := run(ctx, mapLookup(legacy)); !errors.Is(err, context.Canceled) {
		t.Fatalf("cancelled legacy startup err=%v", err)
	}
	requireSequencerSpoolUntouched(t, legacy)
}

func TestSequencerStartupDoesNotRetryCorruptionOrOtherConfigurationErrors(t *testing.T) {
	for _, legacy := range []bool{false, true} {
		for _, failure := range []string{"corrupt", "symlink", "permission", "bad group", "partial identity", "conflicting topic"} {
			t.Run(fmt.Sprintf("legacy=%t %s", legacy, failure), func(t *testing.T) {
				values := managedSequencerEnvironment(t)
				if legacy {
					values = validSequencerEnvironment(t)
				}
				values[envStartupTimeout] = "2s"
				switch failure {
				case "corrupt":
					publishSequencerIdentity(t, values, []byte("corrupt\n"))
				case "symlink":
					if err := os.Symlink("absent.json", sequencerIdentityPath(values)); err != nil {
						t.Fatal(err)
					}
				case "permission":
					if os.Geteuid() == 0 {
						t.Skip("root can read a mode-000 file")
					}
					publishSequencerIdentity(t, values, sequencerIdentityFixture(t))
					if err := os.Chmod(sequencerIdentityPath(values), 0o000); err != nil {
						t.Fatal(err)
					}
				case "bad group":
					values[envCandidateGroup] = " "
				case "partial identity":
					values[envEpoch] = "3"
					delete(values, envProjection)
				case "conflicting topic":
					publishSequencerIdentity(t, values, sequencerIdentityFixture(t))
					values[envOutputTopic] += ".wrong"
				}
				attempts := 0
				lookup := func(name string) (string, bool) {
					if name == envMode {
						attempts++
					}
					return mapLookup(values)(name)
				}
				err := run(context.Background(), lookup)
				if err == nil || errors.Is(err, context.DeadlineExceeded) || errors.Is(err, propertycatalog.ErrInstallationIdentityMissing) || attempts != 1 {
					t.Fatalf("non-missing failure was retried: attempts=%d err=%v", attempts, err)
				}
				if failure == "permission" && !errors.Is(err, os.ErrPermission) {
					t.Fatalf("descriptor permission error was not preserved: %v", err)
				}
				requireSequencerSpoolUntouched(t, values)
			})
		}
	}
}

func TestManagedSequencerStopsRetryWhenPublishedIdentityIsCorrupt(t *testing.T) {
	values := managedSequencerEnvironment(t)
	values[envStartupTimeout] = "2s"
	attempts := 0
	lookup := func(name string) (string, bool) {
		if name == envMode {
			attempts++
			if attempts == 2 {
				publishSequencerIdentity(t, values, []byte("corrupt\n"))
			}
		}
		return mapLookup(values)(name)
	}
	err := run(context.Background(), lookup)
	if err == nil || errors.Is(err, propertycatalog.ErrInstallationIdentityMissing) || errors.Is(err, context.DeadlineExceeded) || attempts != 2 {
		t.Fatalf("corrupt publication did not stop retry: attempts=%d err=%v", attempts, err)
	}
	requireSequencerSpoolUntouched(t, values)
}

func TestSequencerConfigHonorsCancellationDuringSuccessfulRead(t *testing.T) {
	values := managedSequencerEnvironment(t)
	publishSequencerIdentity(t, values, sequencerIdentityFixture(t))
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	lookup := func(name string) (string, bool) {
		if name == envMode {
			cancel()
		}
		return mapLookup(values)(name)
	}
	if _, err := loadConfigWithRetry(ctx, lookup); !errors.Is(err, context.Canceled) {
		t.Fatalf("successful read ignored cancellation: %v", err)
	}
	requireSequencerSpoolUntouched(t, values)
}
