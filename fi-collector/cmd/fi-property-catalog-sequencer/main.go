// Command fi-property-catalog-sequencer is the singleton owner of unified
// property-catalog stream sequence, spool, drain, and ordered Kafka output
// state. Autoscaled collectors publish only deterministic candidates.
package main

import (
	"context"
	"errors"
	"fmt"
	"log"
	"os"
	"os/signal"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/propertycatalog"
)

const (
	envMode        = "FI_PROPERTY_CATALOG_MODE"
	envEnvironment = "FI_PROPERTY_CATALOG_ENVIRONMENT"
	envDevAck      = "FI_PROPERTY_CATALOG_DEV_ACK"
	envProdAck     = "FI_PROPERTY_CATALOG_PROD_ACK"
	envEpoch       = "FI_PROPERTY_CATALOG_EPOCH"
	envProjection  = "FI_PROPERTY_CATALOG_PROJECTION_VERSION"
	envStreamID    = "FI_PROPERTY_CATALOG_PRODUCER_STREAM_ID"
	envScopeMode   = "FI_PROPERTY_CATALOG_WORKSPACE_SCOPE_MODE"
	envAllowlist   = "FI_PROPERTY_CATALOG_WORKSPACE_ALLOWLIST"
	envFenceFile   = "FI_PROPERTY_CATALOG_REVISION_FENCE_FILE"
	envSpoolDir    = "FI_PROPERTY_CATALOG_SPOOL_DIR"

	envOutputBrokers         = "FI_PROPERTY_CATALOG_KAFKA_BROKERS"
	envOutputTopic           = "FI_PROPERTY_CATALOG_KAFKA_TOPIC"
	envOutputClient          = "FI_PROPERTY_CATALOG_KAFKA_CLIENT_ID"
	envOutputDeliveryTimeout = "FI_PROPERTY_CATALOG_KAFKA_DELIVERY_TIMEOUT"
	envTransactionalID       = "FI_PROPERTY_CATALOG_SEQUENCER_TRANSACTIONAL_ID"
	envTransactionTimeout    = "FI_PROPERTY_CATALOG_SEQUENCER_TRANSACTION_TIMEOUT"
	envStartupTimeout        = "FI_PROPERTY_CATALOG_SEQUENCER_STARTUP_TIMEOUT"

	envCandidateBrokers  = "FI_PROPERTY_CATALOG_CANDIDATE_KAFKA_BROKERS"
	envCandidateTopic    = "FI_PROPERTY_CATALOG_CANDIDATE_KAFKA_TOPIC"
	envCandidateGroup    = "FI_PROPERTY_CATALOG_CANDIDATE_KAFKA_CONSUMER_GROUP"
	envCandidateClient   = "FI_PROPERTY_CATALOG_CANDIDATE_KAFKA_CLIENT_ID"
	envCandidateInstance = "FI_PROPERTY_CATALOG_CANDIDATE_KAFKA_INSTANCE_ID"

	envReplayInterval     = "FI_PROPERTY_CATALOG_REPLAY_INTERVAL"
	envShutdownTimeout    = "FI_PROPERTY_CATALOG_SHUTDOWN_TIMEOUT"
	envQueueDepth         = "FI_PROPERTY_CATALOG_QUEUE_DEPTH"
	envMaxSpansPerBatch   = "FI_PROPERTY_CATALOG_MAX_SPANS_PER_BATCH"
	envMaxKeysPerSpan     = "FI_PROPERTY_CATALOG_MAX_KEYS_PER_SPAN"
	envMaxArrayMembers    = "FI_PROPERTY_CATALOG_MAX_ARRAY_MEMBERS_PER_SPAN"
	envMaxEncodedBytes    = "FI_PROPERTY_CATALOG_MAX_ENCODED_BYTES_PER_SPAN"
	envMaxChunkRows       = "FI_PROPERTY_CATALOG_MAX_CHUNK_ROWS"
	envMaxChunkBytes      = "FI_PROPERTY_CATALOG_MAX_CHUNK_BYTES"
	envMaxSpoolFiles      = "FI_PROPERTY_CATALOG_MAX_SPOOL_FILES"
	envMaxSpoolBytes      = "FI_PROPERTY_CATALOG_MAX_SPOOL_BYTES"
	envReceiptFiles       = "FI_PROPERTY_CATALOG_CANDIDATE_RECEIPT_MAX_FILES"
	envReceiptBytes       = "FI_PROPERTY_CATALOG_CANDIDATE_RECEIPT_MAX_BYTES"
	envRecentCandidateIDs = "FI_PROPERTY_CATALOG_CANDIDATE_RECENT_IDS"

	defaultStartupTimeout = 10 * time.Second
	maxStartupTimeout     = 2 * time.Minute
	maxTransactionTimeout = 2 * time.Minute
)

type lookupEnvFunc func(string) (string, bool)

type commandConfig struct {
	runtime         propertycatalog.RuntimeConfig
	output          propertycatalog.FranzProducerConfig
	candidate       propertycatalog.FranzCandidateSourceConfig
	receipts        propertycatalog.CandidateReceiptStoreConfig
	transactionalID string
	startupTimeout  time.Duration
}

func main() {
	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer cancel()
	if err := run(ctx, os.LookupEnv); err != nil && !errors.Is(err, context.Canceled) {
		log.Fatalf("fi-property-catalog-sequencer: %v", err)
	}
}

func run(ctx context.Context, lookup lookupEnvFunc) error {
	if ctx == nil || lookup == nil {
		return errors.New("property catalog sequencer requires context and environment")
	}
	cfg, err := loadConfig(lookup)
	if err != nil {
		return err
	}

	owner, err := propertycatalog.AcquireSequencerOwnerLock(
		cfg.runtime.SpoolDirectory, cfg.transactionalID,
	)
	if err != nil {
		return err
	}
	defer owner.Close()

	output, err := propertycatalog.NewFranzProducer(cfg.output)
	if err != nil {
		return fmt.Errorf("construct ordered-output producer: %w", err)
	}
	defer output.Close()
	startupCtx, stopStartup := context.WithTimeout(ctx, cfg.startupTimeout)
	err = output.FenceOwner(startupCtx)
	stopStartup()
	if err != nil {
		return fmt.Errorf("establish singleton Kafka owner fence: %w", err)
	}

	revisions, err := propertycatalog.NewFileRevisionProvider(cfg.runtime.RevisionFenceFile)
	if err != nil {
		return fmt.Errorf("construct revision provider: %w", err)
	}
	runtime, err := propertycatalog.NewHotRuntime(cfg.runtime, revisions, output)
	if err != nil {
		return fmt.Errorf("construct ordered hot runtime: %w", err)
	}
	runtimeCtx, stopRuntime := context.WithCancel(context.Background())
	defer stopRuntime()
	if err := runtime.Start(runtimeCtx); err != nil {
		return fmt.Errorf("start ordered hot runtime: %w", err)
	}
	defer func() {
		shutdownCtx, cancel := context.WithTimeout(context.Background(), cfg.runtime.ShutdownTimeout)
		defer cancel()
		if shutdownErr := runtime.Shutdown(shutdownCtx); shutdownErr != nil {
			log.Printf("fi-property-catalog-sequencer: incomplete runtime shutdown: %v", shutdownErr)
		}
	}()
	go func() {
		for {
			select {
			case <-runtimeCtx.Done():
				return
			case gap := <-runtime.Gaps():
				if gap != nil {
					log.Printf("fi-property-catalog-sequencer: ordered runtime gap: %v", gap)
				}
			}
		}
	}()

	receipts, err := propertycatalog.NewCandidateReceiptStore(cfg.receipts)
	if err != nil {
		return fmt.Errorf("construct candidate receipt store: %w", err)
	}
	source, err := propertycatalog.NewFranzCandidateSource(cfg.candidate)
	if err != nil {
		return fmt.Errorf("construct candidate source: %w", err)
	}
	sequencer, err := propertycatalog.NewCandidateSequencer(
		cfg.candidate.Topic, source, receipts, runtime,
	)
	if err != nil {
		source.Close()
		return err
	}
	defer sequencer.Close()
	if skipped := sequencer.SkippedCandidates(); skipped != 0 {
		log.Printf(
			"fi-property-catalog-sequencer: durable candidate rollout gaps at startup: skipped_total=%d",
			skipped,
		)
	}
	go func() {
		for {
			select {
			case <-ctx.Done():
				return
			case gap := <-sequencer.Gaps():
				if gap != nil {
					log.Printf("fi-property-catalog-sequencer: candidate rollout gap: %v", gap)
				}
			}
		}
	}()
	return sequencer.Run(ctx)
}

func loadConfig(lookup lookupEnvFunc) (commandConfig, error) {
	if lookup == nil {
		return commandConfig{}, errors.New("environment lookup is required")
	}
	if value, _ := lookup(envMode); value != string(propertycatalog.RuntimeSequencer) {
		return commandConfig{}, fmt.Errorf("%s must equal %q exactly", envMode, propertycatalog.RuntimeSequencer)
	}
	environment, err := required(lookup, envEnvironment)
	if err != nil {
		return commandConfig{}, err
	}
	epoch, err := requiredUint16(lookup, envEpoch)
	if err != nil {
		return commandConfig{}, err
	}
	projection, err := requiredUint16(lookup, envProjection)
	if err != nil {
		return commandConfig{}, err
	}
	streamID, err := required(lookup, envStreamID)
	if err != nil {
		return commandConfig{}, err
	}
	fenceFile, err := required(lookup, envFenceFile)
	if err != nil {
		return commandConfig{}, err
	}
	spoolDir, err := required(lookup, envSpoolDir)
	if err != nil {
		return commandConfig{}, err
	}
	outputBrokersText, err := required(lookup, envOutputBrokers)
	if err != nil {
		return commandConfig{}, err
	}
	outputTopic, err := required(lookup, envOutputTopic)
	if err != nil {
		return commandConfig{}, err
	}
	candidateBrokersText, err := required(lookup, envCandidateBrokers)
	if err != nil {
		return commandConfig{}, err
	}
	candidateTopic, err := required(lookup, envCandidateTopic)
	if err != nil {
		return commandConfig{}, err
	}
	if candidateTopic == outputTopic {
		return commandConfig{}, errors.New("candidate and ordered-output Kafka topics must be distinct")
	}
	candidateGroup, err := required(lookup, envCandidateGroup)
	if err != nil {
		return commandConfig{}, err
	}
	candidateInstance, err := required(lookup, envCandidateInstance)
	if err != nil {
		return commandConfig{}, err
	}
	transactionalID, err := required(lookup, envTransactionalID)
	if err != nil {
		return commandConfig{}, err
	}

	runtime := propertycatalog.RuntimeConfig{
		Mode: propertycatalog.RuntimeSequencer, Environment: environment,
		CatalogEpoch: epoch, ProjectionVersion: projection, ProducerStreamID: streamID,
		RevisionFenceFile: fenceFile, SpoolDirectory: spoolDir,
		Kafka: propertycatalog.KafkaRuntimeConfig{
			Brokers: splitCSV(outputBrokersText), Topic: outputTopic,
		},
	}
	if value, present := lookup(envDevAck); present {
		runtime.DevelopmentAcknowledgement = value
	}
	if value, present := lookup(envProdAck); present {
		runtime.ProductionAcknowledgement = value
	}
	if value, present := lookup(envScopeMode); present {
		runtime.WorkspaceScopeMode = propertycatalog.WorkspaceScopeMode(value)
	}
	if value, present := lookup(envAllowlist); present {
		runtime.WorkspaceAllowlist = splitCSV(value)
	}
	if value, present := lookup(envOutputClient); present {
		runtime.Kafka.ClientID = value
	}
	if err := optionalDuration(lookup, envReplayInterval, &runtime.ReplayInterval, 30*time.Second); err != nil {
		return commandConfig{}, err
	}
	if err := optionalDuration(lookup, envShutdownTimeout, &runtime.ShutdownTimeout, propertycatalog.MaxShutdownTimeout); err != nil {
		return commandConfig{}, err
	}
	if err := optionalDuration(lookup, envOutputDeliveryTimeout, &runtime.Kafka.DeliveryTimeout, propertycatalog.MaxDeliveryTimeout); err != nil {
		return commandConfig{}, err
	}
	for name, target := range map[string]*int{
		envQueueDepth: &runtime.QueueDepth, envMaxSpansPerBatch: &runtime.MaxSpansPerBatch,
		envMaxKeysPerSpan: &runtime.MaxKeysPerSpan, envMaxArrayMembers: &runtime.MaxArrayMembersPerSpan,
		envMaxEncodedBytes: &runtime.MaxEncodedBytesPerSpan, envMaxChunkRows: &runtime.MaxChunkRows,
		envMaxChunkBytes: &runtime.MaxChunkBytes, envMaxSpoolFiles: &runtime.MaxSpoolFiles,
	} {
		if err := optionalPositiveInt(lookup, name, target); err != nil {
			return commandConfig{}, err
		}
	}
	if err := optionalPositiveInt64(lookup, envMaxSpoolBytes, &runtime.MaxSpoolBytes); err != nil {
		return commandConfig{}, err
	}
	runtime = runtime.WithDefaults()
	if err := runtime.Validate(); err != nil {
		return commandConfig{}, err
	}

	transactionTimeout := time.Duration(0)
	if err := optionalDuration(lookup, envTransactionTimeout, &transactionTimeout, maxTransactionTimeout); err != nil {
		return commandConfig{}, err
	}
	startupTimeout := defaultStartupTimeout
	if err := optionalDuration(lookup, envStartupTimeout, &startupTimeout, maxStartupTimeout); err != nil {
		return commandConfig{}, err
	}
	candidateClient := map[string]string{
		propertycatalog.DevelopmentEnvironment: "fi-property-catalog-sequencer-candidate-v1-dev",
		propertycatalog.ProductionEnvironment:  "fi-property-catalog-sequencer-candidate-v1-prod",
	}[environment]
	if value, present := lookup(envCandidateClient); present {
		candidateClient = value
	}
	receiptConfig := propertycatalog.CandidateReceiptStoreConfig{
		Directory: filepath.Join(spoolDir, "candidate-receipts"), Topic: candidateTopic,
	}
	if err := optionalPositiveInt(lookup, envReceiptFiles, &receiptConfig.MaxPendingFiles); err != nil {
		return commandConfig{}, err
	}
	if err := optionalPositiveInt64(lookup, envReceiptBytes, &receiptConfig.MaxPendingBytes); err != nil {
		return commandConfig{}, err
	}
	if err := optionalPositiveInt(lookup, envRecentCandidateIDs, &receiptConfig.MaxRecentIDs); err != nil {
		return commandConfig{}, err
	}

	result := commandConfig{
		runtime: runtime,
		output: propertycatalog.FranzProducerConfig{
			Brokers: runtime.Kafka.Brokers, Topic: runtime.Kafka.Topic,
			ClientID: runtime.Kafka.ClientID, DeliveryTimeout: runtime.Kafka.DeliveryTimeout,
			TransactionalID: transactionalID, TransactionTimeout: transactionTimeout,
		},
		candidate: propertycatalog.FranzCandidateSourceConfig{
			Brokers: splitCSV(candidateBrokersText), Topic: candidateTopic,
			GroupID: candidateGroup, ClientID: candidateClient, InstanceID: candidateInstance,
		},
		receipts: receiptConfig, transactionalID: transactionalID, startupTimeout: startupTimeout,
	}
	// Validate all Kafka identities before any local lock or network adapter is
	// constructed. Constructors repeat these checks as defense in depth.
	if err := propertycatalog.ValidateFranzProducerConfig(result.output); err != nil {
		return commandConfig{}, err
	}
	if err := propertycatalog.ValidateFranzCandidateSourceConfig(result.candidate); err != nil {
		return commandConfig{}, err
	}
	return result, nil
}

func required(lookup lookupEnvFunc, name string) (string, error) {
	value, present := lookup(name)
	if !present || value == "" || strings.TrimSpace(value) != value {
		return "", fmt.Errorf("%s is required and must not be padded", name)
	}
	return value, nil
}

func requiredUint16(lookup lookupEnvFunc, name string) (uint16, error) {
	value, err := required(lookup, name)
	if err != nil {
		return 0, err
	}
	parsed, parseErr := strconv.ParseUint(value, 10, 16)
	if parseErr != nil || parsed == 0 {
		return 0, fmt.Errorf("%s must be a non-zero UInt16", name)
	}
	return uint16(parsed), nil
}

func splitCSV(value string) []string {
	parts := strings.Split(value, ",")
	result := make([]string, 0, len(parts))
	for _, part := range parts {
		if trimmed := strings.TrimSpace(part); trimmed != "" {
			result = append(result, trimmed)
		}
	}
	return result
}

func optionalDuration(lookup lookupEnvFunc, name string, target *time.Duration, maximum time.Duration) error {
	value, present := lookup(name)
	if !present {
		return nil
	}
	if value == "" || strings.TrimSpace(value) != value {
		return fmt.Errorf("%s must be an unpadded positive duration", name)
	}
	parsed, err := time.ParseDuration(value)
	if err != nil || parsed <= 0 || (maximum > 0 && parsed > maximum) {
		return fmt.Errorf("%s must be in (0,%s]", name, maximum)
	}
	*target = parsed
	return nil
}

func optionalPositiveInt(lookup lookupEnvFunc, name string, target *int) error {
	value, present := lookup(name)
	if !present {
		return nil
	}
	parsed, err := strconv.ParseInt(value, 10, 32)
	if err != nil || parsed <= 0 {
		return fmt.Errorf("%s must be a positive integer", name)
	}
	*target = int(parsed)
	return nil
}

func optionalPositiveInt64(lookup lookupEnvFunc, name string, target *int64) error {
	value, present := lookup(name)
	if !present {
		return nil
	}
	parsed, err := strconv.ParseInt(value, 10, 64)
	if err != nil || parsed <= 0 {
		return fmt.Errorf("%s must be a positive integer", name)
	}
	*target = parsed
	return nil
}
