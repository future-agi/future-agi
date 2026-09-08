package catalogwriter

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/attributecatalog"
)

const testProjectID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"

type insertCall struct {
	table Table
	rows  []map[string]any
}

type recordingInserter struct {
	mu        sync.Mutex
	calls     []insertCall
	failCount int
}

type recordingProgressSink struct {
	mu        sync.Mutex
	records   []ProgressRecord
	failCount int
}

func (s *recordingProgressSink) AcknowledgeCatalogProgress(
	_ context.Context, record ProgressRecord,
) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.records = append(s.records, record)
	if s.failCount > 0 {
		s.failCount--
		return errors.New("injected progress acknowledgement failure")
	}
	return nil
}

func (s *recordingProgressSink) snapshot() []ProgressRecord {
	s.mu.Lock()
	defer s.mu.Unlock()
	return append([]ProgressRecord(nil), s.records...)
}

type blockingInserter struct {
	started chan struct{}
	release chan struct{}
	once    sync.Once
	mu      sync.Mutex
	calls   int
}

func newBlockingInserter() *blockingInserter {
	return &blockingInserter{started: make(chan struct{}), release: make(chan struct{})}
}

func (b *blockingInserter) InsertCatalog(ctx context.Context, _ Table, _ []map[string]any) error {
	b.mu.Lock()
	b.calls++
	b.mu.Unlock()
	b.once.Do(func() { close(b.started) })
	select {
	case <-b.release:
		return nil
	case <-ctx.Done():
		return ctx.Err()
	}
}

func (b *blockingInserter) callCount() int {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.calls
}

func (r *recordingInserter) InsertCatalog(_ context.Context, table Table, rows []map[string]any) error {
	r.mu.Lock()
	defer r.mu.Unlock()
	copied := make([]map[string]any, len(rows))
	for i, row := range rows {
		copied[i] = make(map[string]any, len(row))
		for key, value := range row {
			copied[i][key] = value
		}
	}
	r.calls = append(r.calls, insertCall{table: table, rows: copied})
	if r.failCount > 0 {
		r.failCount--
		return errors.New("injected catalog insert failure")
	}
	return nil
}

func enabledConfig(dir string) Config {
	cfg := DefaultConfig()
	cfg.Enabled = true
	cfg.SpoolDir = dir
	cfg.ProgressSink = &recordingProgressSink{}
	return cfg
}

func canonicalSpan(seen string, attrs map[string]string) map[string]any {
	return map[string]any{
		"project_id": testProjectID, "start_time": seen, "attrs_string": attrs,
		"attrs_number": map[string]float64{}, "attrs_bool": map[string]uint8{},
		"attributes_extra": map[string]any{},
	}
}

func keyOnlySpan(seen, key string) map[string]any {
	row := canonicalSpan(seen, map[string]string{})
	row["attributes_extra"] = map[string]any{key: map[string]any{"nested": true}}
	return row
}

func TestZeroConfigIsDisabled(t *testing.T) {
	inserter := &recordingInserter{}
	w, err := New(Config{}, inserter)
	if err != nil {
		t.Fatal(err)
	}
	job, report := w.StageCanonicalSpans([]map[string]any{
		canonicalSpan("2026-08-13 12:00:00.000001", map[string]string{"model": "gpt"}),
	})
	if w.Enabled() || !job.Empty() || report.InputSpans != 1 || report.AcceptedSpans != 0 {
		t.Fatalf("zero Config performed work: job=%+v report=%+v", job, report)
	}
	if err := w.Submit(context.Background(), Job{}); err != nil || len(inserter.calls) != 0 {
		t.Fatalf("disabled Submit err=%v calls=%d", err, len(inserter.calls))
	}
}

func TestRestartRemovesOnlyOwnedRegularStaleTemps(t *testing.T) {
	dir := t.TempDir()
	stale := filepath.Join(dir, spoolTempPrefix+"123456789")
	unrelated := filepath.Join(dir, spoolTempPrefix+"operator-note")
	tooLong := filepath.Join(dir, spoolTempPrefix+"12345678901")
	overUint32 := filepath.Join(dir, spoolTempPrefix+"4294967296")
	target := filepath.Join(dir, "symlink-target")
	symlink := filepath.Join(dir, spoolTempPrefix+"987654321")
	directory := filepath.Join(dir, spoolTempPrefix+"123")
	for path, contents := range map[string]string{
		stale: "partial envelope", unrelated: "keep me", tooLong: "keep me too",
		overUint32: "keep me three", target: "target",
	} {
		if err := os.WriteFile(path, []byte(contents), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	if err := os.Symlink(target, symlink); err != nil {
		t.Fatal(err)
	}
	if err := os.Mkdir(directory, 0o700); err != nil {
		t.Fatal(err)
	}

	if _, err := New(enabledConfig(dir), &recordingInserter{}); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Lstat(stale); !errors.Is(err, os.ErrNotExist) {
		t.Fatalf("owned stale regular temp remains: %v", err)
	}
	for _, path := range []string{unrelated, tooLong, overUint32, target, symlink, directory} {
		if _, err := os.Lstat(path); err != nil {
			t.Fatalf("startup cleanup touched %s: %v", filepath.Base(path), err)
		}
	}
	info, err := os.Lstat(symlink)
	if err != nil || info.Mode()&os.ModeSymlink == 0 {
		t.Fatalf("matching symlink was followed or removed: info=%v err=%v", info, err)
	}
}

func TestStageCopiesCompactRowsAndDedupesMinMax(t *testing.T) {
	w, err := New(enabledConfig(t.TempDir()), &recordingInserter{})
	if err != nil {
		t.Fatal(err)
	}
	marker := "FAT_PAYLOAD_MUST_NOT_SURVIVE"
	fat := strings.Repeat("v", 1<<20) + marker
	later := canonicalSpan("2026-08-13 12:00:00.900001", map[string]string{"User.Name": "Alice"})
	later["input"], later["output"] = fat, fat
	later["attributes_extra"] = map[string]any{"voice.payload": map[string]any{"transcript": fat}}
	earlier := canonicalSpan("2026-01-02 03:04:05.000006", map[string]string{"User.Name": "Alice"})
	earlier["attributes_extra"] = map[string]any{"voice.payload": map[string]any{"transcript": fat}}

	job, report := w.StageCanonicalSpans([]map[string]any{later, earlier})
	if report.AcceptedSpans != 2 || report.RejectedSpans != 0 ||
		len(job.keyRows) != 2 || len(job.valueRows) != 1 || report.DuplicateRows != 3 {
		t.Fatalf("stage job=%+v report=%+v", job, report)
	}
	var gotKey keyRow
	for _, row := range job.keyRows {
		if row.AttributeKey == "User.Name" {
			gotKey = row
		}
	}
	wantKey := keyRow{
		ProjectID: testProjectID, SourceKind: "custom_attribute", AttributeKey: "User.Name", KeyFolded: "user.name",
		AttributeType: "string", FirstSeen: "2026-01-02 03:04:05.000006",
		LastSeen: "2026-08-13 12:00:00.900001", CatalogEpoch: 1,
	}
	if gotKey != wantKey {
		t.Fatalf("key row=%#v want %#v", gotKey, wantKey)
	}
	encoded, err := attributecatalog.EncodeScalar("Alice")
	if err != nil {
		t.Fatal(err)
	}
	value := job.valueRows[0]
	if value.AttributeKey != "User.Name" || value.ValueJSON != `"Alice"` ||
		value.ValueSearchText != "Alice" || value.ValueFingerprint != encoded.Fingerprint ||
		value.FirstSeen != wantKey.FirstSeen || value.LastSeen != wantKey.LastSeen {
		t.Fatalf("value row=%#v", value)
	}
	compact, err := json.Marshal(diskJob{
		KeyRows: job.keyRows, ValueRows: job.valueRows,
		EncodedBytes: job.encodedBytes, Metadata: job.Metadata(),
	})
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(compact), marker) || len(compact) > 8<<10 {
		t.Fatalf("compact job retained fat data: %d bytes", len(compact))
	}
}

func TestStageValidatesCanonicalFieldsAndGlobalLimits(t *testing.T) {
	cfg := enabledConfig(t.TempDir())
	cfg.MaxJobRows = 2
	w, err := New(cfg, &recordingInserter{})
	if err != nil {
		t.Fatal(err)
	}
	badProject := canonicalSpan("2026-08-13 12:00:00.000001", map[string]string{"bad": "project"})
	badProject["project_id"] = strings.ToUpper(testProjectID)
	badTime := canonicalSpan("2026-08-13T12:00:00Z", map[string]string{"bad": "time"})
	badMap := canonicalSpan("2026-08-13 12:00:00.000001", map[string]string{"bad": "map"})
	badMap["attrs_number"] = map[string]any{"score": 1.0}
	valid := canonicalSpan("2026-08-13 12:00:00.000001", map[string]string{"z": "last", "a": "first"})
	job, report := w.StageCanonicalSpans([]map[string]any{badProject, badTime, badMap, valid})
	if report.AcceptedSpans != 1 || report.RejectedSpans != 3 || job.RowCount() != 2 ||
		len(job.keyRows) != 2 || len(job.valueRows) != 0 || report.RowsOmitted != 2 || !report.GlobalTruncated {
		t.Fatalf("validation/bounds job=%+v report=%+v", job, report)
	}
	if job.keyRows[0].AttributeKey != "a" || job.keyRows[1].AttributeKey != "z" {
		t.Fatalf("keys not deterministic: %+v", job.keyRows)
	}

	byteCfg := enabledConfig(t.TempDir())
	byteCfg.MaxJobEncodedBytes = 1
	byteWriter, err := New(byteCfg, &recordingInserter{})
	if err != nil {
		t.Fatal(err)
	}
	byteJob, byteReport := byteWriter.StageCanonicalSpans([]map[string]any{valid})
	if !byteJob.Empty() || byteJob.EncodedBytes() > 1 || byteReport.RowsOmitted != 4 || !byteReport.GlobalTruncated {
		t.Fatalf("byte bound job=%+v report=%+v", byteJob, byteReport)
	}
}

func TestStageNeverRetainsValueWhenWriterLimitOmitsItsKey(t *testing.T) {
	attributeKey := strings.Repeat("k", 1_024)
	seen := "2026-08-13 12:00:00.000001"
	keySize, err := wireSize(keyRow{
		ProjectID: testProjectID, AttributeKey: attributeKey, KeyFolded: attributeKey,
		AttributeType: "string", FirstSeen: seen, LastSeen: seen, CatalogEpoch: 1,
	})
	if err != nil {
		t.Fatal(err)
	}
	encoded, err := attributecatalog.EncodeScalar("v")
	if err != nil {
		t.Fatal(err)
	}
	valueSize, err := wireSize(valueRow{
		ProjectID: testProjectID, AttributeKey: attributeKey, AttributeType: "string",
		ValueFingerprint: encoded.Fingerprint, ValueJSON: encoded.ValueJSON,
		ValueSearchText: encoded.SearchText, FirstSeen: seen, LastSeen: seen, CatalogEpoch: 1,
	})
	if err != nil {
		t.Fatal(err)
	}
	if keySize <= valueSize {
		t.Fatalf("test requires key wire row (%d) larger than value row (%d)", keySize, valueSize)
	}

	cfg := enabledConfig(t.TempDir())
	cfg.MaxChunkEncodedBytes = valueSize
	writer, err := New(cfg, &recordingInserter{})
	if err != nil {
		t.Fatal(err)
	}
	job, report := writer.StageCanonicalSpans([]map[string]any{
		canonicalSpan(seen, map[string]string{attributeKey: "v"}),
	})
	if !job.Empty() || report.RowsOmitted != 2 || !report.GlobalTruncated {
		t.Fatalf("orphan value survived omitted key: job=%+v report=%+v", job, report)
	}
}

func TestSubmitOnlySpoolsAndRestartReplayIsKeyThenValue(t *testing.T) {
	dir := t.TempDir()
	inserter := &recordingInserter{}
	cfg := enabledConfig(dir)
	cfg.MaxChunkRows = 1
	w, err := New(cfg, inserter)
	if err != nil {
		t.Fatal(err)
	}
	job, _ := w.StageCanonicalSpans([]map[string]any{
		canonicalSpan("2026-08-13 12:00:00.000001", map[string]string{"z": "last", "a": "first"}),
	})
	if err := w.Submit(context.Background(), job); err != nil {
		t.Fatal(err)
	}
	if len(inserter.calls) != 0 {
		t.Fatal("Submit performed a network insert")
	}
	entries, err := os.ReadDir(dir)
	if err != nil {
		t.Fatal(err)
	}
	if len(entries) != 1 || !strings.HasPrefix(entries[0].Name(), spoolPrefix) ||
		strings.HasPrefix(entries[0].Name(), ".catalog-tmp-") {
		t.Fatalf("spool is not one atomically published envelope: %+v", entries)
	}
	pending, err := w.Pending()
	if err != nil || len(pending) != 1 || pending[0].Rows != 4 || pending[0].EncodedBytes != job.EncodedBytes() {
		t.Fatalf("pending=%+v err=%v", pending, err)
	}

	restarted, err := New(cfg, inserter)
	if err != nil {
		t.Fatal(err)
	}
	result, err := restarted.Replay(context.Background())
	if err != nil || result.Attempted != 1 || result.Delivered != 1 {
		t.Fatalf("replay=%+v err=%v", result, err)
	}
	if len(inserter.calls) != 4 {
		t.Fatalf("calls=%d want four one-row chunks", len(inserter.calls))
	}
	wantTables := []Table{KeyTable, KeyTable, ValueTable, ValueTable}
	wantKeys := []string{"a", "z", "a", "z"}
	for i := range wantTables {
		call := inserter.calls[i]
		if call.table != wantTables[i] || len(call.rows) != 1 || call.rows[0]["attribute_key"] != wantKeys[i] {
			t.Fatalf("call %d=%+v want table=%s key=%s", i, call, wantTables[i], wantKeys[i])
		}
	}
	if got := inserter.calls[0].rows[0]["first_seen"]; got != "2026-08-13 12:00:00.000001" {
		t.Fatalf("exact timestamp=%v", got)
	}
	pending, err = restarted.Pending()
	if err != nil || len(pending) != 0 {
		t.Fatalf("delivered envelope remains: pending=%+v err=%v", pending, err)
	}
}

func TestReplayFailureRetainsEnvelopeThenRetries(t *testing.T) {
	dir := t.TempDir()
	inserter := &recordingInserter{failCount: 1}
	w, err := New(enabledConfig(dir), inserter)
	if err != nil {
		t.Fatal(err)
	}
	job, _ := w.StageCanonicalSpans([]map[string]any{
		canonicalSpan("2026-08-13 12:00:00.000001", map[string]string{"model": "gpt"}),
	})
	if err := w.Submit(context.Background(), job); err != nil {
		t.Fatal(err)
	}
	first, err := w.Replay(context.Background())
	if err == nil || first.Attempted != 1 || first.Delivered != 0 {
		t.Fatalf("first replay=%+v err=%v", first, err)
	}
	pending, pendingErr := w.Pending()
	if pendingErr != nil || len(pending) != 1 {
		t.Fatalf("failed envelope not retained: pending=%+v err=%v", pending, pendingErr)
	}
	second, err := w.Replay(context.Background())
	if err != nil || second.Attempted != 1 || second.Delivered != 1 {
		t.Fatalf("second replay=%+v err=%v", second, err)
	}
}

func TestSpoolCeilingPreservesExistingEnvelope(t *testing.T) {
	dir := t.TempDir()
	cfg := enabledConfig(dir)
	w, err := New(cfg, &recordingInserter{})
	if err != nil {
		t.Fatal(err)
	}
	job, _ := w.StageCanonicalSpans([]map[string]any{
		canonicalSpan("2026-08-13 12:00:00.000001", map[string]string{"model": "gpt"}),
	})
	if err := w.Submit(context.Background(), job); err != nil {
		t.Fatal(err)
	}
	files, err := w.spool.enumerate(cfg.MaxSpoolFiles)
	if err != nil || len(files) != 1 {
		t.Fatalf("files=%+v err=%v", files, err)
	}
	limitedCfg := cfg
	limitedCfg.MaxSpoolBytes = files[0].size + 1
	limited, err := New(limitedCfg, &recordingInserter{})
	if err != nil {
		t.Fatal(err)
	}
	if err := limited.Submit(context.Background(), job); err == nil || !strings.Contains(err.Error(), "spool byte limit") {
		t.Fatalf("second submit error=%v", err)
	}
	after, err := limited.spool.enumerate(cfg.MaxSpoolFiles)
	if err != nil || len(after) != 1 || after[0].name != files[0].name {
		t.Fatalf("existing spool changed: before=%+v after=%+v err=%v", files, after, err)
	}
}

func TestReplayRejectsTamperAndTrailingJSONWithoutDeleting(t *testing.T) {
	for _, test := range []struct {
		name   string
		mutate func(string) string
		want   string
	}{
		{
			name: "checksum tamper",
			mutate: func(raw string) string {
				return strings.Replace(raw, `"attribute_key":"model"`, `"attribute_key":"other"`, 1)
			},
			want: "checksum mismatch",
		},
		{
			name:   "trailing JSON token",
			mutate: func(raw string) string { return raw + "{}\n" },
			want:   "unexpected trailing JSON value",
		},
	} {
		t.Run(test.name, func(t *testing.T) {
			dir := t.TempDir()
			w, err := New(enabledConfig(dir), &recordingInserter{})
			if err != nil {
				t.Fatal(err)
			}
			job, _ := w.StageCanonicalSpans([]map[string]any{
				canonicalSpan("2026-08-13 12:00:00.000001", map[string]string{"model": "gpt"}),
			})
			if err := w.Submit(context.Background(), job); err != nil {
				t.Fatal(err)
			}
			files, err := w.spool.enumerate(w.cfg.MaxSpoolFiles)
			if err != nil || len(files) != 1 {
				t.Fatalf("files=%+v err=%v", files, err)
			}
			raw, err := os.ReadFile(files[0].path)
			if err != nil {
				t.Fatal(err)
			}
			mutated := test.mutate(string(raw))
			if mutated == string(raw) {
				t.Fatal("test mutation made no change")
			}
			if err := os.WriteFile(files[0].path, []byte(mutated), 0o600); err != nil {
				t.Fatal(err)
			}
			result, err := w.Replay(context.Background())
			if err == nil || !strings.Contains(err.Error(), test.want) || result.Attempted != 1 {
				t.Fatalf("replay=%+v err=%v", result, err)
			}
			remaining, listErr := w.spool.enumerate(w.cfg.MaxSpoolFiles)
			if listErr != nil || len(remaining) != 1 || remaining[0].name != files[0].name {
				t.Fatalf("corrupt envelope was not preserved: %+v err=%v", remaining, listErr)
			}
		})
	}
}

func TestInsertRowsUseOnlyCatalogSchemaColumns(t *testing.T) {
	key := keyRowMap(keyRow{SourceKind: attributecatalog.SourceKindCustomAttribute})
	value := valueRowMap(valueRow{SourceKind: attributecatalog.SourceKindCustomAttribute})
	for _, column := range []string{
		"project_id", "source_kind", "attribute_key", "key_folded", "attribute_type",
		"first_seen", "last_seen", "catalog_epoch",
	} {
		if _, ok := key[column]; !ok {
			t.Errorf("key row missing %s", column)
		}
	}
	if len(key) != 8 {
		t.Errorf("key row has extra columns: %v", key)
	}
	for _, column := range []string{
		"project_id", "source_kind", "attribute_key", "attribute_type", "value_fingerprint",
		"value_json", "value_search_text", "first_seen", "last_seen", "catalog_epoch",
	} {
		if _, ok := value[column]; !ok {
			t.Errorf("value row missing %s", column)
		}
	}
	if len(value) != 10 {
		t.Errorf("value row has extra columns: %v", value)
	}
}

func TestLegacySpoolRowsReplayWithoutChangingChecksumOrEncodedBytes(t *testing.T) {
	dir := t.TempDir()
	writer, err := New(enabledConfig(dir), &recordingInserter{})
	if err != nil {
		t.Fatal(err)
	}
	job, _ := writer.StageCanonicalSpans([]map[string]any{
		canonicalSpan("2026-08-13 12:00:00.000001", map[string]string{"legacy": "value"}),
	})
	for index := range job.keyRows {
		job.keyRows[index].SourceKind = ""
	}
	for index := range job.valueRows {
		job.valueRows[index].SourceKind = ""
	}
	job.encodedBytes = 0
	for _, row := range job.keyRows {
		size, sizeErr := wireSize(row)
		if sizeErr != nil {
			t.Fatal(sizeErr)
		}
		job.encodedBytes += size
	}
	for _, row := range job.valueRows {
		size, sizeErr := wireSize(row)
		if sizeErr != nil {
			t.Fatal(sizeErr)
		}
		job.encodedBytes += size
	}
	job.metadata.EncodedBytes = job.encodedBytes
	if err := writer.Submit(context.Background(), job); err != nil {
		t.Fatal(err)
	}

	restartedInserter := &recordingInserter{}
	restarted, err := New(enabledConfig(dir), restartedInserter)
	if err != nil {
		t.Fatal(err)
	}
	result, err := restarted.Replay(context.Background())
	if err != nil || result.Delivered != 1 {
		t.Fatalf("legacy restart replay=%+v err=%v", result, err)
	}
	if len(restartedInserter.calls) != 2 {
		t.Fatalf("legacy rows were not delivered: %+v", restartedInserter.calls)
	}
	for _, call := range restartedInserter.calls {
		for _, row := range call.rows {
			if _, exists := row["source_kind"]; exists {
				t.Fatalf("legacy replay rewrote durable row shape: %v", row)
			}
		}
	}
}

func TestStageCanonicalModelUsesCollisionFreeSystemNamespace(t *testing.T) {
	w, err := New(enabledConfig(t.TempDir()), &recordingInserter{})
	if err != nil {
		t.Fatal(err)
	}
	span := canonicalSpan(
		"2026-08-13 12:00:00.000001",
		map[string]string{"model": "customer-defined-model"},
	)
	span["model"] = "gpt-4.1"

	job, report := w.StageCanonicalSpans([]map[string]any{span})
	if report.RejectedSpans != 0 || report.IncompleteSpans != 0 {
		t.Fatalf("unexpected staging gap: %+v", report)
	}
	gotKeys := map[string]string{}
	for _, row := range job.keyRows {
		if row.AttributeKey == "model" {
			gotKeys[row.SourceKind] = row.AttributeType
		}
	}
	if !reflect.DeepEqual(gotKeys, map[string]string{
		"custom_attribute": "string", "system_attribute": "string",
	}) {
		t.Fatalf("model namespaces=%v", gotKeys)
	}
	gotValues := map[string]string{}
	for _, row := range job.valueRows {
		if row.AttributeKey == "model" {
			gotValues[row.SourceKind] = row.ValueSearchText
		}
	}
	if !reflect.DeepEqual(gotValues, map[string]string{
		"custom_attribute": "customer-defined-model", "system_attribute": "gpt-4.1",
	}) {
		t.Fatalf("model values=%v", gotValues)
	}
	wire := ExportWireJob(job)
	for _, rows := range [][]map[string]any{wire.KeyRows, wire.ValueRows} {
		for _, row := range rows {
			if row["source_kind"] == nil {
				t.Fatalf("transport row omitted source_kind: %v", row)
			}
		}
	}
}

func TestStageCanonicalModelMatchesBackfillSuggestionByteBoundary(t *testing.T) {
	w, err := New(enabledConfig(t.TempDir()), &recordingInserter{})
	if err != nil {
		t.Fatal(err)
	}
	tests := []struct {
		name       string
		model      string
		wantValues int
		incomplete int
	}{
		{name: "exact-16-kib", model: strings.Repeat("x", 16<<10), wantValues: 1},
		{name: "16-kib-plus-one", model: strings.Repeat("x", (16<<10)+1), incomplete: 1},
		{name: "utf8-bytes-plus-one", model: strings.Repeat("é", (8<<10)+1), incomplete: 1},
		{name: "authoritative-nil-uuid-sentinel", model: catalogSystemNilUUIDValue},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			span := canonicalSpan("2026-08-13 12:00:00.000001", map[string]string{})
			span["model"] = test.model
			job, report := w.StageCanonicalSpans([]map[string]any{span})
			systemValues := 0
			for _, row := range job.valueRows {
				if row.SourceKind == attributecatalog.SourceKindSystemAttribute && row.AttributeKey == "model" {
					systemValues++
				}
			}
			if systemValues != test.wantValues || report.IncompleteSpans != test.incomplete {
				t.Fatalf("system values=%d report=%+v", systemValues, report)
			}
			if test.incomplete != 0 && !containsString(report.BuildGapReasons, "system_value_projection") {
				t.Fatalf("oversized Model lacks durable projection gap: %+v", report)
			}
		})
	}
}

func TestBlockedReplayDoesNotBlockSubmit(t *testing.T) {
	inserter := newBlockingInserter()
	w, err := New(enabledConfig(t.TempDir()), inserter)
	if err != nil {
		t.Fatal(err)
	}
	first, _ := w.StageCanonicalSpans([]map[string]any{
		keyOnlySpan("2026-08-13 12:00:00.000001", "first.map"),
	})
	second, _ := w.StageCanonicalSpans([]map[string]any{
		keyOnlySpan("2026-08-13 12:00:01.000001", "second.map"),
	})
	if err := w.Submit(context.Background(), first); err != nil {
		t.Fatal(err)
	}
	replayDone := make(chan error, 1)
	go func() {
		_, replayErr := w.Replay(context.Background())
		replayDone <- replayErr
	}()
	select {
	case <-inserter.started:
	case <-time.After(2 * time.Second):
		t.Fatal("Replay did not reach blocking inserter")
	}

	submitDone := make(chan error, 1)
	go func() { submitDone <- w.Submit(context.Background(), second) }()
	select {
	case err := <-submitDone:
		if err != nil {
			t.Fatalf("concurrent Submit: %v", err)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("Submit blocked behind Replay network call")
	}
	close(inserter.release)
	if err := <-replayDone; err != nil {
		t.Fatal(err)
	}
	pending, err := w.Pending()
	if err != nil || len(pending) != 1 {
		t.Fatalf("concurrently submitted job missing: pending=%+v err=%v", pending, err)
	}
}

func TestCanceledSubmitDoesNotWaitForAdmission(t *testing.T) {
	w, err := New(enabledConfig(t.TempDir()), &recordingInserter{})
	if err != nil {
		t.Fatal(err)
	}
	job, _ := w.StageCanonicalSpans([]map[string]any{
		keyOnlySpan("2026-08-13 12:00:00.000001", "map"),
	})
	// Simulate another short filesystem critical section owning admission.
	<-w.admission
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if err := w.Submit(ctx, job); !errors.Is(err, context.Canceled) {
		w.releaseAdmission()
		t.Fatalf("Submit error=%v want context.Canceled", err)
	}
	w.releaseAdmission()
	if w.spoolFiles != 0 || w.spoolBytes != 0 {
		t.Fatalf("canceled Submit changed accounting: files=%d bytes=%d", w.spoolFiles, w.spoolBytes)
	}
}

func TestMaxSpoolFilesAndRestartAccounting(t *testing.T) {
	dir := t.TempDir()
	cfg := enabledConfig(dir)
	cfg.MaxSpoolFiles = 1
	w, err := New(cfg, &recordingInserter{})
	if err != nil {
		t.Fatal(err)
	}
	job, _ := w.StageCanonicalSpans([]map[string]any{
		keyOnlySpan("2026-08-13 12:00:00.000001", "map"),
	})
	if err := w.Submit(context.Background(), job); err != nil {
		t.Fatal(err)
	}
	originalBytes := w.spoolBytes
	if err := w.Submit(context.Background(), job); err == nil || !strings.Contains(err.Error(), "spool file limit") {
		t.Fatalf("N+1 Submit error=%v", err)
	}
	if w.spoolFiles != 1 || w.spoolBytes != originalBytes {
		t.Fatalf("N+1 changed accounting: files=%d bytes=%d", w.spoolFiles, w.spoolBytes)
	}
	files, err := w.spool.enumerate(cfg.MaxSpoolFiles)
	if err != nil || len(files) != 1 {
		t.Fatalf("N+1 deleted existing job: files=%+v err=%v", files, err)
	}

	restarted, err := New(cfg, &recordingInserter{})
	if err != nil {
		t.Fatal(err)
	}
	if restarted.spoolFiles != 1 || restarted.spoolBytes != originalBytes {
		t.Fatalf("restart counters files=%d bytes=%d want 1/%d", restarted.spoolFiles, restarted.spoolBytes, originalBytes)
	}
	tooSmall := cfg
	tooSmall.MaxSpoolBytes = originalBytes - 1
	if _, err := New(tooSmall, &recordingInserter{}); err == nil || !strings.Contains(err.Error(), "existing spool uses") {
		t.Fatalf("restart under byte cap error=%v", err)
	}
}

func TestPublishedSaveSyncErrorIsCountedConservatively(t *testing.T) {
	dir := t.TempDir()
	cfg := enabledConfig(dir)
	cfg.MaxSpoolFiles = 1
	w, err := New(cfg, &recordingInserter{})
	if err != nil {
		t.Fatal(err)
	}
	w.spool.syncDirFn = func(string) error { return errors.New("injected directory sync failure") }
	job, _ := w.StageCanonicalSpans([]map[string]any{
		keyOnlySpan("2026-08-13 12:00:00.000001", "map"),
	})
	if err := w.Submit(context.Background(), job); err == nil || !strings.Contains(err.Error(), "sync published spool") {
		t.Fatalf("Submit sync error=%v", err)
	}
	if w.spoolFiles != 1 || w.spoolBytes <= 0 {
		t.Fatalf("published error not counted: files=%d bytes=%d", w.spoolFiles, w.spoolBytes)
	}
	files, err := w.spool.enumerate(cfg.MaxSpoolFiles)
	if err != nil || len(files) != 1 || files[0].size != w.spoolBytes {
		t.Fatalf("published file/accounting mismatch: files=%+v err=%v", files, err)
	}
	if err := w.Submit(context.Background(), job); err == nil || !strings.Contains(err.Error(), "spool file limit") {
		t.Fatalf("sync-error file bypassed cap: %v", err)
	}
}

func TestReplayRemovalFreesAdmissionCapacity(t *testing.T) {
	dir := t.TempDir()
	cfg := enabledConfig(dir)
	cfg.MaxSpoolFiles = 1
	w, err := New(cfg, &recordingInserter{})
	if err != nil {
		t.Fatal(err)
	}
	job, _ := w.StageCanonicalSpans([]map[string]any{
		keyOnlySpan("2026-08-13 12:00:00.000001", "map"),
	})
	if err := w.Submit(context.Background(), job); err != nil {
		t.Fatal(err)
	}
	if _, err := w.Replay(context.Background()); err != nil {
		t.Fatal(err)
	}
	if w.spoolFiles != 0 || w.spoolBytes != 0 {
		t.Fatalf("remove did not decrement counters: files=%d bytes=%d", w.spoolFiles, w.spoolBytes)
	}
	if err := w.Submit(context.Background(), job); err != nil {
		t.Fatalf("Submit after removal: %v", err)
	}
}

func TestConcurrentReplayDoesNotDeliverEnvelopeTwice(t *testing.T) {
	inserter := newBlockingInserter()
	w, err := New(enabledConfig(t.TempDir()), inserter)
	if err != nil {
		t.Fatal(err)
	}
	job, _ := w.StageCanonicalSpans([]map[string]any{
		keyOnlySpan("2026-08-13 12:00:00.000001", "map"),
	})
	if err := w.Submit(context.Background(), job); err != nil {
		t.Fatal(err)
	}
	type replayOutcome struct {
		result ReplayResult
		err    error
	}
	firstDone := make(chan replayOutcome, 1)
	secondDone := make(chan replayOutcome, 1)
	go func() {
		result, replayErr := w.Replay(context.Background())
		firstDone <- replayOutcome{result, replayErr}
	}()
	select {
	case <-inserter.started:
	case <-time.After(2 * time.Second):
		t.Fatal("first Replay did not reach inserter")
	}
	go func() {
		result, replayErr := w.Replay(context.Background())
		secondDone <- replayOutcome{result, replayErr}
	}()
	select {
	case outcome := <-secondDone:
		t.Fatalf("second Replay bypassed worker serialization: %+v", outcome)
	case <-time.After(100 * time.Millisecond):
	}
	close(inserter.release)
	first := <-firstDone
	second := <-secondDone
	if first.err != nil || second.err != nil ||
		first.result != (ReplayResult{Attempted: 1, Delivered: 1}) || second.result != (ReplayResult{}) {
		t.Fatalf("first=%+v second=%+v", first, second)
	}
	if inserter.callCount() != 1 {
		t.Fatalf("envelope delivered %d times", inserter.callCount())
	}
}

func TestMetadataOnlyScopedRejectionSurvivesRestartAndRequiresProgressAck(t *testing.T) {
	dir := t.TempDir()
	sink := &recordingProgressSink{failCount: 1}
	cfg := enabledConfig(dir)
	cfg.ProgressSink = sink
	writer, err := New(cfg, &recordingInserter{})
	if err != nil {
		t.Fatal(err)
	}
	invalid := canonicalSpan("2026-08-13 12:00:00.000001", nil)
	invalid["attrs_number"] = map[string]any{"score": 1.0}
	job, report := writer.StageCanonicalSpans([]map[string]any{invalid})
	if !job.Empty() || report.RejectedSpans != 1 || report.UnscopedRejectedSpans != 0 ||
		len(job.Metadata().Projects) != 1 || job.Metadata().Projects[0].RejectedSpans != 1 {
		t.Fatalf("scoped rejection job=%+v report=%+v", job.Metadata(), report)
	}
	if err := writer.Submit(context.Background(), job); err != nil {
		t.Fatal(err)
	}
	restarted, err := New(cfg, &recordingInserter{})
	if err != nil {
		t.Fatal(err)
	}
	pending, err := restarted.Pending()
	if err != nil || len(pending) != 1 || pending[0].Metadata.Projects[0].RejectedSpans != 1 {
		t.Fatalf("pending metadata=%+v err=%v", pending, err)
	}
	first, err := restarted.Replay(context.Background())
	if err == nil || first != (ReplayResult{Attempted: 1}) {
		t.Fatalf("progress failure replay=%+v err=%v", first, err)
	}
	if remaining, pendingErr := restarted.Pending(); pendingErr != nil || len(remaining) != 1 {
		t.Fatalf("unacknowledged metadata was deleted: %+v err=%v", remaining, pendingErr)
	}
	second, err := restarted.Replay(context.Background())
	if err != nil || second != (ReplayResult{Attempted: 1, Delivered: 1}) {
		t.Fatalf("second replay=%+v err=%v", second, err)
	}
	records := sink.snapshot()
	if len(records) != 2 || records[0].EnvelopeID == "" ||
		records[0].EnvelopeID != records[1].EnvelopeID || records[0].Metadata.CatalogEpoch != 1 {
		t.Fatalf("idempotent progress records=%+v", records)
	}
}

func TestMixedProjectMetadataAndUnscopedGapAreDurable(t *testing.T) {
	secondProject := "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
	second := canonicalSpan("2026-08-13 13:00:00.000001", map[string]string{})
	second["project_id"] = secondProject
	unscoped := canonicalSpan("2026-08-13 14:00:00.000001", map[string]string{})
	unscoped["project_id"] = "not-a-uuid"
	writer, err := New(enabledConfig(t.TempDir()), &recordingInserter{})
	if err != nil {
		t.Fatal(err)
	}
	job, _ := writer.StageCanonicalSpans([]map[string]any{
		canonicalSpan("2026-08-13 12:00:00.000001", map[string]string{}), second, unscoped,
	})
	metadata := job.Metadata()
	if metadata.UnscopedRejectedSpans != 1 || metadata.InputSpans != 3 ||
		len(metadata.Projects) != 2 || metadata.Projects[0].ProjectID != testProjectID ||
		metadata.Projects[1].ProjectID != secondProject ||
		!containsString(metadata.UnscopedGapReasons, "unscoped_rejection") {
		t.Fatalf("mixed metadata=%+v", metadata)
	}
	metadata.Projects[0].GapReasons = append(metadata.Projects[0].GapReasons, "mutated")
	if containsString(job.Metadata().Projects[0].GapReasons, "mutated") {
		t.Fatal("Job.Metadata exposed mutable nested slices")
	}
}

func TestInputLimitProducesBoundedMetadataOnlyGap(t *testing.T) {
	cfg := enabledConfig(t.TempDir())
	cfg.MaxJobInputSpans = 1
	writer, err := New(cfg, &recordingInserter{})
	if err != nil {
		t.Fatal(err)
	}
	job, report := writer.StageCanonicalSpans([]map[string]any{
		canonicalSpan("2026-08-13 12:00:00.000001", nil),
		canonicalSpan("2026-08-13 12:00:01.000001", nil),
	})
	metadata := job.Metadata()
	if !job.Empty() || report.InputSpans != 2 || metadata.InputSpans != 1 ||
		metadata.OverflowSpans != 1 || !containsString(metadata.GapReasons, "input_span_limit") {
		t.Fatalf("input limit job=%+v report=%+v", metadata, report)
	}
	if err := writer.Submit(context.Background(), job); err != nil {
		t.Fatal(err)
	}
}

func TestSubmissionGapErrorCarriesAttemptMetadata(t *testing.T) {
	cfg := enabledConfig(t.TempDir())
	cfg.MaxSpoolFiles = 1
	writer, err := New(cfg, &recordingInserter{})
	if err != nil {
		t.Fatal(err)
	}
	job, _ := writer.StageCanonicalSpans([]map[string]any{
		keyOnlySpan("2026-08-13 12:00:00.000001", "map"),
	})
	if err := writer.Submit(context.Background(), job); err != nil {
		t.Fatal(err)
	}
	err = writer.Submit(context.Background(), job)
	var gap *SubmissionGapError
	if !errors.As(err, &gap) || gap.Metadata.InputSpans != 1 || gap.Metadata.CatalogEpoch != 1 {
		t.Fatalf("submission gap=%T %+v", err, err)
	}
}
