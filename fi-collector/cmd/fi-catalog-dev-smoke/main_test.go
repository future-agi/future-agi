package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"strings"
	"testing"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/catalogkafka"
	"github.com/future-agi/future-agi/fi-collector/pkg/catalogwriter"
)

const (
	testSparseProject = "11111111-1111-4111-8111-111111111111"
	testDenseProject  = "22222222-2222-4222-8222-222222222222"
	testStreamID      = "33333333-3333-4333-8333-333333333333"
)

var fixedNow = time.Date(2026, time.August, 13, 20, 0, 0, 123456000, time.UTC)

type sinkCall struct {
	table string
	rows  []map[string]any
}

type fakeCatalogSink struct {
	calls       []sinkCall
	err         error
	deliveryErr error
}

func (s *fakeCatalogSink) InsertCatalog(
	_ context.Context, table catalogwriter.Table, rows []map[string]any,
) error {
	if s.err != nil {
		return s.err
	}
	s.calls = append(s.calls, sinkCall{table: string(table), rows: cloneRows(rows)})
	return nil
}

func (s *fakeCatalogSink) InsertDelivery(_ context.Context, rows []map[string]any) error {
	if s.deliveryErr != nil {
		return s.deliveryErr
	}
	if s.err != nil {
		return s.err
	}
	s.calls = append(s.calls, sinkCall{table: catalogwriter.DeliveryTableName, rows: cloneRows(rows)})
	return nil
}

func TestDirectResumeOnlyRecoversPendingWALWithoutRestaging(t *testing.T) {
	root := filepath.Join(t.TempDir(), "property-catalog-dev-resume")
	args := validDirectArgs(filepath.Join(root, "spool"))
	failing := &fakeCatalogSink{deliveryErr: errors.New("ledger unavailable")}
	dependencies := runtimeDependencies{
		newSink: func(catalogwriter.ClickHouseSinkConfig) (catalogSink, error) { return failing, nil },
		newProducer: func(catalogkafka.FranzProducerConfig) (envelopeProducer, error) {
			return &fakeEnvelopeProducer{}, nil
		},
		now: func() time.Time { return fixedNow },
	}
	if err := runCLI(args, &bytes.Buffer{}, dependencies); err == nil ||
		!strings.Contains(err.Error(), "ledger unavailable") {
		t.Fatalf("first-run error=%v", err)
	}

	recoveredSink := &fakeCatalogSink{}
	dependencies.newSink = func(catalogwriter.ClickHouseSinkConfig) (catalogSink, error) {
		return recoveredSink, nil
	}
	var output bytes.Buffer
	if err := runCLI(append(args, "--resume-only"), &output, dependencies); err != nil {
		t.Fatal(err)
	}
	evidence := decodeEvidence(t, output.Bytes())
	if evidence.ReplayAttempted != 2 || evidence.ReplayDelivered != 2 ||
		len(evidence.Fixtures) != 0 || evidence.InputSpans != 0 || len(evidence.Envelopes) != 2 {
		t.Fatalf("resume evidence=%#v", evidence)
	}
	assertOnlyCatalogWrites(t, recoveredSink.calls)
}

type fakeEnvelopeProducer struct {
	envelopes []catalogkafka.WireEnvelope
	closed    bool
	err       error
}

func (p *fakeEnvelopeProducer) Publish(_ context.Context, envelope catalogkafka.WireEnvelope) error {
	if p.err != nil {
		return p.err
	}
	raw, err := envelope.MarshalBinary()
	if err != nil {
		return err
	}
	defensive, err := catalogkafka.ParseWireEnvelope(raw)
	if err != nil {
		return err
	}
	p.envelopes = append(p.envelopes, defensive)
	return nil
}

func (p *fakeEnvelopeProducer) Close() { p.closed = true }

func TestDirectSmokeUsesActualBuilderAndOnlyCatalogTables(t *testing.T) {
	t.Parallel()
	root := filepath.Join(t.TempDir(), "property-catalog-dev-direct")
	spool := filepath.Join(root, "spool")
	args := validDirectArgs(spool)

	firstSink := &fakeCatalogSink{}
	firstOutput := runWithDependencies(t, args, firstSink, &fakeEnvelopeProducer{})
	first := decodeEvidence(t, firstOutput)
	assertDirectEvidence(t, first, 1)
	assertOnlyCatalogWrites(t, firstSink.calls)

	// Restarting with the same WAL root must load the durable per-project
	// sequencer state and continue the version-3 payload chain.
	secondSink := &fakeCatalogSink{}
	secondOutput := runWithDependencies(t, args, secondSink, &fakeEnvelopeProducer{})
	second := decodeEvidence(t, secondOutput)
	assertDirectEvidence(t, second, 2)
	assertOnlyCatalogWrites(t, secondSink.calls)
	assertDirectLedgerChain(t, firstSink.calls, secondSink.calls)

	stateDirectory := filepath.Join(spool, catalogkafka.DirectPublisherStateDirectoryName)
	entries, err := os.ReadDir(stateDirectory)
	if err != nil || len(entries) != 2 {
		t.Fatalf("direct publisher state entries=%d err=%v", len(entries), err)
	}
	for _, entry := range entries {
		info, infoErr := entry.Info()
		if infoErr != nil {
			t.Fatalf("inspect direct state %s: %v", entry.Name(), infoErr)
		}
		if !info.Mode().IsRegular() || info.Mode().Perm() != 0o600 {
			t.Fatalf("unsafe direct state %s mode=%v", entry.Name(), info.Mode())
		}
	}
}

func TestKafkaProduceSmokePublishesProjectScopedV3EnvelopesWithoutClickHouse(t *testing.T) {
	t.Parallel()
	root := filepath.Join(t.TempDir(), "property-catalog-dev-kafka")
	spool := filepath.Join(root, "spool")
	state := filepath.Join(root, "state")
	producer := &fakeEnvelopeProducer{}
	sinkConstructed := false
	dependencies := runtimeDependencies{
		newSink: func(catalogwriter.ClickHouseSinkConfig) (catalogSink, error) {
			sinkConstructed = true
			return nil, errors.New("unexpected ClickHouse construction")
		},
		newProducer: func(catalogkafka.FranzProducerConfig) (envelopeProducer, error) {
			return producer, nil
		},
		now: func() time.Time { return fixedNow },
	}
	args := []string{
		"--mode", "kafka-produce", "--environment", devEnvironment,
		"--ack", devAcknowledgement, "--epoch", "91",
		"--sparse-project-id", testSparseProject, "--dense-project-id", testDenseProject,
		"--producer-stream-id", testStreamID, "--spool-dir", spool,
		"--state-dir", state, "--kafka-brokers", "127.0.0.1:29092",
		"--kafka-topic", "property-catalog.dev.span-attribute-catalog.v1", "--timeout", "10s",
	}
	var output bytes.Buffer
	if err := runCLI(args, &output, dependencies); err != nil {
		t.Fatal(err)
	}
	if sinkConstructed {
		t.Fatal("kafka-produce constructed a ClickHouse sink")
	}
	if !producer.closed {
		t.Fatal("Kafka producer was not closed")
	}
	if len(producer.envelopes) != 2 {
		t.Fatalf("published %d envelopes, require two project-scoped envelopes", len(producer.envelopes))
	}
	projects := make([]string, 0, 2)
	for _, envelope := range producer.envelopes {
		snapshot := envelope.Snapshot()
		projects = append(projects, snapshot.ProjectID)
		if snapshot.Format != catalogkafka.EnvelopeFormat || snapshot.Version != catalogkafka.EnvelopeVersion {
			t.Fatalf("wrong envelope contract: %#v", snapshot)
		}
		if snapshot.Sequence != 1 || snapshot.PreviousPayloadSHA256 != catalogkafka.ZeroSHA256 {
			t.Fatalf("new project stream did not start at sequence one: %#v", snapshot)
		}
		if snapshot.ProducerStreamID != testStreamID || snapshot.Payload.Outcome != catalogkafka.OutcomeCommitted {
			t.Fatalf("wrong stream or outcome: %#v", snapshot)
		}
		if snapshot.Payload.SourceRows != 2 || snapshot.Payload.KeyRows == 0 || snapshot.Payload.ValueRows == 0 {
			t.Fatalf("missing source/catalog rows: %#v", snapshot.Payload)
		}
	}
	sort.Strings(projects)
	if !reflect.DeepEqual(projects, []string{testSparseProject, testDenseProject}) {
		t.Fatalf("published projects %v", projects)
	}

	evidence := decodeEvidence(t, output.Bytes())
	if evidence.Mode != modeKafkaProduce || evidence.Topic != "property-catalog.dev.span-attribute-catalog.v1" ||
		evidence.Database != "" || evidence.ReplayAttempted != 2 || evidence.ReplayDelivered != 2 ||
		len(evidence.Envelopes) != 2 || len(evidence.TablesWritten) != 0 {
		t.Fatalf("unexpected Kafka evidence: %#v", evidence)
	}
	entries, err := os.ReadDir(state)
	if err != nil {
		t.Fatal(err)
	}
	if len(entries) != 2 {
		t.Fatalf("publisher state has %d files, require one per project stream", len(entries))
	}
	for _, entry := range entries {
		info, err := entry.Info()
		if err != nil {
			t.Fatal(err)
		}
		if !info.Mode().IsRegular() || info.Mode().Perm() != 0o600 {
			t.Fatalf("publisher state %s has unsafe mode %s", entry.Name(), info.Mode())
		}
	}
}

func TestSmokeFailsClosedBeforeNetwork(t *testing.T) {
	t.Parallel()
	base := filepath.Join(t.TempDir(), "property-catalog-dev-guard", "spool")
	valid := validDirectArgs(base)
	tests := []struct {
		name    string
		mutate  func([]string) []string
		message string
	}{
		{"production environment", replaceFlag("--environment", "production"), "environment"},
		{"missing acknowledgement", replaceFlag("--ack", "yes"), "acknowledgement"},
		{"remote ClickHouse", replaceFlag("--clickhouse-url", "http://192.0.2.1:18123"), "loopback"},
		{"non-isolated database", replaceFlag("--database", "futureagi"), "isolated"},
		{"long timeout", replaceFlag("--timeout", "16s"), "timeout"},
		{"relative spool", replaceFlag("--spool-dir", "property-catalog-dev-spool"), "absolute"},
		{"same fixture tenant", replaceFlag("--dense-project-id", testSparseProject), "different"},
	}
	for _, test := range tests {
		test := test
		t.Run(test.name, func(t *testing.T) {
			_, err := parseCLI(test.mutate(append([]string(nil), valid...)))
			if err == nil || !strings.Contains(err.Error(), test.message) {
				t.Fatalf("got %v, require error containing %q", err, test.message)
			}
		})
	}
}

func TestKafkaGuardsRejectRemoteAndMixedSettings(t *testing.T) {
	t.Parallel()
	root := filepath.Join(t.TempDir(), "property-catalog-dev-kafka-guard")
	valid := []string{
		"--mode", "kafka-produce", "--environment", devEnvironment,
		"--ack", devAcknowledgement, "--epoch", "91",
		"--sparse-project-id", testSparseProject, "--dense-project-id", testDenseProject,
		"--producer-stream-id", testStreamID,
		"--spool-dir", filepath.Join(root, "spool"), "--state-dir", filepath.Join(root, "state"),
		"--kafka-brokers", "localhost:29092", "--kafka-topic", "property-catalog.dev.catalog.v1",
	}
	if _, err := parseCLI(valid); err != nil {
		t.Fatalf("valid Kafka config rejected: %v", err)
	}
	remote := replaceFlag("--kafka-brokers", "kafka.internal:9092")(append([]string(nil), valid...))
	if _, err := parseCLI(remote); err == nil || !strings.Contains(err.Error(), "loopback") {
		t.Fatalf("remote broker error = %v", err)
	}
	mixed := append(append([]string(nil), valid...), "--clickhouse-url", "http://127.0.0.1:18123")
	if _, err := parseCLI(mixed); err == nil || !strings.Contains(err.Error(), "rejects ClickHouse") {
		t.Fatalf("mixed transport error = %v", err)
	}
}

func TestDevDirectoryRejectsSymlinkTarget(t *testing.T) {
	t.Parallel()
	root := filepath.Join(t.TempDir(), "property-catalog-dev-symlink")
	realDirectory := filepath.Join(root, "real")
	if err := os.MkdirAll(realDirectory, 0o700); err != nil {
		t.Fatal(err)
	}
	link := filepath.Join(root, "spool")
	if err := os.Symlink(realDirectory, link); err != nil {
		t.Skipf("symlinks unavailable: %v", err)
	}
	if err := validateDevDirectory("spool", link); err == nil || !strings.Contains(err.Error(), "symlink") {
		t.Fatalf("symlink error = %v", err)
	}
}

func validDirectArgs(spool string) []string {
	return []string{
		"--mode", "direct", "--environment", devEnvironment, "--ack", devAcknowledgement,
		"--epoch", "91", "--sparse-project-id", testSparseProject,
		"--dense-project-id", testDenseProject, "--producer-stream-id", testStreamID,
		"--spool-dir", spool, "--clickhouse-url", "http://127.0.0.1:18123",
		"--database", "legacy_catalog_snapshot", "--timeout", "10s",
	}
}

func runWithDependencies(
	t *testing.T, args []string, sink *fakeCatalogSink, producer *fakeEnvelopeProducer,
) []byte {
	t.Helper()
	dependencies := runtimeDependencies{
		newSink: func(catalogwriter.ClickHouseSinkConfig) (catalogSink, error) { return sink, nil },
		newProducer: func(catalogkafka.FranzProducerConfig) (envelopeProducer, error) {
			return producer, nil
		},
		now: func() time.Time { return fixedNow },
	}
	var output bytes.Buffer
	if err := runCLI(args, &output, dependencies); err != nil {
		t.Fatal(err)
	}
	return output.Bytes()
}

func decodeEvidence(t *testing.T, encoded []byte) smokeEvidence {
	t.Helper()
	decoder := json.NewDecoder(bytes.NewReader(encoded))
	decoder.DisallowUnknownFields()
	var evidence smokeEvidence
	if err := decoder.Decode(&evidence); err != nil {
		t.Fatalf("decode evidence: %v\n%s", err, encoded)
	}
	return evidence
}

func assertDirectEvidence(t *testing.T, evidence smokeEvidence, wantSequence uint64) {
	t.Helper()
	if evidence.Format != evidenceFormat || evidence.Version != evidenceVersion ||
		evidence.Environment != devEnvironment || evidence.Mode != modeDirect ||
		evidence.CatalogEpoch != 91 || evidence.Database != "legacy_catalog_snapshot" ||
		evidence.Topic != "" || evidence.ProducerStreamID != testStreamID ||
		evidence.ReplayAttempted != 2 || evidence.ReplayDelivered != 2 || len(evidence.Fixtures) != 2 ||
		len(evidence.Envelopes) != 2 || evidence.ElapsedMilliseconds != 0 {
		t.Fatalf("unexpected direct evidence: %#v", evidence)
	}
	if len(evidence.FixtureSHA256) != 64 || evidence.InputSpans != 4 ||
		evidence.KeyRows == 0 || evidence.ValueRows == 0 {
		t.Fatalf("missing deterministic counts/hashes: %#v", evidence)
	}
	fixtures := make(map[string]fixtureEvidence, 2)
	for _, fixture := range evidence.Fixtures {
		fixtures[fixture.Name] = fixture
		if fixture.AcceptedSpans != fixture.InputSpans || len(fixture.GapReasons) != 0 ||
			len(fixture.JobSHA256) != 64 {
			t.Fatalf("incomplete fixture evidence: %#v", fixture)
		}
	}
	if fixtures["dense"].KeyRows <= fixtures["sparse"].KeyRows ||
		fixtures["dense"].ValueRows <= fixtures["sparse"].ValueRows {
		t.Fatalf("dense fixture is not denser: %#v", fixtures)
	}
	for _, envelope := range evidence.Envelopes {
		if envelope.Sequence != wantSequence || envelope.Outcome != string(catalogkafka.OutcomeCommitted) ||
			len(envelope.EnvelopeID) != 64 || len(envelope.PayloadSHA256) != 64 {
			t.Fatalf("invalid direct v3 receipt: %#v", envelope)
		}
	}
	wantTables := []string{
		string(catalogwriter.KeyTable), string(catalogwriter.ValueTable), catalogwriter.DeliveryTableName,
	}
	if !reflect.DeepEqual(evidence.TablesWritten, wantTables) {
		t.Fatalf("tables written %v, want %v", evidence.TablesWritten, wantTables)
	}
}

func assertDirectLedgerChain(t *testing.T, firstCalls, secondCalls []sinkCall) {
	t.Helper()
	first := deliveryRowsByProject(t, firstCalls)
	second := deliveryRowsByProject(t, secondCalls)
	if len(first) != 2 || len(second) != 2 {
		t.Fatalf("delivery projects first=%d second=%d", len(first), len(second))
	}
	for projectID, firstRow := range first {
		secondRow, ok := second[projectID]
		if !ok {
			t.Fatalf("second run omitted project %s", projectID)
		}
		if firstRow["sequence"] != uint64(1) || secondRow["sequence"] != uint64(2) ||
			firstRow["previous_payload_sha256"] != catalogkafka.ZeroSHA256 ||
			secondRow["previous_payload_sha256"] != firstRow["payload_sha256"] {
			t.Fatalf("broken direct chain first=%#v second=%#v", firstRow, secondRow)
		}
		for _, row := range []map[string]any{firstRow, secondRow} {
			if row["envelope_version"] != catalogkafka.EnvelopeVersion ||
				row["transport"] != "direct" || row["kafka_partition"] != int32(-1) ||
				row["kafka_offset"] != int64(-1) || row["outcome"] != "committed" {
				t.Fatalf("invalid direct v3 ledger row: %#v", row)
			}
		}
	}
}

func deliveryRowsByProject(t *testing.T, calls []sinkCall) map[string]map[string]any {
	t.Helper()
	out := make(map[string]map[string]any)
	for _, call := range calls {
		if call.table != catalogwriter.DeliveryTableName {
			continue
		}
		for _, row := range call.rows {
			projectID, ok := row["project_id"].(string)
			if !ok || projectID == "" {
				t.Fatalf("delivery row has invalid project: %#v", row)
			}
			out[projectID] = row
		}
	}
	return out
}

func assertOnlyCatalogWrites(t *testing.T, calls []sinkCall) {
	t.Helper()
	allowed := map[string]bool{
		string(catalogwriter.KeyTable): true, string(catalogwriter.ValueTable): true,
		catalogwriter.DeliveryTableName: true,
	}
	counts := make(map[string]int)
	for _, call := range calls {
		if !allowed[call.table] {
			t.Fatalf("wrote forbidden table %q", call.table)
		}
		counts[call.table] += len(call.rows)
		for _, row := range call.rows {
			if _, hasTraceID := row["trace_id"]; hasTraceID {
				t.Fatalf("catalog write retained a canonical span field: %#v", row)
			}
		}
	}
	if counts[string(catalogwriter.KeyTable)] == 0 || counts[string(catalogwriter.ValueTable)] == 0 ||
		counts[catalogwriter.DeliveryTableName] != 2 {
		t.Fatalf("unexpected table row counts: %v", counts)
	}
}

func replaceFlag(name, value string) func([]string) []string {
	return func(arguments []string) []string {
		for index := 0; index+1 < len(arguments); index += 2 {
			if arguments[index] == name {
				arguments[index+1] = value
				return arguments
			}
		}
		return append(arguments, name, value)
	}
}
