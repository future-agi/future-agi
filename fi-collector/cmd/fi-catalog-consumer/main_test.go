package main

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/catalogkafka"
)

func TestProductionIsRefusedBeforeConsumerConstruction(t *testing.T) {
	environment := validEnvironment()
	environment[envEnvironment] = "production"
	constructed := 0

	err := run(context.Background(), bootstrapArgs(), mapLookup(environment), func(
		catalogkafka.FranzConsumerConfig,
		catalogkafka.DeliveryHandler,
		*catalogkafka.SequenceValidator,
	) (runningConsumer, error) {
		constructed++
		return &fakeConsumer{}, nil
	})
	if err == nil || !strings.Contains(err.Error(), `FI_CATALOG_ENVIRONMENT must equal "development" exactly`) {
		t.Fatalf("expected exact development-only refusal, got %v", err)
	}
	if constructed != 0 {
		t.Fatalf("consumer constructed %d times for production configuration", constructed)
	}
}

func TestEnvironmentMatchIsExact(t *testing.T) {
	for _, value := range []string{"", "dev", "Development", "DEVELOPMENT", "development ", " production"} {
		t.Run(value, func(t *testing.T) {
			environment := validEnvironment()
			environment[envEnvironment] = value
			_, err := loadConfig(bootstrapArgs(), mapLookup(environment))
			if err == nil {
				t.Fatalf("environment %q unexpectedly accepted", value)
			}
		})
	}

	if _, err := loadConfig(bootstrapArgs(), mapLookup(validEnvironment())); err != nil {
		t.Fatalf("exact development environment rejected: %v", err)
	}
}

func TestClickHouseDeliveryUsesOneHardTenSecondCeiling(t *testing.T) {
	cfg, err := loadConfig(bootstrapArgs(), mapLookup(validEnvironment()))
	if err != nil {
		t.Fatal(err)
	}
	if cfg.deliveryTimeout != catalogClickHouseDeliveryTimeout ||
		cfg.clickHouse.RequestTimeout != catalogClickHouseDeliveryTimeout ||
		cfg.clickHouse.MaxExecutionTime != catalogClickHouseDeliveryTimeout {
		t.Fatalf("ClickHouse timeout wiring=%+v delivery=%s", cfg.clickHouse, cfg.deliveryTimeout)
	}
	if cfg.deliveryTimeout <= 0 || cfg.deliveryTimeout > 10*time.Second {
		t.Fatalf("unsafe whole-envelope timeout %s", cfg.deliveryTimeout)
	}
}

func TestEveryRequiredSettingFailsClosed(t *testing.T) {
	required := []string{
		envClickHouseURL,
		envClickHouseDatabase,
		envClickHouseUsername,
		envClickHousePassword,
		envKafkaBrokers,
		envKafkaTopic,
		envKafkaConsumerGroup,
	}
	for _, name := range required {
		t.Run(name, func(t *testing.T) {
			environment := validEnvironment()
			delete(environment, name)
			_, err := loadConfig(bootstrapArgs(), mapLookup(environment))
			if err == nil || !strings.Contains(err.Error(), name) {
				t.Fatalf("expected missing %s error, got %v", name, err)
			}
		})
	}

	environment := validEnvironment()
	environment[envClickHousePassword] = ""
	if _, err := loadConfig(bootstrapArgs(), mapLookup(environment)); err != nil {
		t.Fatalf("an explicitly configured empty development password must be allowed: %v", err)
	}
}

func TestSeedModesAreExplicitAndMutuallyExclusive(t *testing.T) {
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

func TestLedgerModeRequiresSeparateReadCredentials(t *testing.T) {
	required := []string{envLedgerURL, envLedgerDatabase, envLedgerUsername, envLedgerPassword}
	for _, name := range required {
		t.Run(name, func(t *testing.T) {
			environment := validLedgerEnvironment("http://ledger:8123")
			delete(environment, name)
			_, err := loadConfig(ledgerArgs(), mapLookup(environment))
			if err == nil || !strings.Contains(err.Error(), name) {
				t.Fatalf("missing %s error=%v", name, err)
			}
		})
	}

	environment := validLedgerEnvironment("http://ledger:8123")
	environment[envLedgerPassword] = ""
	cfg, err := loadConfig(ledgerArgs(), mapLookup(environment))
	if err != nil {
		t.Fatalf("explicit empty ledger password rejected: %v", err)
	}
	if cfg.seedMode != sequenceSeedLedger || cfg.ledger.Username != "ledger-reader" ||
		cfg.ledger.Username == cfg.clickHouse.Username {
		t.Fatalf("separate ledger config not preserved: %+v", cfg)
	}

	for _, name := range []string{"URL", "database"} {
		t.Run("mismatched "+name, func(t *testing.T) {
			environment := validLedgerEnvironment("http://ledger:8123")
			if name == "URL" {
				environment[envLedgerURL] = "http://other-ledger:8123"
			} else {
				environment[envLedgerDatabase] = "other_catalog_dev"
			}
			_, err := loadConfig(ledgerArgs(), mapLookup(environment))
			if err == nil || !strings.Contains(err.Error(), "exactly match") {
				t.Fatalf("mismatched %s error=%v", name, err)
			}
		})
	}
}

func TestInvalidConfigurationNeverConstructsKafkaConsumer(t *testing.T) {
	tests := []struct {
		name string
		args []string
		edit func(map[string]string)
	}{
		{
			name: "bootstrap assertion absent",
			args: nil,
			edit: func(map[string]string) {},
		},
		{
			name: "production",
			args: bootstrapArgs(),
			edit: func(environment map[string]string) { environment[envEnvironment] = "production" },
		},
		{
			name: "Kafka URL broker",
			args: bootstrapArgs(),
			edit: func(environment map[string]string) { environment[envKafkaBrokers] = "kafka://broker:9092" },
		},
		{
			name: "bad topic",
			args: bootstrapArgs(),
			edit: func(environment map[string]string) { environment[envKafkaTopic] = "bad/topic" },
		},
		{
			name: "bad ClickHouse URL",
			args: bootstrapArgs(),
			edit: func(environment map[string]string) { environment[envClickHouseURL] = "ftp://clickhouse" },
		},
		{
			name: "missing group",
			args: bootstrapArgs(),
			edit: func(environment map[string]string) { delete(environment, envKafkaConsumerGroup) },
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			environment := validEnvironment()
			test.edit(environment)
			constructed := 0
			err := run(context.Background(), test.args, mapLookup(environment), func(
				catalogkafka.FranzConsumerConfig,
				catalogkafka.DeliveryHandler,
				*catalogkafka.SequenceValidator,
			) (runningConsumer, error) {
				constructed++
				return &fakeConsumer{}, nil
			})
			if err == nil {
				t.Fatal("invalid configuration unexpectedly succeeded")
			}
			if constructed != 0 {
				t.Fatalf("Kafka client boundary reached %d times", constructed)
			}
		})
	}
}

func TestValidDevelopmentConfigurationRunsAndClosesConsumer(t *testing.T) {
	wantRunError := errors.New("stop after construction")
	fake := &fakeConsumer{runError: wantRunError}
	constructed := 0

	err := run(context.Background(), bootstrapArgs(), mapLookup(validEnvironment()), func(
		cfg catalogkafka.FranzConsumerConfig,
		handler catalogkafka.DeliveryHandler,
		validator *catalogkafka.SequenceValidator,
	) (runningConsumer, error) {
		constructed++
		if got := strings.Join(cfg.Brokers, ","); got != "kafka-1:9092,kafka-2:9092" {
			t.Fatalf("brokers = %q", got)
		}
		if cfg.Topic != "property-catalog.v1" || cfg.GroupID != "property-catalog.consumer" {
			t.Fatalf("unexpected Kafka config: %+v", cfg)
		}
		if handler == nil || validator == nil {
			t.Fatal("handler and validator must be constructed before Kafka")
		}
		if cfg.AssignmentCheckpointLoader != nil {
			t.Fatal("known-empty mode unexpectedly configured assignment ledger refresh")
		}
		return fake, nil
	})
	if !errors.Is(err, wantRunError) {
		t.Fatalf("run error = %v, want %v", err, wantRunError)
	}
	if constructed != 1 || fake.runs != 1 || fake.closes != 1 {
		t.Fatalf("constructed=%d runs=%d closes=%d", constructed, fake.runs, fake.closes)
	}
}

func TestLedgerModeSeedsSequenceBeforeKafkaConstruction(t *testing.T) {
	projectID := "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
	streamID := "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
	firstPayload := strings.Repeat("1", 64)
	secondPayload := strings.Repeat("2", 64)
	zero := strings.Repeat("0", 64)
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		username, password, ok := request.BasicAuth()
		if !ok || username != "ledger-reader" || password != "read-secret" {
			t.Errorf("ledger credentials=%q/%q present=%v", username, password, ok)
		}
		rows := []map[string]any{
			{
				"project_id": projectID, "catalog_epoch": 9, "producer_stream_id": streamID,
				"sequence": 1, "envelope_format": catalogkafka.EnvelopeFormat,
				"envelope_version": catalogkafka.EnvelopeVersion,
				"envelope_id":      strings.Repeat("a", 64), "payload_sha256": firstPayload,
				"previous_payload_sha256": zero, "transport": "kafka", "_version": 1,
			},
			{
				"project_id": projectID, "catalog_epoch": 9, "producer_stream_id": streamID,
				"sequence": 2, "envelope_format": catalogkafka.EnvelopeFormat,
				"envelope_version": catalogkafka.EnvelopeVersion,
				"envelope_id":      strings.Repeat("b", 64), "payload_sha256": secondPayload,
				"previous_payload_sha256": firstPayload, "transport": "kafka", "_version": 2,
			},
		}
		encoder := json.NewEncoder(writer)
		for _, row := range rows {
			if err := encoder.Encode(row); err != nil {
				t.Error(err)
			}
		}
	}))
	defer server.Close()

	environment := validLedgerEnvironment(server.URL)
	wantRunError := errors.New("stop after seeded construction")
	fake := &fakeConsumer{runError: wantRunError}
	constructed := 0
	err := run(context.Background(), ledgerArgs(), mapLookup(environment), func(
		cfg catalogkafka.FranzConsumerConfig,
		_ catalogkafka.DeliveryHandler,
		validator *catalogkafka.SequenceValidator,
	) (runningConsumer, error) {
		constructed++
		if cfg.AssignmentCheckpointLoader == nil {
			t.Fatal("ledger mode omitted assignment-time checkpoint refresh")
		}
		checkpoint, ok := validator.Checkpoint(projectID, 9, streamID)
		if !ok || checkpoint.Sequence != 2 || checkpoint.PayloadSHA256 != secondPayload {
			t.Fatalf("seeded checkpoint=%+v present=%v", checkpoint, ok)
		}
		return fake, nil
	})
	if !errors.Is(err, wantRunError) || constructed != 1 || fake.closes != 1 {
		t.Fatalf("run error=%v constructed=%d closes=%d", err, constructed, fake.closes)
	}
}

func TestLedgerConflictFailsBeforeKafkaConstruction(t *testing.T) {
	payload := strings.Repeat("1", 64)
	base := map[string]any{
		"project_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "catalog_epoch": 9,
		"producer_stream_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", "sequence": 1,
		"envelope_format": catalogkafka.EnvelopeFormat, "envelope_version": catalogkafka.EnvelopeVersion,
		"envelope_id": strings.Repeat("a", 64), "payload_sha256": payload,
		"previous_payload_sha256": strings.Repeat("0", 64), "transport": "kafka", "_version": 7,
	}
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, _ *http.Request) {
		encoder := json.NewEncoder(writer)
		if err := encoder.Encode(base); err != nil {
			t.Error(err)
		}
		conflict := make(map[string]any, len(base))
		for key, value := range base {
			conflict[key] = value
		}
		conflict["payload_sha256"] = strings.Repeat("2", 64)
		if err := encoder.Encode(conflict); err != nil {
			t.Error(err)
		}
	}))
	defer server.Close()

	constructed := 0
	err := run(context.Background(), ledgerArgs(), mapLookup(validLedgerEnvironment(server.URL)), func(
		catalogkafka.FranzConsumerConfig,
		catalogkafka.DeliveryHandler,
		*catalogkafka.SequenceValidator,
	) (runningConsumer, error) {
		constructed++
		return &fakeConsumer{}, nil
	})
	if err == nil || !strings.Contains(err.Error(), "conflicting rows") || constructed != 0 {
		t.Fatalf("error=%v Kafka constructions=%d", err, constructed)
	}
}

type fakeConsumer struct {
	runError error
	runs     int
	closes   int
}

func (consumer *fakeConsumer) Run(context.Context) error {
	consumer.runs++
	return consumer.runError
}

func (consumer *fakeConsumer) Close() { consumer.closes++ }

func bootstrapArgs() []string { return []string{"--start-sequence-one-only"} }

func ledgerArgs() []string { return []string{"--seed-from-delivery-ledger"} }

func validEnvironment() map[string]string {
	return map[string]string{
		envEnvironment:        developmentEnvironment,
		envClickHouseURL:      "http://clickhouse:8123",
		envClickHouseDatabase: "property_catalog_dev",
		envClickHouseUsername: "default",
		envClickHousePassword: "",
		envKafkaBrokers:       "kafka-1:9092,kafka-2:9092",
		envKafkaTopic:         "property-catalog.v1",
		envKafkaConsumerGroup: "property-catalog.consumer",
	}
}

func validLedgerEnvironment(url string) map[string]string {
	environment := validEnvironment()
	environment[envClickHouseURL] = url
	environment[envLedgerURL] = url
	environment[envLedgerDatabase] = "property_catalog_dev"
	environment[envLedgerUsername] = "ledger-reader"
	environment[envLedgerPassword] = "read-secret"
	return environment
}

func mapLookup(environment map[string]string) lookupEnvFunc {
	return func(name string) (string, bool) {
		value, present := environment[name]
		return value, present
	}
}
