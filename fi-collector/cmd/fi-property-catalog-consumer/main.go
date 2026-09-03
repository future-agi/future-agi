// Command fi-property-catalog-consumer is the default-off Kafka delivery side
// of the unified property catalog v1. Its sink is closed
// over property_definition_catalog, span_attribute_value_catalog, and
// property_catalog_deliveries; it has no canonical-span table API.
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
	"strconv"
	"strings"
	"syscall"
	"time"
	"unicode"

	"github.com/future-agi/future-agi/fi-collector/pkg/propertycatalog"
)

const (
	envConsumerMode = "FI_PROPERTY_CATALOG_CONSUMER_MODE"
	envEnvironment  = "FI_PROPERTY_CATALOG_ENVIRONMENT"
	envDevAck       = "FI_PROPERTY_CATALOG_DEV_ACK"
	envProdAck      = "FI_PROPERTY_CATALOG_PROD_ACK"

	envProductionDatabase = "FI_PROPERTY_CATALOG_PRODUCTION_DATABASE"
	envClickHouseURL      = "FI_PROPERTY_CATALOG_CH_URL"
	envClickHouseDatabase = "FI_PROPERTY_CATALOG_CH_DATABASE"
	envClickHouseUsername = "FI_PROPERTY_CATALOG_CH_USERNAME"
	envClickHousePassword = "FI_PROPERTY_CATALOG_CH_PASSWORD"

	envLedgerURL      = "FI_PROPERTY_CATALOG_LEDGER_CH_URL"
	envLedgerDatabase = "FI_PROPERTY_CATALOG_LEDGER_CH_DATABASE"
	envLedgerUsername = "FI_PROPERTY_CATALOG_LEDGER_CH_USERNAME"
	envLedgerPassword = "FI_PROPERTY_CATALOG_LEDGER_CH_PASSWORD"

	envKafkaBrokers         = "FI_PROPERTY_CATALOG_KAFKA_BROKERS"
	envKafkaTopic           = "FI_PROPERTY_CATALOG_KAFKA_TOPIC"
	envKafkaGroup           = "FI_PROPERTY_CATALOG_KAFKA_CONSUMER_GROUP"
	envKafkaClient          = "FI_PROPERTY_CATALOG_KAFKA_CLIENT_ID"
	envDeliveryWall         = "FI_PROPERTY_CATALOG_DELIVERY_TIMEOUT"
	envCheckpointMaxStreams = "FI_PROPERTY_CATALOG_CHECKPOINT_MAX_STREAMS"
	envCheckpointMaxBytes   = "FI_PROPERTY_CATALOG_CHECKPOINT_MAX_INVENTORY_BYTES"

	consumerModeKafka      = "kafka"
	defaultDeliveryTimeout = propertycatalog.DefaultDeliveryTransportTimeout
)

type lookupEnvFunc func(string) (string, bool)

type seedMode uint8

const (
	seedSequenceOne seedMode = iota + 1
	seedDeliveryLedger
)

type commandConfig struct {
	write            propertycatalog.ClickHouseSinkConfig
	ledger           propertycatalog.ClickHouseSinkConfig
	kafka            propertycatalog.FranzConsumerConfig
	seed             seedMode
	delivery         time.Duration
	checkpointLimits propertycatalog.CheckpointLoaderLimits
}

type runningConsumer interface {
	Run(context.Context) error
	Close()
}

type consumerFactory func(
	propertycatalog.FranzConsumerConfig,
	propertycatalog.Handler,
	*propertycatalog.SequenceValidator,
) (runningConsumer, error)

type sinkFactory func(propertycatalog.ClickHouseSinkConfig) (propertycatalog.DeliverySink, error)

type checkpointLeaseReader interface {
	propertycatalog.CheckpointLoader
	propertycatalog.DeliveryLeaseGuard
}

type loaderFactory func(
	propertycatalog.ClickHouseSinkConfig,
	propertycatalog.CheckpointLoaderLimits,
) (checkpointLeaseReader, error)

type dependencies struct {
	newSink     sinkFactory
	newLoader   loaderFactory
	newConsumer consumerFactory
}

func main() {
	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer cancel()
	err := run(ctx, os.Args[1:], os.LookupEnv, defaultDependencies())
	if err == nil || errors.Is(err, context.Canceled) {
		return
	}
	log.Fatalf("fi-property-catalog-consumer: %v", err)
}

func defaultDependencies() dependencies {
	return dependencies{
		newSink: func(cfg propertycatalog.ClickHouseSinkConfig) (propertycatalog.DeliverySink, error) {
			return propertycatalog.NewClickHouseSink(cfg)
		},
		newLoader: func(
			cfg propertycatalog.ClickHouseSinkConfig,
			limits propertycatalog.CheckpointLoaderLimits,
		) (checkpointLeaseReader, error) {
			return propertycatalog.NewClickHouseCheckpointLoader(cfg, limits)
		},
		newConsumer: func(
			cfg propertycatalog.FranzConsumerConfig,
			handler propertycatalog.Handler,
			validator *propertycatalog.SequenceValidator,
		) (runningConsumer, error) {
			return propertycatalog.NewFranzConsumer(cfg, handler, validator)
		},
	}
}

func run(ctx context.Context, args []string, lookup lookupEnvFunc, deps dependencies) error {
	if ctx == nil || lookup == nil || deps.newSink == nil || deps.newLoader == nil || deps.newConsumer == nil {
		return errors.New("property catalog consumer requires context, environment, and factories")
	}
	cfg, err := loadConfig(args, lookup)
	if err != nil {
		return err
	}
	if err := ctx.Err(); err != nil {
		return err
	}

	// Constructing either HTTP adapter is local-only. In ledger mode, validate
	// both identities and the Kafka settings before the first checkpoint read.
	sink, err := deps.newSink(cfg.write)
	if err != nil {
		return fmt.Errorf("configure property catalog ClickHouse sink: %w", err)
	}
	loader, err := deps.newLoader(cfg.ledger, cfg.checkpointLimits)
	if err != nil {
		return fmt.Errorf("configure property catalog delivery ledger reader: %w", err)
	}
	seeds, err := loader.LoadCheckpoints(ctx)
	if err != nil {
		return fmt.Errorf("load property catalog sequence checkpoints: %w", err)
	}
	if cfg.seed == seedSequenceOne && len(seeds) != 0 {
		return errors.New("start-sequence-one-only requires a proven-empty dedicated delivery ledger")
	}
	validator, err := propertycatalog.NewSequenceValidator(seeds)
	if err != nil {
		return fmt.Errorf("validate property catalog sequence checkpoints: %w", err)
	}
	handler, err := propertycatalog.NewDeliveryHandler(sink, loader, cfg.delivery)
	if err != nil {
		return fmt.Errorf("configure property catalog delivery handler: %w", err)
	}
	cfg.kafka.CheckpointLoader = loader
	consumer, err := deps.newConsumer(cfg.kafka, handler, validator)
	if err != nil {
		return fmt.Errorf("configure property catalog Kafka consumer: %w", err)
	}
	if consumer == nil {
		return errors.New("property catalog Kafka consumer factory returned nil")
	}
	defer consumer.Close()
	return consumer.Run(ctx)
}

func loadConfig(args []string, lookup lookupEnvFunc) (commandConfig, error) {
	if lookup == nil {
		return commandConfig{}, errors.New("environment lookup is required")
	}
	flags := flag.NewFlagSet("fi-property-catalog-consumer", flag.ContinueOnError)
	flags.SetOutput(io.Discard)
	sequenceOne := flags.Bool(
		"start-sequence-one-only", false,
		"accept only sequence-one roots for a known-empty dedicated development topic",
	)
	ledger := flags.Bool(
		"seed-from-delivery-ledger", false,
		"seed and rebalance-refresh sequence state from property_catalog_deliveries",
	)
	if err := flags.Parse(args); err != nil {
		return commandConfig{}, fmt.Errorf("parse flags: %w", err)
	}
	if flags.NArg() != 0 {
		return commandConfig{}, errors.New("positional arguments are not supported")
	}
	if *sequenceOne == *ledger {
		return commandConfig{}, errors.New("exactly one of --start-sequence-one-only or --seed-from-delivery-ledger is required")
	}
	if value, _ := lookup(envConsumerMode); value != consumerModeKafka {
		return commandConfig{}, fmt.Errorf("%s must equal %q exactly", envConsumerMode, consumerModeKafka)
	}
	environment, err := validatedEnvironment(lookup)
	if err != nil {
		return commandConfig{}, err
	}
	if environment == propertycatalog.ProductionEnvironment && *sequenceOne {
		return commandConfig{}, errors.New("production requires --seed-from-delivery-ledger")
	}
	deliveryTimeout, err := boundedDeliveryTimeout(lookup)
	if err != nil {
		return commandConfig{}, err
	}
	productionDatabase, err := optionalProductionDatabase(lookup)
	if err != nil {
		return commandConfig{}, err
	}

	write, err := clickHouseConfig(
		lookup,
		envClickHouseURL,
		envClickHouseDatabase,
		envClickHouseUsername,
		envClickHousePassword,
		environment,
		productionDatabase,
		deliveryTimeout,
	)
	if err != nil {
		return commandConfig{}, err
	}
	brokersText, err := requireEnv(lookup, envKafkaBrokers, false)
	if err != nil {
		return commandConfig{}, err
	}
	topic, err := requireEnv(lookup, envKafkaTopic, false)
	if err != nil {
		return commandConfig{}, err
	}
	group, err := requireEnv(lookup, envKafkaGroup, false)
	if err != nil {
		return commandConfig{}, err
	}
	brokers := strings.Split(brokersText, ",")
	clientID := map[string]string{
		propertycatalog.DevelopmentEnvironment: "fi-property-catalog-consumer-v1-dev",
		propertycatalog.ProductionEnvironment:  "fi-property-catalog-consumer-v1-prod",
	}[environment]
	if value, present := lookup(envKafkaClient); present {
		clientID = value
	}
	if err := validateKafkaConfig(brokers, topic, group, clientID); err != nil {
		return commandConfig{}, err
	}

	ledgerConfig, err := clickHouseConfig(
		lookup,
		envLedgerURL,
		envLedgerDatabase,
		envLedgerUsername,
		envLedgerPassword,
		environment,
		productionDatabase,
		deliveryTimeout,
	)
	if err != nil {
		return commandConfig{}, err
	}
	if ledgerConfig.URL != write.URL || ledgerConfig.Database != write.Database {
		return commandConfig{}, errors.New("delivery ledger URL and database must exactly match the catalog write destination")
	}
	if ledgerConfig.Username == write.Username {
		return commandConfig{}, errors.New("delivery ledger reader and catalog writer require distinct ClickHouse usernames")
	}
	checkpointLimits := propertycatalog.CheckpointLoaderLimits{
		MaxStreams:        propertycatalog.DefaultCheckpointMaxStreams,
		InventoryMaxBytes: propertycatalog.DefaultCheckpointInventoryMaxBytes,
	}
	if err := optionalBoundedPositiveInt(
		lookup, envCheckpointMaxStreams, &checkpointLimits.MaxStreams,
		propertycatalog.MaximumCheckpointMaxStreams,
	); err != nil {
		return commandConfig{}, err
	}
	if err := optionalBoundedPositiveInt64(
		lookup, envCheckpointMaxBytes, &checkpointLimits.InventoryMaxBytes,
		propertycatalog.MaximumCheckpointInventoryMaxBytes,
	); err != nil {
		return commandConfig{}, err
	}
	result := commandConfig{
		write:  write,
		ledger: ledgerConfig,
		kafka: propertycatalog.FranzConsumerConfig{
			Brokers: brokers, Topic: topic, GroupID: group, ClientID: clientID,
		},
		seed: seedSequenceOne, delivery: deliveryTimeout,
		checkpointLimits: checkpointLimits,
	}
	if *ledger {
		result.seed = seedDeliveryLedger
	}
	return result, nil
}

func clickHouseConfig(
	lookup lookupEnvFunc,
	urlName, databaseName, usernameName, passwordName string,
	environment, productionDatabase string,
	requestTimeout time.Duration,
) (propertycatalog.ClickHouseSinkConfig, error) {
	urlValue, err := requireEnv(lookup, urlName, false)
	if err != nil {
		return propertycatalog.ClickHouseSinkConfig{}, err
	}
	database, err := requireEnv(lookup, databaseName, false)
	if err != nil {
		return propertycatalog.ClickHouseSinkConfig{}, err
	}
	username, err := requireEnv(lookup, usernameName, false)
	if err != nil {
		return propertycatalog.ClickHouseSinkConfig{}, err
	}
	password, err := requireEnv(lookup, passwordName, true)
	if err != nil {
		return propertycatalog.ClickHouseSinkConfig{}, err
	}
	cfg := propertycatalog.ClickHouseSinkConfig{
		URL: urlValue, Database: database, Username: username, Password: password,
		Environment: environment, ProductionDatabase: productionDatabase, RequestTimeout: requestTimeout,
	}
	// Constructor validation is local-only and binds the isolated database
	// prefix to the exact environment before a ledger read or Kafka client.
	if _, err := propertycatalog.NewClickHouseSink(cfg); err != nil {
		return propertycatalog.ClickHouseSinkConfig{}, fmt.Errorf("%s: %w", databaseName, err)
	}
	return cfg, nil
}

// optionalProductionDatabase mirrors the backend's
// PROPERTY_CATALOG_PRODUCTION_DATABASE contract: unset keeps the default
// production catalog name, while a present value must be the exact isolated
// database the deployment binds every reader and writer to.
func optionalProductionDatabase(lookup lookupEnvFunc) (string, error) {
	if _, present := lookup(envProductionDatabase); !present {
		return "", nil
	}
	return requireEnv(lookup, envProductionDatabase, false)
}

func validatedEnvironment(lookup lookupEnvFunc) (string, error) {
	environment, err := requireEnv(lookup, envEnvironment, false)
	if err != nil {
		return "", err
	}
	devAck, _ := lookup(envDevAck)
	prodAck, _ := lookup(envProdAck)
	switch environment {
	case propertycatalog.DevelopmentEnvironment:
		if devAck != propertycatalog.DevelopmentAcknowledgement || prodAck != "" {
			return "", errors.New("development consumer requires only the exact development acknowledgement")
		}
	case propertycatalog.ProductionEnvironment:
		if prodAck != propertycatalog.ProductionAcknowledgement || devAck != "" {
			return "", errors.New("production consumer requires only the exact production acknowledgement")
		}
	default:
		return "", fmt.Errorf("%s must be an exact supported environment", envEnvironment)
	}
	return environment, nil
}

func boundedDeliveryTimeout(lookup lookupEnvFunc) (time.Duration, error) {
	value, present := lookup(envDeliveryWall)
	if !present || value == "" {
		return defaultDeliveryTimeout, nil
	}
	if strings.TrimSpace(value) != value {
		return 0, fmt.Errorf("%s must not contain surrounding whitespace", envDeliveryWall)
	}
	timeout, err := time.ParseDuration(value)
	if err != nil || timeout <= 0 || timeout > propertycatalog.MaxDeliveryTimeout {
		return 0, fmt.Errorf(
			"%s must be a positive duration no greater than %s",
			envDeliveryWall,
			propertycatalog.MaxDeliveryTimeout,
		)
	}
	return timeout, nil
}

func optionalBoundedPositiveInt(
	lookup lookupEnvFunc, name string, target *int, maximum int,
) error {
	value, present := lookup(name)
	if !present {
		return nil
	}
	parsed, err := strconv.ParseInt(value, 10, 32)
	if err != nil || parsed <= 0 || parsed > int64(maximum) {
		return fmt.Errorf("%s must be an integer in [1,%d]", name, maximum)
	}
	*target = int(parsed)
	return nil
}

func optionalBoundedPositiveInt64(
	lookup lookupEnvFunc, name string, target *int64, maximum int64,
) error {
	value, present := lookup(name)
	if !present {
		return nil
	}
	parsed, err := strconv.ParseInt(value, 10, 64)
	if err != nil || parsed <= 0 || parsed > maximum {
		return fmt.Errorf("%s must be an integer in [1,%d]", name, maximum)
	}
	*target = parsed
	return nil
}

func requireEnv(lookup lookupEnvFunc, name string, allowEmpty bool) (string, error) {
	value, present := lookup(name)
	if !present || (!allowEmpty && value == "") {
		return "", fmt.Errorf("%s must be set%s", name, map[bool]string{true: "", false: " and non-empty"}[allowEmpty])
	}
	if strings.TrimSpace(value) != value {
		return "", fmt.Errorf("%s must not contain surrounding whitespace", name)
	}
	return value, nil
}

func validateKafkaConfig(brokers []string, topic, group, clientID string) error {
	if len(brokers) == 0 || len(brokers) > propertycatalog.MaxKafkaBrokers {
		return errors.New("property catalog Kafka requires 1..16 brokers")
	}
	seen := make(map[string]struct{}, len(brokers))
	for index, broker := range brokers {
		if !safeKafkaText(broker, propertycatalog.MaxKafkaIdentityBytes) || strings.Contains(broker, "://") || strings.ContainsAny(broker, "/?#") {
			return fmt.Errorf("property catalog Kafka broker %d must be a bounded host[:port]", index)
		}
		if _, exists := seen[broker]; exists {
			return fmt.Errorf("property catalog Kafka broker %d is duplicated", index)
		}
		seen[broker] = struct{}{}
	}
	if topic == "" || topic == "." || topic == ".." || len(topic) > propertycatalog.MaxKafkaTopicBytes {
		return errors.New("property catalog Kafka topic is invalid")
	}
	for _, char := range topic {
		if char > unicode.MaxASCII || !((char >= 'a' && char <= 'z') || (char >= 'A' && char <= 'Z') ||
			(char >= '0' && char <= '9') || char == '.' || char == '_' || char == '-') {
			return errors.New("property catalog Kafka topic contains an invalid character")
		}
	}
	if !safeKafkaText(group, propertycatalog.MaxKafkaIdentityBytes) ||
		!safeKafkaText(clientID, propertycatalog.MaxKafkaIdentityBytes) {
		return errors.New("property catalog Kafka consumer group/client identity is invalid")
	}
	return nil
}

func safeKafkaText(value string, limit int) bool {
	if value == "" || len(value) > limit || strings.TrimSpace(value) != value {
		return false
	}
	for _, char := range value {
		if unicode.IsControl(char) {
			return false
		}
	}
	return true
}
