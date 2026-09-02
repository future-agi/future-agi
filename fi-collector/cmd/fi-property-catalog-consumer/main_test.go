package main

import (
	"context"
	"errors"
	"strings"
	"testing"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/propertycatalog"
)

const (
	testOrganization = "11111111-1111-4111-8111-111111111111"
	testWorkspace    = "22222222-2222-4222-8222-222222222222"
	testBuildToken   = "55555555-5555-4555-8555-555555555555"
	testStream       = "44444444-4444-4444-8444-444444444444"
)

func validEnvironment() map[string]string {
	return map[string]string{
		envConsumerMode:       consumerModeKafka,
		envEnvironment:        propertycatalog.DevelopmentEnvironment,
		envDevAck:             propertycatalog.DevelopmentAcknowledgement,
		envClickHouseURL:      "http://catalog-clickhouse:8123",
		envClickHouseDatabase: "property_catalog_dev_consumer_test",
		envClickHouseUsername: "property-writer",
		envClickHousePassword: "write-secret",
		envLedgerURL:          "http://catalog-clickhouse:8123",
		envLedgerDatabase:     "property_catalog_dev_consumer_test",
		envLedgerUsername:     "property-ledger-reader",
		envLedgerPassword:     "read-secret",
		envKafkaBrokers:       "kafka-1:9092,kafka-2:9092",
		envKafkaTopic:         "property-catalog.v1.dev",
		envKafkaGroup:         "property-catalog.consumer.v1.dev",
	}
}

func ledgerEnvironment() map[string]string {
	return validEnvironment()
}

func productionEnvironment() map[string]string {
	values := validEnvironment()
	values[envEnvironment] = propertycatalog.ProductionEnvironment
	delete(values, envDevAck)
	values[envProdAck] = propertycatalog.ProductionAcknowledgement
	values[envClickHouseDatabase] = "property_catalog"
	values[envLedgerDatabase] = "property_catalog"
	values[envKafkaTopic] = "futureagi.prod.property-catalog.v1"
	values[envKafkaGroup] = "futureagi.prod.property-catalog.consumer.v1"
	return values
}

func mapLookup(values map[string]string) lookupEnvFunc {
	return func(name string) (string, bool) {
		value, present := values[name]
		return value, present
	}
}

func TestUnifiedConsumerDefaultsOffAndProductionFailsBeforeFactories(t *testing.T) {
	for _, edit := range []func(map[string]string){
		func(values map[string]string) { delete(values, envConsumerMode) },
		func(values map[string]string) { values[envConsumerMode] = "disabled" },
		func(values map[string]string) { values[envEnvironment] = "production" },
		func(values map[string]string) { values[envEnvironment] = "Development" },
		func(values map[string]string) { values[envDevAck] = "yes" },
	} {
		values := validEnvironment()
		edit(values)
		calls := 0
		deps := dependencies{
			newSink: func(propertycatalog.ClickHouseSinkConfig) (propertycatalog.DeliverySink, error) {
				calls++
				return &fakeSink{}, nil
			},
			newLoader: func(
				propertycatalog.ClickHouseSinkConfig,
				propertycatalog.CheckpointLoaderLimits,
			) (checkpointLeaseReader, error) {
				calls++
				return &fakeLoader{}, nil
			},
			newConsumer: func(propertycatalog.FranzConsumerConfig, propertycatalog.Handler, *propertycatalog.SequenceValidator) (runningConsumer, error) {
				calls++
				return &fakeConsumer{}, nil
			},
		}
		err := run(context.Background(), []string{"--start-sequence-one-only"}, mapLookup(values), deps)
		if err == nil {
			t.Fatal("unsafe consumer configuration was accepted")
		}
		if calls != 0 {
			t.Fatalf("unsafe configuration reached %d adapter factories", calls)
		}
	}
}

func TestUnifiedConsumerSeedModeIsExplicitAndExclusive(t *testing.T) {
	for _, args := range [][]string{
		nil,
		{"--start-sequence-one-only", "--seed-from-delivery-ledger"},
	} {
		_, err := loadConfig(args, mapLookup(validEnvironment()))
		if err == nil || !strings.Contains(err.Error(), "exactly one") {
			t.Fatalf("args=%v error=%v", args, err)
		}
	}
}

func TestProductionConsumerRequiresExactGateMatchingDatabaseAndLedgerSeed(t *testing.T) {
	cfg, err := loadConfig(
		[]string{"--seed-from-delivery-ledger"}, mapLookup(productionEnvironment()),
	)
	if err != nil {
		t.Fatal(err)
	}
	if cfg.write.Environment != propertycatalog.ProductionEnvironment ||
		cfg.write.Database != "property_catalog" ||
		cfg.kafka.ClientID != "fi-property-catalog-consumer-v1-prod" ||
		cfg.seed != seedDeliveryLedger {
		t.Fatalf("production config=%+v", cfg)
	}
	if _, err := loadConfig(
		[]string{"--start-sequence-one-only"}, mapLookup(productionEnvironment()),
	); err == nil || !strings.Contains(err.Error(), "seed-from-delivery-ledger") {
		t.Fatalf("production sequence-one error=%v", err)
	}

	for name, mutate := range map[string]func(map[string]string){
		"dev database": func(values map[string]string) {
			values[envClickHouseDatabase] = "property_catalog_dev_wrong"
		},
		"dev acknowledgement": func(values map[string]string) {
			values[envDevAck] = propertycatalog.DevelopmentAcknowledgement
		},
		"empty client ID": func(values map[string]string) {
			values[envKafkaClient] = ""
		},
	} {
		t.Run(name, func(t *testing.T) {
			values := productionEnvironment()
			mutate(values)
			if _, err := loadConfig(
				[]string{"--seed-from-delivery-ledger"}, mapLookup(values),
			); err == nil {
				t.Fatal("unsafe production configuration was accepted")
			}
		})
	}
}

func TestDeliveryTimeoutSupportsOnlyBoundedEnvironmentOverrides(t *testing.T) {
	values := validEnvironment()
	values[envDeliveryWall] = "3s"
	cfg, err := loadConfig(
		[]string{"--start-sequence-one-only"},
		mapLookup(values),
	)
	if err != nil {
		t.Fatal(err)
	}
	if cfg.delivery != 3*time.Second || cfg.write.RequestTimeout != 3*time.Second ||
		cfg.ledger.RequestTimeout != 3*time.Second {
		t.Fatalf("delivery config=%+v", cfg)
	}

	for _, value := range []string{"0s", "11s", "invalid", " 3s"} {
		values := validEnvironment()
		values[envDeliveryWall] = value
		if _, err := loadConfig(
			[]string{"--start-sequence-one-only"},
			mapLookup(values),
		); err == nil || !strings.Contains(err.Error(), envDeliveryWall) {
			t.Fatalf("delivery timeout %q error=%v", value, err)
		}
	}
}

func TestCheckpointInventoryLimitsSupportBoundedEnvironmentOverrides(t *testing.T) {
	values := validEnvironment()
	values[envCheckpointMaxStreams] = "32768"
	values[envCheckpointMaxBytes] = "134217728"
	cfg, err := loadConfig(
		[]string{"--start-sequence-one-only"},
		mapLookup(values),
	)
	if err != nil {
		t.Fatal(err)
	}
	if cfg.checkpointLimits.MaxStreams != 32768 ||
		cfg.checkpointLimits.InventoryMaxBytes != 134217728 {
		t.Fatalf("checkpoint limits=%+v", cfg.checkpointLimits)
	}

	for name, invalid := range map[string][]string{
		envCheckpointMaxStreams: {"0", "262145", "invalid", " 1024"},
		envCheckpointMaxBytes:   {"0", "536870913", "invalid", " 1024"},
	} {
		for _, value := range invalid {
			values := validEnvironment()
			values[name] = value
			if _, err := loadConfig(
				[]string{"--start-sequence-one-only"}, mapLookup(values),
			); err == nil || !strings.Contains(err.Error(), name) {
				t.Fatalf("%s=%q error=%v", name, value, err)
			}
		}
	}
}

func TestLedgerModeRequiresSameDestinationAndDistinctReadIdentity(t *testing.T) {
	for _, name := range []string{envLedgerURL, envLedgerDatabase, envLedgerUsername, envLedgerPassword} {
		t.Run("missing "+name, func(t *testing.T) {
			values := ledgerEnvironment()
			delete(values, name)
			_, err := loadConfig([]string{"--seed-from-delivery-ledger"}, mapLookup(values))
			if err == nil || !strings.Contains(err.Error(), name) {
				t.Fatalf("missing %s error=%v", name, err)
			}
		})
	}
	for _, mutate := range []func(map[string]string){
		func(values map[string]string) { values[envLedgerURL] = "http://different:8123" },
		func(values map[string]string) { values[envLedgerDatabase] = "different" },
		func(values map[string]string) { values[envLedgerUsername] = values[envClickHouseUsername] },
	} {
		values := ledgerEnvironment()
		mutate(values)
		if _, err := loadConfig([]string{"--seed-from-delivery-ledger"}, mapLookup(values)); err == nil {
			t.Fatal("unsafe delivery-ledger identity was accepted")
		}
	}
}

func TestKnownEmptyModeProvesEmptyLedgerBeforeKafka(t *testing.T) {
	want := errors.New("stop after local construction")
	runner := &fakeConsumer{runErr: want}
	loader := &fakeLoader{}
	loaderCalls := 0
	consumerCalls := 0
	deps := dependencies{
		newSink: func(cfg propertycatalog.ClickHouseSinkConfig) (propertycatalog.DeliverySink, error) {
			if cfg.Username != "property-writer" || cfg.RequestTimeout != defaultDeliveryTimeout {
				t.Fatalf("write config=%+v", cfg)
			}
			return &fakeSink{}, nil
		},
		newLoader: func(
			propertycatalog.ClickHouseSinkConfig,
			propertycatalog.CheckpointLoaderLimits,
		) (checkpointLeaseReader, error) {
			loaderCalls++
			return loader, nil
		},
		newConsumer: func(
			cfg propertycatalog.FranzConsumerConfig,
			handler propertycatalog.Handler,
			validator *propertycatalog.SequenceValidator,
		) (runningConsumer, error) {
			consumerCalls++
			if cfg.CheckpointLoader != loader || handler == nil || validator == nil ||
				cfg.Topic != "property-catalog.v1.dev" {
				t.Fatalf("consumer config=%+v handler=%v validator=%v", cfg, handler, validator)
			}
			return runner, nil
		},
	}
	err := run(
		context.Background(), []string{"--start-sequence-one-only"},
		mapLookup(validEnvironment()), deps,
	)
	if !errors.Is(err, want) || loaderCalls != 1 || consumerCalls != 1 || runner.closed != 1 {
		t.Fatalf("error=%v loader=%d consumer=%d closed=%d", err, loaderCalls, consumerCalls, runner.closed)
	}
}

func TestKnownEmptyModeRejectsNonemptyLedgerBeforeKafka(t *testing.T) {
	consumerCalls := 0
	deps := dependencies{
		newSink: func(propertycatalog.ClickHouseSinkConfig) (propertycatalog.DeliverySink, error) {
			return &fakeSink{}, nil
		},
		newLoader: func(
			propertycatalog.ClickHouseSinkConfig,
			propertycatalog.CheckpointLoaderLimits,
		) (checkpointLeaseReader, error) {
			return &fakeLoader{checkpoints: []propertycatalog.StreamCheckpoint{validCheckpoint()}}, nil
		},
		newConsumer: func(propertycatalog.FranzConsumerConfig, propertycatalog.Handler, *propertycatalog.SequenceValidator) (runningConsumer, error) {
			consumerCalls++
			return &fakeConsumer{}, nil
		},
	}
	err := run(
		context.Background(), []string{"--start-sequence-one-only"},
		mapLookup(validEnvironment()), deps,
	)
	if err == nil || !strings.Contains(err.Error(), "proven-empty") || consumerCalls != 0 {
		t.Fatalf("error=%v consumer calls=%d", err, consumerCalls)
	}
}

func TestLedgerLoadsBeforeKafkaAndRefreshesAssignments(t *testing.T) {
	want := errors.New("stop after seeded local construction")
	events := make([]string, 0, 4)
	loader := &fakeLoader{checkpoints: []propertycatalog.StreamCheckpoint{validCheckpoint()}}
	runner := &fakeConsumer{runErr: want}
	deps := dependencies{
		newSink: func(propertycatalog.ClickHouseSinkConfig) (propertycatalog.DeliverySink, error) {
			events = append(events, "sink")
			return &fakeSink{}, nil
		},
		newLoader: func(
			cfg propertycatalog.ClickHouseSinkConfig,
			limits propertycatalog.CheckpointLoaderLimits,
		) (checkpointLeaseReader, error) {
			events = append(events, "loader")
			if cfg.Username != "property-ledger-reader" {
				t.Fatalf("ledger identity=%+v", cfg)
			}
			if limits.MaxStreams != propertycatalog.DefaultCheckpointMaxStreams ||
				limits.InventoryMaxBytes != propertycatalog.DefaultCheckpointInventoryMaxBytes {
				t.Fatalf("checkpoint limits=%+v", limits)
			}
			loader.onLoad = func() { events = append(events, "load") }
			return loader, nil
		},
		newConsumer: func(
			cfg propertycatalog.FranzConsumerConfig,
			_ propertycatalog.Handler,
			validator *propertycatalog.SequenceValidator,
		) (runningConsumer, error) {
			events = append(events, "consumer")
			if cfg.CheckpointLoader != loader {
				t.Fatal("assignment checkpoint refresh was not wired")
			}
			if _, err := validator.Check(envelopeAfterCheckpoint(t)); err != nil {
				t.Fatalf("initial ledger seed was not installed: %v", err)
			}
			return runner, nil
		},
	}
	err := run(
		context.Background(), []string{"--seed-from-delivery-ledger"},
		mapLookup(ledgerEnvironment()), deps,
	)
	if !errors.Is(err, want) || strings.Join(events, ",") != "sink,loader,load,consumer" || runner.closed != 1 {
		t.Fatalf("error=%v events=%v closed=%d", err, events, runner.closed)
	}
}

func TestKafkaSettingsRejectURLsDuplicatesAndUnsafeTopicBeforeFactories(t *testing.T) {
	for _, value := range []string{"kafka://broker:9092", "broker:9092,broker:9092", "broker:9092/path"} {
		values := validEnvironment()
		values[envKafkaBrokers] = value
		if _, err := loadConfig([]string{"--start-sequence-one-only"}, mapLookup(values)); err == nil {
			t.Fatalf("brokers %q accepted", value)
		}
	}
	values := validEnvironment()
	values[envKafkaTopic] = "legacy/topic"
	if _, err := loadConfig([]string{"--start-sequence-one-only"}, mapLookup(values)); err == nil {
		t.Fatal("unsafe topic accepted")
	}
}

type fakeSink struct{}

func (*fakeSink) InsertPropertyCatalog(context.Context, propertycatalog.Table, []map[string]any) error {
	return nil
}

func (*fakeSink) InsertPropertyCatalogDelivery(context.Context, []map[string]any) error { return nil }

type fakeLoader struct {
	checkpoints []propertycatalog.StreamCheckpoint
	onLoad      func()
}

func (l *fakeLoader) LoadCheckpoints(context.Context) ([]propertycatalog.StreamCheckpoint, error) {
	if l.onLoad != nil {
		l.onLoad()
	}
	return append([]propertycatalog.StreamCheckpoint(nil), l.checkpoints...), nil
}

func (l *fakeLoader) AuthorizeDelivery(
	context.Context, propertycatalog.DeliveryLeaseRequest,
) (propertycatalog.DeliveryLeaseEvidence, error) {
	return propertycatalog.DeliveryLeaseEvidence{
		BuildLeaseSHA256: strings.Repeat("a", 64), StreamRole: "hot_values",
		ProjectIDs:  []string{"33333333-3333-4333-8333-333333333333"},
		SpanSinceUS: 1, SpanUntilUS: 2,
	}, nil
}

type fakeConsumer struct {
	runErr error
	closed int
}

func (c *fakeConsumer) Run(context.Context) error { return c.runErr }
func (c *fakeConsumer) Close()                    { c.closed++ }

func validCheckpoint() propertycatalog.StreamCheckpoint {
	return propertycatalog.StreamCheckpoint{
		OrganizationID: testOrganization, WorkspaceID: testWorkspace,
		CatalogEpoch: 3, CatalogRevision: 17, BuildToken: testBuildToken,
		ProjectionVersion: 1, SourceAdapter: propertycatalog.AdapterSpanAttribute,
		ProducerStreamID: testStream, Sequence: 1,
		PayloadSHA256: strings.Repeat("a", 64), EnvelopeID: strings.Repeat("b", 64),
	}
}

func envelopeAfterCheckpoint(t *testing.T) propertycatalog.WireEnvelope {
	t.Helper()
	payload, err := propertycatalog.BuildPayload(nil, nil, 10, 1024, 0, strings.Repeat("c", 64))
	if err != nil {
		t.Fatal(err)
	}
	envelope, err := propertycatalog.NewWireEnvelope(propertycatalog.EnvelopeInput{
		OrganizationID: testOrganization, WorkspaceID: testWorkspace,
		CatalogEpoch: 3, CatalogRevision: 17, BuildToken: testBuildToken,
		ProjectionVersion: 1, SourceAdapter: propertycatalog.AdapterSpanAttribute,
		SourceVersion: 2, SourceFingerprint: strings.Repeat("d", 64),
		ProducerStreamID: testStream, Sequence: 2,
		PreviousPayloadSHA256: strings.Repeat("a", 64), Payload: payload,
	})
	if err != nil {
		t.Fatal(err)
	}
	return envelope
}
