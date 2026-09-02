// Command fi-catalog-consumer is the development-only Kafka delivery side of
// the span-attribute catalog. It has no access to the canonical spans table:
// its ClickHouse sink is closed over the two catalog data tables and the new
// delivery ledger.
package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"io"
	"log"
	"os"
	"os/signal"
	"strings"
	"syscall"
	"time"
	"unicode"

	"github.com/future-agi/future-agi/fi-collector/pkg/catalogkafka"
	"github.com/future-agi/future-agi/fi-collector/pkg/catalogwriter"
)

const (
	developmentEnvironment = "development"

	envEnvironment        = "FI_CATALOG_ENVIRONMENT"
	envClickHouseURL      = "FI_CATALOG_CH_URL"
	envClickHouseDatabase = "FI_CATALOG_CH_DATABASE"
	envClickHouseUsername = "FI_CATALOG_CH_USERNAME"
	envClickHousePassword = "FI_CATALOG_CH_PASSWORD"
	envLedgerURL          = "FI_CATALOG_LEDGER_CH_URL"
	envLedgerDatabase     = "FI_CATALOG_LEDGER_CH_DATABASE"
	envLedgerUsername     = "FI_CATALOG_LEDGER_CH_USERNAME"
	envLedgerPassword     = "FI_CATALOG_LEDGER_CH_PASSWORD"
	envKafkaBrokers       = "FI_CATALOG_KAFKA_BROKERS"
	envKafkaTopic         = "FI_CATALOG_KAFKA_TOPIC"
	envKafkaConsumerGroup = "FI_CATALOG_KAFKA_CONSUMER_GROUP"

	maxKafkaBrokers       = 16
	maxKafkaIdentityBytes = 255

	// One deadline covers all catalog chunks and the delivery-ledger insert.
	// Individual ClickHouse requests use the same ceiling, but cannot reset it.
	catalogClickHouseDeliveryTimeout = 10 * time.Second
)

type lookupEnvFunc func(string) (string, bool)

type sequenceSeedMode uint8

const (
	sequenceSeedEmpty sequenceSeedMode = iota + 1
	sequenceSeedLedger
)

type commandConfig struct {
	clickHouse      catalogwriter.ClickHouseSinkConfig
	ledger          catalogkafka.DeliveryLedgerReaderConfig
	kafka           catalogkafka.FranzConsumerConfig
	deliveryTimeout time.Duration
	seedMode        sequenceSeedMode
}

type runningConsumer interface {
	Run(context.Context) error
	Close()
}

type consumerFactory func(
	catalogkafka.FranzConsumerConfig,
	catalogkafka.DeliveryHandler,
	*catalogkafka.SequenceValidator,
) (runningConsumer, error)

func main() {
	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer cancel()

	err := run(ctx, os.Args[1:], os.LookupEnv, newFranzConsumer)
	if err == nil || errors.Is(err, context.Canceled) {
		return
	}
	log.Fatalf("fi-catalog-consumer: %v", err)
}

func run(
	ctx context.Context,
	args []string,
	lookup lookupEnvFunc,
	newConsumer consumerFactory,
) error {
	if ctx == nil {
		return errors.New("consumer context is required")
	}
	if lookup == nil {
		return errors.New("environment lookup is required")
	}
	if newConsumer == nil {
		return errors.New("consumer factory is required")
	}

	cfg, err := loadConfig(args, lookup)
	if err != nil {
		return err
	}

	var seeds []catalogkafka.StreamCheckpoint
	var ledgerReader catalogkafka.CheckpointLoader
	if cfg.seedMode == sequenceSeedLedger {
		reader, err := catalogkafka.NewDeliveryLedgerCheckpointReader(cfg.ledger)
		if err != nil {
			return fmt.Errorf("configure catalog delivery ledger reader: %w", err)
		}
		seeds, err = reader.Load(ctx)
		if err != nil {
			return fmt.Errorf("load catalog sequence checkpoints: %w", err)
		}
		ledgerReader = reader
	}

	// All configuration and any durable seed have been validated before a
	// Kafka client can be constructed.
	sink, err := catalogwriter.NewClickHouseSink(cfg.clickHouse)
	if err != nil {
		return fmt.Errorf("configure catalog ClickHouse sink: %w", err)
	}
	handler, err := catalogkafka.NewClickHouseDeliveryHandler(sink, cfg.deliveryTimeout)
	if err != nil {
		return fmt.Errorf("configure catalog delivery handler: %w", err)
	}
	validator, err := catalogkafka.NewSequenceValidator(seeds)
	if err != nil {
		return fmt.Errorf("configure catalog sequence validator: %w", err)
	}
	cfg.kafka.AssignmentCheckpointLoader = ledgerReader
	consumer, err := newConsumer(cfg.kafka, handler, validator)
	if err != nil {
		return fmt.Errorf("configure Kafka consumer: %w", err)
	}
	if consumer == nil {
		return errors.New("Kafka consumer factory returned nil")
	}
	defer consumer.Close()

	return consumer.Run(ctx)
}

func loadConfig(args []string, lookup lookupEnvFunc) (commandConfig, error) {
	if lookup == nil {
		return commandConfig{}, errors.New("environment lookup is required")
	}
	flags := flag.NewFlagSet("fi-catalog-consumer", flag.ContinueOnError)
	flags.SetOutput(io.Discard)
	sequenceOneOnly := flags.Bool(
		"start-sequence-one-only",
		false,
		"seed no checkpoints for a known-empty development topic and accept only streams beginning at sequence one",
	)
	seedFromLedger := flags.Bool(
		"seed-from-delivery-ledger",
		false,
		"resume development streams from validated latest rows in the catalog delivery ledger",
	)
	if err := flags.Parse(args); err != nil {
		return commandConfig{}, fmt.Errorf("parse flags: %w", err)
	}
	if flags.NArg() != 0 {
		return commandConfig{}, errors.New("positional arguments are not supported")
	}

	environment, _ := lookup(envEnvironment)
	if environment != developmentEnvironment {
		return commandConfig{}, fmt.Errorf(
			"%s must equal %q exactly; got %q",
			envEnvironment, developmentEnvironment, environment,
		)
	}
	if *sequenceOneOnly == *seedFromLedger {
		return commandConfig{}, errors.New(
			"exactly one of --start-sequence-one-only or --seed-from-delivery-ledger is required",
		)
	}

	clickHouseURL, err := requireNonEmptyEnv(lookup, envClickHouseURL)
	if err != nil {
		return commandConfig{}, err
	}
	clickHouseDatabase, err := requireNonEmptyEnv(lookup, envClickHouseDatabase)
	if err != nil {
		return commandConfig{}, err
	}
	clickHouseUsername, err := requireNonEmptyEnv(lookup, envClickHouseUsername)
	if err != nil {
		return commandConfig{}, err
	}
	clickHousePassword, passwordPresent := lookup(envClickHousePassword)
	if !passwordPresent {
		return commandConfig{}, fmt.Errorf("%s must be set (an empty development password is allowed)", envClickHousePassword)
	}
	var ledgerConfig catalogkafka.DeliveryLedgerReaderConfig
	seedMode := sequenceSeedEmpty
	if *seedFromLedger {
		seedMode = sequenceSeedLedger
		ledgerURL, err := requireNonEmptyEnv(lookup, envLedgerURL)
		if err != nil {
			return commandConfig{}, err
		}
		ledgerDatabase, err := requireNonEmptyEnv(lookup, envLedgerDatabase)
		if err != nil {
			return commandConfig{}, err
		}
		ledgerUsername, err := requireNonEmptyEnv(lookup, envLedgerUsername)
		if err != nil {
			return commandConfig{}, err
		}
		ledgerPassword, present := lookup(envLedgerPassword)
		if !present {
			return commandConfig{}, fmt.Errorf(
				"%s must be set (an empty development password is allowed)", envLedgerPassword,
			)
		}
		if ledgerURL != clickHouseURL || ledgerDatabase != clickHouseDatabase {
			return commandConfig{}, errors.New(
				"catalog ledger URL and database must exactly match the catalog write destination",
			)
		}
		ledgerConfig = catalogkafka.DeliveryLedgerReaderConfig{
			URL: ledgerURL, Database: ledgerDatabase,
			Username: ledgerUsername, Password: ledgerPassword,
		}
	}
	kafkaBrokers, err := requireNonEmptyEnv(lookup, envKafkaBrokers)
	if err != nil {
		return commandConfig{}, err
	}
	topic, err := requireNonEmptyEnv(lookup, envKafkaTopic)
	if err != nil {
		return commandConfig{}, err
	}
	group, err := requireNonEmptyEnv(lookup, envKafkaConsumerGroup)
	if err != nil {
		return commandConfig{}, err
	}

	brokers := strings.Split(kafkaBrokers, ",")
	if err := validateKafkaSettings(brokers, topic, group); err != nil {
		return commandConfig{}, err
	}

	return commandConfig{
		clickHouse: catalogwriter.ClickHouseSinkConfig{
			URL: clickHouseURL, Database: clickHouseDatabase,
			Username: clickHouseUsername, Password: clickHousePassword,
			RequestTimeout:   catalogClickHouseDeliveryTimeout,
			MaxExecutionTime: catalogClickHouseDeliveryTimeout,
		},
		kafka: catalogkafka.FranzConsumerConfig{
			Brokers: brokers, Topic: topic, GroupID: group,
		},
		ledger:          ledgerConfig,
		deliveryTimeout: catalogClickHouseDeliveryTimeout,
		seedMode:        seedMode,
	}, nil
}

func requireNonEmptyEnv(lookup lookupEnvFunc, name string) (string, error) {
	value, present := lookup(name)
	if !present || value == "" {
		return "", fmt.Errorf("%s must be set and non-empty", name)
	}
	if strings.TrimSpace(value) != value {
		return "", fmt.Errorf("%s must not contain surrounding whitespace", name)
	}
	return value, nil
}

// validateKafkaSettings mirrors the fail-closed public franz adapter contract
// so malformed settings cannot reach client construction (and therefore
// cannot start metadata I/O). The adapter validates them again at its boundary.
func validateKafkaSettings(brokers []string, topic, group string) error {
	if len(brokers) == 0 || len(brokers) > maxKafkaBrokers {
		return fmt.Errorf("%s must contain between 1 and %d brokers", envKafkaBrokers, maxKafkaBrokers)
	}
	seen := make(map[string]struct{}, len(brokers))
	for index, broker := range brokers {
		if err := validateKafkaIdentity("broker", broker); err != nil {
			return fmt.Errorf("%s broker %d: %w", envKafkaBrokers, index, err)
		}
		if strings.Contains(broker, "://") || strings.ContainsAny(broker, "/?#") {
			return fmt.Errorf("%s broker %d must be host[:port], not a URL", envKafkaBrokers, index)
		}
		if _, exists := seen[broker]; exists {
			return fmt.Errorf("%s contains duplicate broker %q", envKafkaBrokers, broker)
		}
		seen[broker] = struct{}{}
	}
	if topic == "." || topic == ".." || len(topic) > 249 {
		return fmt.Errorf("%s is not a valid Kafka topic", envKafkaTopic)
	}
	for index := 0; index < len(topic); index++ {
		char := topic[index]
		if (char < 'a' || char > 'z') && (char < 'A' || char > 'Z') &&
			(char < '0' || char > '9') && char != '.' && char != '_' && char != '-' {
			return fmt.Errorf("%s contains an invalid character", envKafkaTopic)
		}
	}
	if err := validateKafkaIdentity("consumer group", group); err != nil {
		return fmt.Errorf("%s: %w", envKafkaConsumerGroup, err)
	}
	return nil
}

func validateKafkaIdentity(name, value string) error {
	if value == "" || len(value) > maxKafkaIdentityBytes || strings.TrimSpace(value) != value {
		return fmt.Errorf("%s must be non-empty, bounded, and have no surrounding whitespace", name)
	}
	for _, char := range value {
		if unicode.IsControl(char) {
			return fmt.Errorf("%s must not contain control characters", name)
		}
	}
	return nil
}

func newFranzConsumer(
	cfg catalogkafka.FranzConsumerConfig,
	handler catalogkafka.DeliveryHandler,
	validator *catalogkafka.SequenceValidator,
) (runningConsumer, error) {
	return catalogkafka.NewFranzConsumer(cfg, handler, validator)
}
