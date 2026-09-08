// Command fi-catalog-dev-smoke exercises the span-attribute catalog delivery
// paths against an isolated development environment. It is intentionally not
// a general migration or ingestion command: its ClickHouse sink is closed over
// the three new catalog tables, its endpoint must be loopback, and several
// independent development sentinels must agree before any network call.
package main

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"math"
	"net"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/catalogkafka"
	"github.com/future-agi/future-agi/fi-collector/pkg/catalogwriter"
	"github.com/google/uuid"
)

const (
	devEnvironment     = "development"
	devAcknowledgement = "PROPERTY_CATALOG_DEV_ONLY"
	devPathMarker      = "property-catalog-dev"
	devTopicPrefix     = "property-catalog.dev."
	evidenceFormat     = "futureagi.span-attribute-catalog-dev-smoke"
	evidenceVersion    = uint16(1)
	maxSmokeTimeout    = 15 * time.Second
	maxSinkTimeout     = 10 * time.Second
)

var databasePattern = regexp.MustCompile(`^[a-z][a-z0-9_]*$`)

var reservedDatabases = map[string]struct{}{
	"default": {}, "futureagi": {}, "information_schema": {}, "property_catalog": {}, "system": {},
}

type smokeMode string

const (
	modeDirect       smokeMode = "direct"
	modeKafkaProduce smokeMode = "kafka-produce"
)

type cliConfig struct {
	mode             smokeMode
	environment      string
	acknowledgement  string
	epoch            uint16
	sparseProjectID  string
	denseProjectID   string
	producerStreamID string
	spoolDirectory   string
	stateDirectory   string
	timeout          time.Duration
	clickHouseURL    string
	database         string
	username         string
	brokers          []string
	topic            string
	resumeOnly       bool
}

type catalogSink interface {
	InsertCatalog(context.Context, catalogwriter.Table, []map[string]any) error
	InsertDelivery(context.Context, []map[string]any) error
}

type envelopePublisher interface {
	Publish(context.Context, catalogkafka.WireEnvelope) error
}

type envelopeProducer interface {
	envelopePublisher
	Close()
}

type runtimeDependencies struct {
	newSink     func(catalogwriter.ClickHouseSinkConfig) (catalogSink, error)
	newProducer func(catalogkafka.FranzProducerConfig) (envelopeProducer, error)
	now         func() time.Time
}

func productionDependencies() runtimeDependencies {
	return runtimeDependencies{
		newSink: func(config catalogwriter.ClickHouseSinkConfig) (catalogSink, error) {
			return catalogwriter.NewClickHouseSink(config)
		},
		newProducer: func(config catalogkafka.FranzProducerConfig) (envelopeProducer, error) {
			return catalogkafka.NewFranzProducer(config)
		},
		now: time.Now,
	}
}

type fixtureEvidence struct {
	Name          string   `json:"name"`
	ProjectID     string   `json:"project_id"`
	InputSpans    int      `json:"input_spans"`
	AcceptedSpans int      `json:"accepted_spans"`
	KeyRows       int      `json:"key_rows"`
	ValueRows     int      `json:"value_rows"`
	DuplicateRows int      `json:"duplicate_rows"`
	EncodedBytes  int      `json:"encoded_bytes"`
	GapReasons    []string `json:"gap_reasons"`
	JobSHA256     string   `json:"job_sha256"`
}

type envelopeEvidence struct {
	ProjectID     string `json:"project_id"`
	Sequence      uint64 `json:"sequence"`
	EnvelopeID    string `json:"envelope_id"`
	PayloadSHA256 string `json:"payload_sha256"`
	Outcome       string `json:"outcome"`
}

type smokeEvidence struct {
	Format              string             `json:"format"`
	Version             uint16             `json:"version"`
	Environment         string             `json:"environment"`
	Mode                smokeMode          `json:"mode"`
	CatalogEpoch        uint16             `json:"catalog_epoch"`
	ProducerStreamID    string             `json:"producer_stream_id"`
	Database            string             `json:"database,omitempty"`
	Topic               string             `json:"topic,omitempty"`
	FixtureSHA256       string             `json:"fixture_sha256"`
	Fixtures            []fixtureEvidence  `json:"fixtures"`
	InputSpans          int                `json:"input_spans"`
	KeyRows             int                `json:"key_rows"`
	ValueRows           int                `json:"value_rows"`
	ReplayAttempted     int                `json:"replay_attempted"`
	ReplayDelivered     int                `json:"replay_delivered"`
	Envelopes           []envelopeEvidence `json:"envelopes"`
	TablesWritten       []string           `json:"tables_written"`
	ElapsedMilliseconds int64              `json:"elapsed_milliseconds"`
}

type namedFixture struct {
	name      string
	projectID string
	rows      []map[string]any
}

func main() {
	if err := runCLI(os.Args[1:], os.Stdout, productionDependencies()); err != nil {
		_, _ = fmt.Fprintln(os.Stderr, "fi-catalog-dev-smoke:", err)
		os.Exit(2)
	}
}

func runCLI(arguments []string, output io.Writer, dependencies runtimeDependencies) error {
	config, err := parseCLI(arguments)
	if err != nil {
		return err
	}
	if output == nil {
		return errors.New("output writer is required")
	}
	if dependencies.now == nil || dependencies.newSink == nil || dependencies.newProducer == nil {
		return errors.New("runtime dependencies are incomplete")
	}

	started := dependencies.now()
	ctx, cancel := context.WithTimeout(context.Background(), config.timeout)
	defer cancel()
	evidence, err := execute(ctx, config, dependencies)
	if err != nil {
		return err
	}
	elapsed := dependencies.now().Sub(started)
	if elapsed < 0 {
		return errors.New("runtime clock moved backwards")
	}
	evidence.ElapsedMilliseconds = elapsed.Milliseconds()
	encoder := json.NewEncoder(output)
	encoder.SetEscapeHTML(false)
	encoder.SetIndent("", "  ")
	return encoder.Encode(evidence)
}

func parseCLI(arguments []string) (cliConfig, error) {
	flags := flag.NewFlagSet("fi-catalog-dev-smoke", flag.ContinueOnError)
	flags.SetOutput(io.Discard)
	var rawMode string
	var rawEpoch uint
	var rawBrokers string
	config := cliConfig{timeout: 10 * time.Second}
	flags.StringVar(&rawMode, "mode", "", "direct or kafka-produce")
	flags.StringVar(&config.environment, "environment", "", "must be development")
	flags.StringVar(&config.acknowledgement, "ack", "", "required destructive-safety sentinel")
	flags.UintVar(&rawEpoch, "epoch", 0, "non-zero catalog epoch")
	flags.StringVar(&config.sparseProjectID, "sparse-project-id", "", "isolated sparse-fixture project UUID")
	flags.StringVar(&config.denseProjectID, "dense-project-id", "", "isolated dense-fixture project UUID")
	flags.StringVar(&config.producerStreamID, "producer-stream-id", "", "producer stream UUID")
	flags.StringVar(&config.spoolDirectory, "spool-dir", "", "dedicated absolute catalog spool directory")
	flags.StringVar(&config.stateDirectory, "state-dir", "", "dedicated absolute Kafka publisher-state directory")
	flags.DurationVar(&config.timeout, "timeout", config.timeout, "whole-command timeout (maximum 15s)")
	flags.StringVar(&config.clickHouseURL, "clickhouse-url", "", "loopback ClickHouse HTTP endpoint")
	flags.StringVar(&config.database, "database", "", "isolated development catalog database")
	flags.StringVar(&config.username, "username", "", "ClickHouse username")
	flags.StringVar(&rawBrokers, "kafka-brokers", "", "comma-separated loopback Kafka brokers")
	flags.StringVar(&config.topic, "kafka-topic", "", "isolated property-catalog.dev.* topic")
	flags.BoolVar(&config.resumeOnly, "resume-only", false, "replay the existing durable spool without staging new fixtures")
	if err := flags.Parse(arguments); err != nil {
		return cliConfig{}, err
	}
	if flags.NArg() != 0 {
		return cliConfig{}, errors.New("positional arguments are not accepted")
	}
	if rawEpoch > math.MaxUint16 {
		return cliConfig{}, errors.New("epoch exceeds UInt16")
	}
	config.epoch = uint16(rawEpoch)
	config.mode = smokeMode(rawMode)
	if rawBrokers != "" {
		config.brokers = strings.Split(rawBrokers, ",")
	}
	if err := validateCLI(config); err != nil {
		return cliConfig{}, err
	}
	return config, nil
}

func validateCLI(config cliConfig) error {
	if config.environment != devEnvironment {
		return errors.New("environment must be exactly development")
	}
	if config.acknowledgement != devAcknowledgement {
		return errors.New("missing exact property-catalog development-only acknowledgement")
	}
	if config.epoch == 0 {
		return errors.New("catalog epoch must be non-zero")
	}
	if config.timeout <= 0 || config.timeout > maxSmokeTimeout {
		return errors.New("timeout must be in (0,15s]")
	}
	if err := validateCanonicalUUID("sparse project", config.sparseProjectID); err != nil {
		return err
	}
	if err := validateCanonicalUUID("dense project", config.denseProjectID); err != nil {
		return err
	}
	if config.sparseProjectID == config.denseProjectID {
		return errors.New("sparse and dense fixture projects must be different")
	}
	if err := validateCanonicalUUID("producer stream", config.producerStreamID); err != nil {
		return err
	}
	if err := validateDevDirectory("spool", config.spoolDirectory); err != nil {
		return err
	}

	switch config.mode {
	case modeDirect:
		if config.stateDirectory != "" || len(config.brokers) != 0 || config.topic != "" {
			return errors.New("direct mode rejects Kafka settings")
		}
		if err := validateLoopbackHTTP(config.clickHouseURL); err != nil {
			return err
		}
		_, reserved := reservedDatabases[config.database]
		if len(config.database) > 128 || !databasePattern.MatchString(config.database) || reserved {
			return errors.New("database must be a safe isolated development identifier")
		}
	case modeKafkaProduce:
		if config.clickHouseURL != "" || config.database != "" || config.username != "" {
			return errors.New("kafka-produce mode rejects ClickHouse settings")
		}
		if err := validateDevDirectory("publisher state", config.stateDirectory); err != nil {
			return err
		}
		if config.spoolDirectory == config.stateDirectory {
			return errors.New("spool and publisher-state directories must be different")
		}
		if len(config.brokers) == 0 {
			return errors.New("kafka-produce mode requires at least one loopback broker")
		}
		for index, broker := range config.brokers {
			if err := validateLoopbackBroker(broker); err != nil {
				return fmt.Errorf("Kafka broker %d: %w", index, err)
			}
		}
		if !strings.HasPrefix(config.topic, devTopicPrefix) || len(config.topic) <= len(devTopicPrefix) {
			return fmt.Errorf("Kafka topic must use the isolated %s* prefix", devTopicPrefix)
		}
	default:
		return errors.New("mode must be exactly direct or kafka-produce")
	}
	return nil
}

func validateCanonicalUUID(name, value string) error {
	parsed, err := uuid.Parse(value)
	if err != nil || parsed == uuid.Nil || parsed.String() != value {
		return fmt.Errorf("%s must be a canonical non-zero UUID", name)
	}
	return nil
}

func validateDevDirectory(name, path string) error {
	if path == "" || !filepath.IsAbs(path) || filepath.Clean(path) != path {
		return fmt.Errorf("%s directory must be a clean absolute path", name)
	}
	if filepath.Dir(path) == path || !pathHasMarker(path, devPathMarker) {
		return fmt.Errorf("%s directory must be dedicated and contain path marker %q", name, devPathMarker)
	}
	info, err := os.Lstat(path)
	if err == nil && (info.Mode()&os.ModeSymlink != 0 || !info.IsDir()) {
		return fmt.Errorf("%s path must be a real directory, not a symlink or file", name)
	}
	if err != nil && !errors.Is(err, os.ErrNotExist) {
		return fmt.Errorf("inspect %s directory: %w", name, err)
	}
	return nil
}

func pathHasMarker(path, marker string) bool {
	for _, component := range strings.Split(filepath.ToSlash(path), "/") {
		if strings.HasPrefix(component, marker) {
			return true
		}
	}
	return false
}

func validateLoopbackHTTP(raw string) error {
	parsed, err := url.Parse(raw)
	if err != nil || parsed.Scheme != "http" || parsed.Host == "" {
		return errors.New("ClickHouse URL must be an absolute loopback http endpoint")
	}
	if parsed.User != nil || parsed.RawQuery != "" || parsed.Fragment != "" ||
		(parsed.Path != "" && parsed.Path != "/") {
		return errors.New("ClickHouse URL must not contain credentials, path, query, or fragment")
	}
	if parsed.Port() == "" || !isLoopbackHost(parsed.Hostname()) {
		return errors.New("ClickHouse URL must contain an explicit port on a loopback host")
	}
	return nil
}

func validateLoopbackBroker(broker string) error {
	if broker == "" || strings.TrimSpace(broker) != broker || strings.Contains(broker, "://") {
		return errors.New("broker must be a host:port without whitespace or scheme")
	}
	host, port, err := net.SplitHostPort(broker)
	if err != nil || port == "" || !isLoopbackHost(host) {
		return errors.New("broker must have an explicit port on a loopback host")
	}
	return nil
}

func isLoopbackHost(host string) bool {
	if host == "localhost" {
		return true
	}
	parsed := net.ParseIP(host)
	return parsed != nil && parsed.IsLoopback()
}

func execute(ctx context.Context, config cliConfig, dependencies runtimeDependencies) (smokeEvidence, error) {
	fixtures := deterministicFixtures(config.sparseProjectID, config.denseProjectID)
	fixtureDigest, err := digestJSON(fixturesForDigest(fixtures))
	if err != nil {
		return smokeEvidence{}, fmt.Errorf("hash fixtures: %w", err)
	}
	writerConfig := catalogwriter.DefaultConfig()
	writerConfig.Enabled = true
	writerConfig.CatalogEpoch = config.epoch
	writerConfig.SpoolDir = config.spoolDirectory
	// A Kafka record is deliberately much smaller than the writer's general
	// 8 MiB ceiling. The smoke fixture stays below this cap in both modes.
	writerConfig.MaxJobEncodedBytes = 384 << 10
	writerConfig.MaxJobProjects = 1
	writerConfig.MaxChunkEncodedBytes = 256 << 10

	writer, err := catalogwriter.NewTransportWriter(writerConfig)
	if err != nil {
		return smokeEvidence{}, fmt.Errorf("construct catalog spool writer: %w", err)
	}
	allRows := make([]map[string]any, 0)
	fixtureNames := make(map[string]string, len(fixtures))
	for _, fixture := range fixtures {
		allRows = append(allRows, fixture.rows...)
		fixtureNames[fixture.projectID] = fixture.name
	}
	fixtureEvidenceRows := make([]fixtureEvidence, 0, len(fixtures))
	if !config.resumeOnly {
		staged := writer.StageCanonicalSpansByProject(allRows)
		if len(staged) != len(fixtures) {
			return smokeEvidence{}, fmt.Errorf("staging produced %d jobs, require %d project-scoped jobs", len(staged), len(fixtures))
		}
		for _, projectJob := range staged {
			report := projectJob.Report
			if len(report.Projects) != 1 || report.Projects[0].ProjectID == "" {
				return smokeEvidence{}, errors.New("staging produced an unscoped or mixed-project job")
			}
			if report.RejectedSpans != 0 || report.IncompleteSpans != 0 || report.RowsOmitted != 0 ||
				report.GlobalTruncated || len(report.BuildGapReasons) != 0 {
				return smokeEvidence{}, fmt.Errorf("fixture %s staged with a coverage gap", fixtureNames[report.Projects[0].ProjectID])
			}
			wireJob := catalogwriter.ExportWireJob(projectJob.Job)
			jobDigest, hashErr := digestJSON(wireJob)
			if hashErr != nil {
				return smokeEvidence{}, fmt.Errorf("hash staged job: %w", hashErr)
			}
			fixtureEvidenceRows = append(fixtureEvidenceRows, fixtureEvidence{
				Name: fixtureNames[report.Projects[0].ProjectID], ProjectID: report.Projects[0].ProjectID,
				InputSpans: report.InputSpans, AcceptedSpans: report.AcceptedSpans,
				KeyRows: report.KeyRows, ValueRows: report.ValueRows,
				DuplicateRows: report.DuplicateRows, EncodedBytes: report.EncodedBytes,
				GapReasons: append([]string{}, report.BuildGapReasons...), JobSHA256: jobDigest,
			})
			if err := writer.Submit(ctx, projectJob.Job); err != nil {
				return smokeEvidence{}, fmt.Errorf("spool fixture %s: %w", fixtureNames[report.Projects[0].ProjectID], err)
			}
		}
	}
	sort.Slice(fixtureEvidenceRows, func(i, j int) bool {
		return fixtureEvidenceRows[i].ProjectID < fixtureEvidenceRows[j].ProjectID
	})

	var replay catalogwriter.ReplayResult
	var envelopes []envelopeEvidence
	var tables []string
	switch config.mode {
	case modeDirect:
		sinkTimeout := min(config.timeout, maxSinkTimeout)
		sink, sinkErr := dependencies.newSink(catalogwriter.ClickHouseSinkConfig{
			URL: config.clickHouseURL, Database: config.database, Username: config.username,
			Password: os.Getenv("FI_CATALOG_DEV_CH_PASSWORD"), RequestTimeout: sinkTimeout,
			MaxExecutionTime: sinkTimeout, MaxRequestBytes: 1 << 20,
			MaxResponseBytes: 64 << 10, MaxMemoryUsage: 256 << 20, MaxThreads: 2,
		})
		if sinkErr != nil {
			return smokeEvidence{}, fmt.Errorf("construct catalog-only ClickHouse sink: %w", sinkErr)
		}
		directPublisher, publisherErr := catalogkafka.NewDirectClickHouseEnvelopePublisher(
			sink, sinkTimeout,
		)
		if publisherErr != nil {
			return smokeEvidence{}, fmt.Errorf("construct direct v3 ClickHouse publisher: %w", publisherErr)
		}
		recorder := &recordingProducer{delegate: directPublisher}
		publisher, publisherErr := catalogkafka.NewSpoolPublisher(catalogkafka.PublisherConfig{
			StateDirectory: filepath.Join(
				config.spoolDirectory, catalogkafka.DirectPublisherStateDirectoryName,
			),
			ProducerStreamID: config.producerStreamID,
			MaxChunkRows:     writerConfig.MaxChunkRows,
			MaxChunkBytes:    writerConfig.MaxChunkEncodedBytes,
		}, recorder)
		if publisherErr != nil {
			return smokeEvidence{}, fmt.Errorf("construct direct v3 sequencer: %w", publisherErr)
		}
		replay, err = writer.ReplayTo(ctx, publisher)
		if err != nil {
			return smokeEvidence{}, fmt.Errorf("direct catalog replay: %w", err)
		}
		envelopes = recorder.receiptsSnapshot()
		tables = []string{
			string(catalogwriter.KeyTable), string(catalogwriter.ValueTable),
			catalogwriter.DeliveryTableName,
		}
	case modeKafkaProduce:
		producer, producerErr := dependencies.newProducer(catalogkafka.FranzProducerConfig{
			Brokers: append([]string(nil), config.brokers...), Topic: config.topic,
			DeliveryTimeout: min(config.timeout, maxSinkTimeout),
		})
		if producerErr != nil {
			return smokeEvidence{}, fmt.Errorf("construct Kafka producer: %w", producerErr)
		}
		recorder := &recordingProducer{delegate: producer}
		defer recorder.Close()
		publisher, publisherErr := catalogkafka.NewSpoolPublisher(catalogkafka.PublisherConfig{
			StateDirectory: config.stateDirectory, ProducerStreamID: config.producerStreamID,
			MaxChunkRows: writerConfig.MaxChunkRows, MaxChunkBytes: writerConfig.MaxChunkEncodedBytes,
		}, recorder)
		if publisherErr != nil {
			return smokeEvidence{}, fmt.Errorf("construct Kafka spool publisher: %w", publisherErr)
		}
		replay, err = writer.ReplayTo(ctx, publisher)
		if err != nil {
			return smokeEvidence{}, fmt.Errorf("Kafka catalog replay: %w", err)
		}
		envelopes = recorder.receiptsSnapshot()
		tables = []string{}
	}

	evidence := smokeEvidence{
		Format: evidenceFormat, Version: evidenceVersion, Environment: config.environment,
		Mode: config.mode, CatalogEpoch: config.epoch, ProducerStreamID: config.producerStreamID,
		FixtureSHA256: fixtureDigest, Fixtures: fixtureEvidenceRows,
		ReplayAttempted: replay.Attempted, ReplayDelivered: replay.Delivered,
		Envelopes: envelopes, TablesWritten: tables,
	}
	if config.mode == modeDirect {
		evidence.Database = config.database
	} else {
		evidence.Topic = config.topic
	}
	for _, fixture := range fixtureEvidenceRows {
		evidence.InputSpans += fixture.InputSpans
		evidence.KeyRows += fixture.KeyRows
		evidence.ValueRows += fixture.ValueRows
	}
	return evidence, nil
}

func deterministicFixtures(sparseProjectID, denseProjectID string) []namedFixture {
	return []namedFixture{
		{
			name: "sparse", projectID: sparseProjectID,
			rows: []map[string]any{
				canonicalFixtureRow(sparseProjectID, "2026-08-13 12:00:00.000000",
					map[string]string{"deployment.environment": "development", "service.name": "checkout"},
					map[string]float64{"http.status_code": 200}, map[string]uint8{"trace.sampled": 1}, nil),
				canonicalFixtureRow(sparseProjectID, "2026-08-13 12:00:00.500000",
					map[string]string{"deployment.environment": "development", "service.name": "checkout"},
					map[string]float64{"http.status_code": 201}, map[string]uint8{"trace.sampled": 1}, nil),
			},
		},
		{
			name: "dense", projectID: denseProjectID,
			rows: []map[string]any{
				canonicalFixtureRow(denseProjectID, "2026-08-13 12:05:00.000000",
					map[string]string{
						"ai.model": "gpt-fixture", "deployment.environment": "development",
						"gen_ai.operation.name": "chat", "http.method": "POST",
						"http.route": "/v1/evaluate", "region": "asia-south1",
						"service.name": "evaluation-api", "span.kind": "server",
					},
					map[string]float64{
						"gen_ai.usage.input_tokens": 128, "gen_ai.usage.output_tokens": 32,
						"http.status_code": 200, "latency_ms": 42.125,
					},
					map[string]uint8{"cache.hit": 0, "evaluation.passed": 1, "trace.sampled": 1},
					map[string]any{
						"evaluation.labels": []any{"quality", "latency", float64(7), true},
						"request.metadata":  map[string]any{"fixture": true, "source": "dev-smoke"},
						"response.shape":    "fixture-json-scalar",
					}),
				canonicalFixtureRow(denseProjectID, "2026-08-13 12:05:01.250000",
					map[string]string{
						"ai.model": "gpt-fixture-2", "deployment.environment": "development",
						"gen_ai.operation.name": "chat", "http.method": "POST",
						"http.route": "/v1/evaluate", "region": "asia-south1",
						"service.name": "evaluation-api", "span.kind": "server",
					},
					map[string]float64{
						"gen_ai.usage.input_tokens": 256, "gen_ai.usage.output_tokens": 64,
						"http.status_code": 200, "latency_ms": 39.75,
					},
					map[string]uint8{"cache.hit": 1, "evaluation.passed": 1, "trace.sampled": 1},
					map[string]any{
						"evaluation.labels": []any{"quality", "cost", float64(7), true},
						"request.metadata":  map[string]any{"fixture": true, "source": "dev-smoke"},
						"response.shape":    "fixture-json-scalar",
					}),
			},
		},
	}
}

func canonicalFixtureRow(
	projectID, startTime string,
	stringsMap map[string]string,
	numbersMap map[string]float64,
	booleansMap map[string]uint8,
	extraMap map[string]any,
) map[string]any {
	if extraMap == nil {
		extraMap = map[string]any{}
	}
	return map[string]any{
		"project_id": projectID, "start_time": startTime,
		"attrs_string": stringsMap, "attrs_number": numbersMap,
		"attrs_bool": booleansMap, "attributes_extra": extraMap,
	}
}

func fixturesForDigest(fixtures []namedFixture) []map[string]any {
	out := make([]map[string]any, len(fixtures))
	for index, fixture := range fixtures {
		out[index] = map[string]any{
			"name": fixture.name, "project_id": fixture.projectID, "rows": fixture.rows,
		}
	}
	return out
}

func digestJSON(value any) (string, error) {
	encoded, err := json.Marshal(value)
	if err != nil {
		return "", err
	}
	digest := sha256.Sum256(encoded)
	return hex.EncodeToString(digest[:]), nil
}

func cloneRows(source []map[string]any) []map[string]any {
	out := make([]map[string]any, len(source))
	for index, row := range source {
		out[index] = make(map[string]any, len(row))
		for key, value := range row {
			out[index][key] = value
		}
	}
	return out
}

type recordingProducer struct {
	delegate envelopeProducer
	mu       sync.Mutex
	receipts []envelopeEvidence
}

func (p *recordingProducer) Publish(ctx context.Context, envelope catalogkafka.WireEnvelope) error {
	if p == nil || p.delegate == nil {
		return errors.New("nil recording producer")
	}
	if err := p.delegate.Publish(ctx, envelope); err != nil {
		return err
	}
	p.mu.Lock()
	p.receipts = append(p.receipts, envelopeEvidenceFromSnapshot(envelope.Snapshot()))
	p.mu.Unlock()
	return nil
}

func (p *recordingProducer) Close() {
	if p != nil && p.delegate != nil {
		p.delegate.Close()
	}
}

func (p *recordingProducer) receiptsSnapshot() []envelopeEvidence {
	p.mu.Lock()
	defer p.mu.Unlock()
	return append([]envelopeEvidence(nil), p.receipts...)
}

func envelopeEvidenceFromSnapshot(snapshot catalogkafka.EnvelopeSnapshot) envelopeEvidence {
	return envelopeEvidence{
		ProjectID: snapshot.ProjectID, Sequence: snapshot.Sequence,
		EnvelopeID: snapshot.EnvelopeID, PayloadSHA256: snapshot.PayloadSHA256,
		Outcome: string(snapshot.Payload.Outcome),
	}
}
