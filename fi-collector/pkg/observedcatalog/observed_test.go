package observedcatalog

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"reflect"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/twmb/franz-go/pkg/kgo"
)

func testSpan() ScopedSpan {
	return ScopedSpan{OrganizationID: "11111111-1111-4111-8111-111111111111", WorkspaceID: "22222222-2222-4222-8222-222222222222", Row: map[string]any{
		"org_id": "11111111-1111-4111-8111-111111111111", "project_id": "33333333-3333-4333-8333-333333333333", "start_time": "2026-09-08 01:02:03.123456",
		"attrs_string": map[string]string{"a": "hello"}, "attrs_number": map[string]float64{"n": 1.5}, "attrs_bool": map[string]uint8{"b": 1},
		"attributes_extra": map[string]any{"array": []any{"1", json.Number("1"), true, "1"}, "empty": []any{}, "map": map[string]any{"nested": "x"}, "null": nil}, "model": "model-a",
	}}
}

func testBatch(t *testing.T) Batch {
	t.Helper()
	b, r, err := Extract(testSpan(), DefaultLimits())
	if err != nil || !r.Complete {
		t.Fatalf("extract: %v %+v", err, r)
	}
	return b
}

func TestExtractLiveAndBackfillUseSameTypedContract(t *testing.T) {
	live := testSpan()
	before, _ := json.Marshal(live.Row)
	b, report, err := Extract(live, DefaultLimits())
	if err != nil || !report.Complete {
		t.Fatalf("%v %+v", err, report)
	}
	if len(b.Keys) != 8 || len(b.Values) != 7 {
		t.Fatalf("keys=%d values=%d", len(b.Keys), len(b.Values))
	}
	array := map[string]bool{}
	for _, row := range b.Values {
		if row.AttributeKey == "array" {
			if row.AttributeType != "array" {
				t.Fatal(row)
			}
			array[row.ValueJSON] = true
		}
	}
	if len(array) != 3 || !array[`"1"`] || !array[`1`] || !array[`true`] {
		t.Fatal(array)
	}
	backfill := testSpan()
	raw, _ := json.Marshal(backfill.Row["attributes_extra"])
	backfill.Row["attributes_extra"] = string(raw)
	backfill.Row["org_id"] = nil
	other, _, err := Extract(backfill, DefaultLimits())
	if err != nil || !reflect.DeepEqual(b, other) {
		t.Fatalf("backfill differs: %v", err)
	}
	after, _ := json.Marshal(live.Row)
	if string(before) != string(after) {
		t.Fatal("mutated canonical span")
	}
	backfill.Row["org_id"] = "44444444-4444-4444-8444-444444444444"
	if _, _, err := Extract(backfill, DefaultLimits()); err == nil {
		t.Fatal("org mismatch accepted")
	}
	live.ScopeError = "project_workspace_mismatch"
	if _, _, err := Extract(live, DefaultLimits()); err == nil {
		t.Fatal("scope error accepted")
	}
}

func TestEligibleValuesChunkWithoutCumulativeByteTruncation(t *testing.T) {
	span := testSpan()
	values := map[string]string{}
	for i := 0; i < 100; i++ {
		values[fmt.Sprintf("k%03d", i)] = strings.Repeat("x", 10<<10)
	}
	span.Row["attrs_string"] = values
	b, report, err := Extract(span, DefaultLimits())
	if err != nil || !report.Complete {
		t.Fatalf("%v %+v", err, report)
	}
	chunks, err := Chunk(b)
	if err != nil || len(chunks) < 3 {
		t.Fatalf("chunks=%d %v", len(chunks), err)
	}
	var retained int
	for _, chunk := range chunks {
		raw, err := Encode(chunk)
		if err != nil || len(raw) > MaxRecordBytes {
			t.Fatal(err)
		}
		decoded, err := Decode(raw)
		if err != nil {
			t.Fatal(err)
		}
		for _, row := range decoded.Values {
			if strings.HasPrefix(row.AttributeKey, "k") {
				retained++
			}
		}
	}
	if retained != 100 {
		t.Fatalf("dropped eligible values: %d", retained)
	}
	values["k000"] = strings.Repeat("x", MaxStringValueBytes+1)
	b, report, err = Extract(span, DefaultLimits())
	if err != nil || report.Complete {
		t.Fatalf("oversize diagnostic: %v %+v", err, report)
	}
	var foundKey, foundLater, foundTooLarge bool
	for _, row := range b.Keys {
		foundKey = foundKey || row.AttributeKey == "k000"
	}
	for _, row := range b.Values {
		foundLater = foundLater || row.AttributeKey == "k099"
		foundTooLarge = foundTooLarge || row.AttributeKey == "k000"
	}
	if !foundKey || !foundLater || foundTooLarge {
		t.Fatal("oversized value hid key or later values")
	}
}

func TestMergeIsCollisionSafeAndMinMaxIdempotent(t *testing.T) {
	b := testBatch(t)
	first := b.Values[0]
	second := first
	second.ValueJSON = `"different"`
	second.ValueSearchTextFolded = "different" // force a fingerprint collision at the identity seam
	a := Batch{Values: []ValueRow{first, second}}
	older := first
	older.FirstSeen = "2020-01-01 00:00:00.000000"
	older.LastSeen = "2030-01-01 00:00:00.000000"
	x := Merge(a, Batch{Values: []ValueRow{older}}, a)
	y := Merge(Batch{Values: []ValueRow{older}}, a)
	if !reflect.DeepEqual(x, y) || len(x.Values) != 2 {
		t.Fatal("not collision-safe/idempotent")
	}
	for _, v := range x.Values {
		if v.ValueJSON == first.ValueJSON && (v.FirstSeen != older.FirstSeen || v.LastSeen != older.LastSeen) {
			t.Fatal("min/max changed")
		}
	}
	if err := a.Validate(); err == nil {
		t.Fatal("invalid supplied fingerprint not rejected at edge")
	}
}

func TestWireRejectsLegacyPoisonAndTamper(t *testing.T) {
	b := testBatch(t)
	raw, err := Encode(b)
	if err != nil {
		t.Fatal(err)
	}
	for _, bad := range [][]byte{[]byte(`{"format":"futureagi.property-catalog-envelope","version":1}`), append(append([]byte{}, raw...), []byte(` {}`)...), []byte(strings.Replace(string(raw), "hello", "tamper", 1))} {
		if _, err := Decode(bad); err == nil {
			t.Fatal("accepted poison")
		}
	}
	for _, bad := range []string{"bool", "unknown"} {
		b.Keys[0].AttributeType = bad
		if err := b.Validate(); err == nil {
			t.Fatal("accepted invalid type")
		}
	}
}

type publisherFunc func(context.Context, Batch) error

func (f publisherFunc) Publish(ctx context.Context, b Batch) error { return f(ctx, b) }

func TestSpoolAcknowledgementRestartAndPartialPublication(t *testing.T) {
	cfg := SpoolConfig{Directory: t.TempDir(), MaxFiles: 10, MaxBytes: 1 << 20}
	w, err := NewWriter(cfg, DefaultLimits())
	if err != nil {
		t.Fatal(err)
	}
	b := testBatch(t)
	if err = w.Enqueue(context.Background(), b); err != nil {
		t.Fatal(err)
	}
	if _, err = NewWriter(cfg, DefaultLimits()); err == nil {
		t.Fatal("two spool owners")
	}
	fail := publisherFunc(func(context.Context, Batch) error { return errors.New("broker failed") })
	if _, err = w.Replay(context.Background(), fail); err == nil || len(w.files) != 1 {
		t.Fatal("lost failed publication")
	}
	w.Close()
	w, err = NewWriter(cfg, DefaultLimits())
	if err != nil {
		t.Fatal(err)
	}
	defer w.Close()
	var received Batch
	n, err := w.Replay(context.Background(), publisherFunc(func(_ context.Context, b Batch) error { received = Merge(received, b); return nil }))
	if err != nil || n != 1 || len(w.files) != 0 || !reflect.DeepEqual(Merge(b), received) {
		t.Fatalf("restart replay %d %v", n, err)
	}
}

func TestSpoolBoundsCorruptionAndFsyncFailure(t *testing.T) {
	cfg := SpoolConfig{Directory: t.TempDir(), MaxFiles: 1, MaxBytes: MaxRecordBytes}
	w, err := NewWriter(cfg, DefaultLimits())
	if err != nil {
		t.Fatal(err)
	}
	defer w.Close()
	b := testBatch(t)
	w.syncDir = func(string) error { return errors.New("fsync failure") }
	if err = w.Enqueue(context.Background(), b); err == nil || len(w.files) != 1 {
		t.Fatal("directory fsync not reflected")
	}
	w.syncDir = syncDirectory
	b.Keys[0].LastSeen = "2026-09-09 00:00:00.000000"
	if err = w.Enqueue(context.Background(), b); err == nil || len(w.files) != 1 {
		t.Fatal("capacity not preserved")
	}
	for name := range w.files {
		if err = os.WriteFile(filepath.Join(cfg.Directory, name), []byte(`{}`), 0600); err != nil {
			t.Fatal(err)
		}
	}
	called := false
	if _, err = w.Replay(context.Background(), publisherFunc(func(context.Context, Batch) error { called = true; return nil })); err == nil || called || len(w.files) != 1 {
		t.Fatal("corrupt record was skipped")
	}
}

type sourceStub struct {
	records   []*kgo.Record
	commits   int
	allowed   int
	commitErr error
}

func (s *sourceStub) PollRecords(_ context.Context, limit int) kgo.Fetches {
	return kgo.Fetches{{Topics: []kgo.FetchTopic{{Topic: DefaultTopic, Partitions: []kgo.FetchPartition{{Partition: 0, Records: s.records[:min(limit, len(s.records))]}}}}}}
}
func (s *sourceStub) CommitRecords(ctx context.Context, _ ...*kgo.Record) error {
	s.commits++
	if err := ctx.Err(); err != nil {
		return err
	}
	return s.commitErr
}
func (s *sourceStub) AllowRebalance()         { s.allowed++ }
func (s *sourceStub) CloseAllowingRebalance() {}

type sinkFunc func(context.Context, Batch) error

func (f sinkFunc) Insert(ctx context.Context, b Batch) error { return f(ctx, b) }

func TestConsumerNeverCommitsPoisonOrPartialWrites(t *testing.T) {
	raw, err := Encode(testBatch(t))
	if err != nil {
		t.Fatal(err)
	}
	for _, mode := range []string{"poison", "insert-failure", "commit-failure", "success"} {
		t.Run(mode, func(t *testing.T) {
			source := &sourceStub{records: []*kgo.Record{{Topic: DefaultTopic, Partition: 0, Offset: 4, Value: raw}}}
			if mode == "poison" {
				source.records[0].Value = []byte(`bad`)
			}
			if mode == "commit-failure" {
				source.commitErr = errors.New("commit response lost")
			}
			writes := 0
			consumer := Consumer{source: source, cfg: KafkaConfig{Topic: DefaultTopic, Timeout: time.Second}, sink: sinkFunc(func(context.Context, Batch) error {
				writes++
				if mode == "insert-failure" {
					return errors.New("partial write")
				}
				return nil
			})}
			err := consumer.processOnce(context.Background())
			if source.allowed != 1 {
				t.Fatal("rebalance not released")
			}
			if mode == "success" {
				if err != nil || source.commits != 1 || writes != 1 {
					t.Fatal("success not committed")
				}
			} else if err == nil {
				t.Fatal("failed record accepted")
			}
			if mode == "poison" && (source.commits != 0 || writes != 0) {
				t.Fatal("poison was written or skipped")
			}
			if mode == "insert-failure" && source.commits != 0 {
				t.Fatal("committed partial writes")
			}
		})
	}
}

func TestConsumerSlowSuccessfulSinkGetsIndependentCommitBudget(t *testing.T) {
	raw, err := Encode(testBatch(t))
	if err != nil {
		t.Fatal(err)
	}
	source := &sourceStub{records: []*kgo.Record{{Topic: DefaultTopic, Partition: 0, Offset: 4, Value: raw}}}
	consumer := Consumer{source: source, cfg: KafkaConfig{Topic: DefaultTopic, Timeout: 20 * time.Millisecond}, sink: sinkFunc(func(ctx context.Context, _ Batch) error {
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(40 * time.Millisecond):
			return nil
		}
	})}
	if err := consumer.processOnce(context.Background()); err != nil || source.commits != 1 || source.allowed != 1 {
		t.Fatal("healthy sink starved the independent commit budget", err)
	}
}

func TestClickHouseKeysThenValuesAndAmbiguousFailure(t *testing.T) {
	var queries []string
	failValues := true
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		q := r.URL.Query()
		queries = append(queries, q.Get("query"))
		if q.Get("async_insert") != "0" || q.Get("wait_end_of_query") != "1" || q.Get("insert_quorum") != "auto" {
			t.Error("unsafe acknowledgement settings")
		}
		quorumMillis, err := strconv.Atoi(q.Get("insert_quorum_timeout"))
		if err != nil || quorumMillis <= 0 || quorumMillis >= 1000 {
			t.Error("quorum timeout not inside client deadline")
		}
		body, _ := io.ReadAll(r.Body)
		if strings.Contains(string(body), "catalog_epoch") {
			t.Error("legacy columns")
		}
		if strings.Contains(q.Get("query"), ValueTable) && failValues {
			w.WriteHeader(200)
			w.Write([]byte("Code: 1. late insert failure"))
		}
	}))
	defer server.Close()
	sink, err := NewClickHouseSink(ClickHouseConfig{URL: server.URL, Database: "catalog", Timeout: time.Second})
	if err != nil {
		t.Fatal(err)
	}
	b := testBatch(t)
	if err = sink.Insert(context.Background(), b); err == nil {
		t.Fatal("late exception acknowledged")
	}
	failValues = false
	if err = sink.Insert(context.Background(), b); err != nil {
		t.Fatal(err)
	}
	if len(queries) != 4 || !strings.Contains(queries[0], KeyTable) || !strings.Contains(queries[1], ValueTable) {
		t.Fatal(queries)
	}
}

func TestCustomKafkaNamesAndConfigurationValidation(t *testing.T) {
	c, err := (KafkaConfig{Brokers: []string{"localhost:9092"}, Topic: "tenant-custom-observed", Group: "custom-group"}).normalized()
	if err != nil || c.Topic != "tenant-custom-observed" || c.Group != "custom-group" {
		t.Fatal("custom names rejected")
	}
	for _, topic := range []string{"..", "invalid topic", "x/y"} {
		if _, err := (KafkaConfig{Brokers: c.Brokers, Topic: topic}).normalized(); err == nil {
			t.Fatal("bad topic accepted")
		}
	}
	getenv := func(key string) string {
		return map[string]string{"FI_OBSERVED_CATALOG_MODE": "kafka", "FI_OBSERVED_CATALOG_KAFKA_BROKERS": "localhost:9092", "FI_OBSERVED_CATALOG_KAFKA_TOPIC": "mine", "FI_OBSERVED_CATALOG_KAFKA_GROUP": "my-group"}[key]
	}
	r, err := RuntimeFromEnv(RuntimeConfig{}, getenv)
	if err != nil || r.Kafka.Topic != "mine" {
		t.Fatal(err)
	}
}

func TestSharedEnvironmentLimitsAndScopeEdges(t *testing.T) {
	env := map[string]string{
		"FI_OBSERVED_CATALOG_MODE": "kafka", "FI_OBSERVED_CATALOG_KAFKA_BROKERS": "localhost:9092",
		"FI_OBSERVED_CATALOG_MAX_KEYS_PER_SPAN": "256", "FI_OBSERVED_CATALOG_MAX_ARRAY_MEMBERS_PER_SPAN": "512",
	}
	getenv := func(key string) string { return env[key] }
	limits, err := LimitsFromEnv(getenv)
	if err != nil {
		t.Fatal(err)
	}
	runtime, err := RuntimeFromEnv(RuntimeConfig{}, getenv)
	if err != nil || runtime.Limits != limits || limits != (Limits{256, 512}) {
		t.Fatal("runtime and backfill limits differ", err)
	}
	for _, bad := range []string{"0", "-1", "bad", "4097"} {
		env["FI_OBSERVED_CATALOG_MAX_KEYS_PER_SPAN"] = bad
		if _, err := LimitsFromEnv(getenv); err == nil {
			t.Fatal("bad shared limit accepted", bad)
		}
	}
	span := testSpan()
	delete(span.Row, "org_id")
	if _, _, err := Extract(span, limits); err != nil {
		t.Fatal("proven scope with absent source org rejected", err)
	}
	for _, bad := range []any{true, 123, "foreign"} {
		span.Row["org_id"] = bad
		if _, _, err := Extract(span, limits); err == nil {
			t.Fatal("unproven source org accepted")
		}
	}
	span.Row["org_id"] = nil
	span.Row["project_id"] = "not-a-uuid"
	if _, _, err := Extract(span, limits); err == nil {
		t.Fatal("invalid source project accepted")
	}
}

func TestSpoolDiscardsOnlyInterruptedTemporaryFiles(t *testing.T) {
	directory := filepath.Join(t.TempDir(), "new", "nested", "spool")
	w, err := NewWriter(SpoolConfig{Directory: directory}, DefaultLimits())
	if err != nil {
		t.Fatal(err)
	}
	w.Close()
	for _, name := range []string{".observed-tmp-interrupted", "unrelated.txt"} {
		if err := os.WriteFile(filepath.Join(directory, name), []byte("incomplete"), 0600); err != nil {
			t.Fatal(err)
		}
	}
	w, err = NewWriter(SpoolConfig{Directory: directory}, DefaultLimits())
	if err != nil {
		t.Fatal(err)
	}
	defer w.Close()
	if _, err := os.Stat(filepath.Join(directory, ".observed-tmp-interrupted")); !errors.Is(err, os.ErrNotExist) {
		t.Fatal("interrupted temporary retained", err)
	}
	if _, err := os.Stat(filepath.Join(directory, "unrelated.txt")); err != nil {
		t.Fatal("unrelated file removed", err)
	}
}

func TestSpoolFailedPublicationDoesNotBlockConcurrentHandoff(t *testing.T) {
	w, err := NewWriter(SpoolConfig{Directory: t.TempDir()}, DefaultLimits())
	if err != nil {
		t.Fatal(err)
	}
	defer w.Close()
	b := testBatch(t)
	if err := w.Enqueue(context.Background(), b); err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	started, replayDone := make(chan struct{}), make(chan error, 1)
	go func() {
		_, err := w.Replay(ctx, publisherFunc(func(ctx context.Context, _ Batch) error {
			close(started)
			<-ctx.Done()
			return ctx.Err()
		}))
		replayDone <- err
	}()
	<-started
	b.Keys[0].LastSeen = "2026-09-09 00:00:00.000000"
	enqueueDone := make(chan error, 1)
	go func() { enqueueDone <- w.Enqueue(context.Background(), b) }()
	select {
	case err := <-enqueueDone:
		if err != nil {
			t.Fatal(err)
		}
	case <-time.After(time.Second):
		t.Fatal("network publication holds synchronous handoff lock")
	}
	cancel()
	if err := <-replayDone; err == nil {
		t.Fatal("publication cancellation lost")
	}
	if len(w.files) != 2 {
		t.Fatal("failed publication or concurrent handoff lost")
	}
}
