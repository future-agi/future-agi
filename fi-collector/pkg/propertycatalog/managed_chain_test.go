package propertycatalog

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"testing"
	"time"
)

// These component integration tests use the real candidate, receipt, spool,
// producer-state, wire, sequence, and delivery implementations. Transport and
// authoritative lease/database state are modeled explicitly; this is not a
// live Kafka/ClickHouse or application-lifecycle qualification claim.
func managedChainRows() []ScopedSpan {
	first := hotRow(testProject, testSeen,
		map[string]string{"plan": "Pro", "quote": "a\"b\\c", "unicode": "CAFÉ"},
		map[string]float64{"score": 1.5, "negative": -12.5, "zero": 0})
	first["attrs_bool"] = map[string]uint8{"enabled": 1, "disabled": 0}
	first["attributes_extra"] = map[string]any{
		"tags":     []any{"A", "B", "A", nil, float64(3), true, map[string]any{"nested": 1}},
		"map_only": map[string]any{"nested": 4}, "null_only": nil, "empty_array": []any{},
	}
	first["model"] = "Smoke-Model"
	second := hotRow(testProject, testLastSeen, map[string]string{"plan": "Pro", "versioned": "new"}, nil)
	second["model"] = "Smoke-Model"
	return []ScopedSpan{scopedHotRow(testWorkspace, testProject, first), scopedHotRow(testWorkspace, testProject, second)}
}

func requireManagedChainGolden(t *testing.T, values []CandidateValue) {
	t.Helper()
	golden := []string{
		`custom_attribute/plan/string/"Pro"/pro`,
		`custom_attribute/quote/string/"a\"b\\c"/a"b\c`,
		`custom_attribute/unicode/string/"CAFÉ"/café`,
		`custom_attribute/score/number/1.5/1.5`,
		`custom_attribute/negative/number/-12.5/-12.5`,
		`custom_attribute/zero/number/0/0`,
		`custom_attribute/enabled/boolean/true/true`,
		`custom_attribute/disabled/boolean/false/false`,
		`custom_attribute/tags/array/"A"/a`,
		`custom_attribute/tags/array/"B"/b`,
		`custom_attribute/tags/array/3/3`,
		`custom_attribute/tags/array/true/true`,
		`system_attribute/model/string/"Smoke-Model"/smoke-model`,
		`custom_attribute/versioned/string/"new"/new`,
	}
	actual := make([]string, 0, len(values))
	for _, value := range values {
		actual = append(actual, fmt.Sprintf("%s/%s/%s/%s/%s", value.SourceKind, value.AttributeKey,
			value.AttributeType, value.ValueJSON, value.ValueSearchTextFolded))
		if (value.AttributeKey == "plan" || value.AttributeKey == "model") &&
			(value.FirstSeen != testSeen || value.LastSeen != testLastSeen) {
			t.Fatalf("observation min/max were not merged: %+v", value)
		}
	}
	sort.Strings(actual)
	sort.Strings(golden)
	if !reflect.DeepEqual(actual, golden) {
		t.Fatalf("projection differs from independent golden values:\nactual=%q\ngolden=%q", actual, golden)
	}
}

func managedChainRuntime(t *testing.T, cfg RuntimeConfig, writer *recordingRecordWriter) *HotRuntime {
	t.Helper()
	identity, err := LoadInstallationIdentity(filepath.Join(filepath.Dir(cfg.RevisionFenceFile), InstallationIdentityFilename))
	if err != nil {
		t.Fatal(err)
	}
	if cfg.CatalogEpoch != identity.CatalogEpoch || cfg.ProjectionVersion != identity.ProjectionVersion ||
		cfg.ProducerStreamID != identity.ProducerStreamID {
		t.Fatal("runtime differs from persisted installation identity")
	}
	provider, err := NewFileRevisionProvider(cfg.RevisionFenceFile)
	if err != nil {
		t.Fatal(err)
	}
	now, _ := time.Parse(dateTime64Layout, testSeen)
	provider.now = func() time.Time { return now }
	producer, err := NewProducer(cfg.Kafka.Topic, writer)
	if err != nil {
		t.Fatal(err)
	}
	runtime, err := NewHotRuntime(cfg, provider, producer)
	if err != nil {
		t.Fatal(err)
	}
	return runtime
}

func managedChainDeliveredValues(t *testing.T, sink *recordingSink, cfg RuntimeConfig, revision uint64) []CandidateValue {
	t.Helper()
	var values []CandidateValue
	for index, call := range sink.calls {
		if call == "ledger" {
			continue
		}
		if call != "data:"+string(AttributeValueTable) {
			t.Fatalf("hot path wrote non-value data: %s", call)
		}
		for _, fields := range sink.rows[index] {
			raw, err := json.Marshal(fields)
			if err != nil {
				t.Fatal(err)
			}
			var row AttributeValueRow
			if err := json.Unmarshal(raw, &row); err != nil {
				t.Fatal(err)
			}
			if row.OrganizationID != testOrganization || row.WorkspaceID != testWorkspace || row.ProjectID != testProject ||
				row.CatalogEpoch != cfg.CatalogEpoch || row.CatalogRevision != revision || row.BuildToken != testBuildToken {
				t.Fatalf("delivery changed identity or tenant scope: %+v", row)
			}
			values = append(values, CandidateValue{
				SourceKind: row.SourceKind, AttributeKey: row.AttributeKey, AttributeType: row.AttributeType,
				ValueFingerprint: row.ValueFingerprint, ValueJSON: row.ValueJSON, ValueSearchTextFolded: row.ValueSearchTextFolded,
				FirstSeen: row.FirstSeen, LastSeen: row.LastSeen,
			})
		}
	}
	sort.Slice(values, func(i, j int) bool { return candidateValueIdentity(values[i]) < candidateValueIdentity(values[j]) })
	return values
}

func managedChainLedgerSeed(t *testing.T, sink *recordingSink) StreamCheckpoint {
	t.Helper()
	// Read what the actual DeliveryHandler wrote, not the producer checkpoint.
	// This models durable DB state; it does not exercise HTTPCheckpointLoader's
	// authoritative database proof, which belongs to the separate live harness.
	for index := len(sink.calls) - 1; index >= 0; index-- {
		if sink.calls[index] != "ledger" {
			continue
		}
		row := sink.rows[index][0]
		return StreamCheckpoint{
			OrganizationID: row["organization_id"].(string), WorkspaceID: row["workspace_id"].(string),
			CatalogEpoch: row["catalog_epoch"].(uint16), CatalogRevision: row["catalog_revision"].(uint64),
			BuildToken: row["build_token"].(string), ProjectionVersion: row["projection_version"].(uint16),
			SourceAdapter: SourceAdapter(row["source_adapter"].(string)), ProducerStreamID: row["producer_stream_id"].(string),
			Sequence: row["sequence"].(uint64), Terminal: row["terminal"].(uint8) == 1,
			PayloadSHA256: row["payload_sha256"].(string), EnvelopeID: row["envelope_id"].(string),
		}
	}
	t.Fatal("consumer did not write a delivery ledger")
	return StreamCheckpoint{}
}

func TestManagedChainProjectionParityAndRestart(t *testing.T) {
	for _, checkpoint := range []string{"receipt_before_sequence", "spool_before_publish", "ack_before_spool_removal"} {
		t.Run(checkpoint, func(t *testing.T) {
			ctx := context.Background()
			cfg := validRuntimeConfig(t).WithDefaults()
			cfg.Mode, cfg.WorkspaceScopeMode, cfg.WorkspaceAllowlist = RuntimeSequencer, WorkspaceScopeRevisionFence, nil
			cfg.CatalogEpoch, cfg.ProjectionVersion = 7, 3
			cfg.MaxChunkRows = 2 // Exercise all chunks, not just the first row.
			rawIdentity := alteredInstallationIdentity(t, func(doc map[string]any) {
				doc["catalog_epoch"], doc["projection_version"] = 7, 3
				doc["ordered_topic"] = cfg.Kafka.Topic
			})
			identityPath := filepath.Join(filepath.Dir(cfg.RevisionFenceFile), InstallationIdentityFilename)
			if err := os.WriteFile(identityPath, rawIdentity, 0o400); err != nil {
				t.Fatal(err)
			}
			fence := testRevisionFence(41, "building")
			fence.CatalogEpoch, fence.ProjectionVersion = cfg.CatalogEpoch, cfg.ProjectionVersion
			writeCandidateTestFence(t, cfg.RevisionFenceFile, fence)
			rows := managedChainRows()
			beforeRows, _ := json.Marshal(rows)
			candidates, err := BuildCandidates(managedCandidateConfig(t), rows)
			if err != nil || len(candidates) != 1 {
				t.Fatalf("build candidates=%d err=%v", len(candidates), err)
			}
			candidate := candidates[0]
			original, _ := candidate.MarshalBinary()
			snapshot := candidate.Snapshot()
			if snapshot.Version != 2 || snapshot.CatalogEpoch != 0 || snapshot.ProjectionVersion != 0 ||
				snapshot.SourceRows != 2 || len(snapshot.GapReasons) != 0 {
				t.Fatalf("collector allocated identity or lost data: %+v", snapshot)
			}
			requireManagedChainGolden(t, snapshot.Values)
			candidateWriter := &recordingRecordWriter{}
			candidateTopic := "futureagi.dev.property-catalog.candidates.v1"
			candidateProducer, err := NewCandidateProducer(candidateTopic, candidateWriter)
			if err != nil {
				t.Fatal(err)
			}
			if err := candidateProducer.Publish(ctx, candidate); err != nil {
				t.Fatal(err)
			}
			record := candidateWriter.records[0]
			record.Partition, record.Offset = 0, 21
			if !bytes.Equal(record.Value, original) || string(record.Key) != testWorkspace {
				t.Fatal("candidate producer changed bytes or tenant key")
			}
			orderedWriter := &recordingRecordWriter{}
			runtime := managedChainRuntime(t, cfg, orderedWriter)
			receiptDirectory := filepath.Join(cfg.SpoolDirectory, "candidate-receipts")
			receipts := testReceiptStore(t, receiptDirectory, candidateTopic)
			source := &oneRecordSource{record: record}
			if checkpoint == "receipt_before_sequence" {
				source.commitErr = errors.New("injected ambiguous candidate offset commit")
			}
			sequencer, err := NewCandidateSequencer(candidateTopic, source, receipts, runtime)
			if err != nil {
				t.Fatal(err)
			}
			err = sequencer.ProcessOne(ctx)
			if (err != nil) != (source.commitErr != nil) {
				t.Fatalf("unexpected receipt boundary error: %v", err)
			}
			if checkpoint == "ack_before_spool_removal" {
				pending, err := runtime.spool.PendingEnvelopes()
				if err != nil || len(pending) != 1 {
					t.Fatalf("spool count=%d err=%v", len(pending), err)
				}
				if err := runtime.publisher.Publish(ctx, pending[0]); err != nil {
					t.Fatal(err)
				}
			}
			// Reconstruct all local state from the same durable volume.
			runtime = managedChainRuntime(t, cfg, orderedWriter)
			receipts = testReceiptStore(t, receiptDirectory, candidateTopic)
			source = &oneRecordSource{record: record}
			sequencer, err = NewCandidateSequencer(candidateTopic, source, receipts, runtime)
			if err != nil {
				t.Fatal(err)
			}
			if err := sequencer.ReplayPending(ctx); err != nil {
				t.Fatal(err)
			}
			// Broker redelivery after restart must not allocate another sequence.
			if err := sequencer.ProcessOne(ctx); err != nil {
				t.Fatal(err)
			}
			if _, err := runtime.spool.Replay(ctx, runtime.publisher); err != nil {
				t.Fatal(err)
			}
			if len(orderedWriter.records) != 1 || sequencer.SkippedCandidates() != 0 {
				t.Fatalf("restart duplicated or skipped data: records=%d skipped=%d", len(orderedWriter.records), sequencer.SkippedCandidates())
			}
			ordered := orderedWriter.records[0]
			ordered.Partition, ordered.Offset = 0, 37
			envelope, err := ParseWireEnvelope(ordered.Value)
			if err != nil {
				t.Fatal(err)
			}
			issued := envelope.Snapshot()
			if issued.CatalogEpoch != 7 || issued.CatalogRevision != 41 || issued.ProjectionVersion != 3 ||
				issued.Sequence != 1 || issued.Payload.SourceBatchDigest != snapshot.CandidateID || issued.Payload.ValueRows != 14 ||
				issued.Payload.SourceRows != 2 || issued.Payload.DefinitionRows != 0 || len(issued.Payload.Chunks) != 7 {
				t.Fatalf("ordered binding or payload changed: %+v", issued)
			}
			sink := &recordingSink{}
			guard := &recordingLeaseGuard{role: "hot_values", projectIDs: []string{testProject}}
			handler, err := NewDeliveryHandler(sink, guard, time.Second)
			if err != nil {
				t.Fatal(err)
			}
			validator, _ := NewSequenceValidator(nil)
			consumerSource := &oneRecordSource{record: ordered, commitErr: errors.New("injected crash after ledger before commit")}
			consumer, err := NewConsumer(cfg.Kafka.Topic, consumerSource, handler, validator)
			if err != nil {
				t.Fatal(err)
			}
			if err := consumer.ProcessOne(ctx); err == nil {
				t.Fatal("consumer ignored injected commit failure")
			}
			values := managedChainDeliveredValues(t, sink, cfg, 41)
			if !reflect.DeepEqual(values, snapshot.Values) {
				t.Fatalf("consumer changed projected values/fingerprints/times:\nactual=%+v\nwant=%+v", values, snapshot.Values)
			}
			requireManagedChainGolden(t, values)
			if len(sink.calls) != 8 || sink.calls[7] != "ledger" {
				t.Fatalf("all data chunks must precede the ledger: %v", sink.calls)
			}
			seed := managedChainLedgerSeed(t, sink)
			validator, err = NewSequenceValidator([]StreamCheckpoint{seed})
			if err != nil {
				t.Fatal(err)
			}
			guard = &recordingLeaseGuard{failAt: 1}
			handler, _ = NewDeliveryHandler(sink, guard, time.Second)
			consumerSource = &oneRecordSource{record: ordered}
			consumer, err = NewConsumer(cfg.Kafka.Topic, consumerSource, handler, validator)
			if err != nil {
				t.Fatal(err)
			}
			if err := consumer.ProcessOne(ctx); err != nil || consumerSource.commits != 1 || len(sink.calls) != 8 || len(guard.requests) != 0 {
				t.Fatalf("consumer restart refreshed exact durable delivery: err=%v writes=%v", err, sink.calls)
			}
			// A producer restart after ACK/spool removal must resume at sequence 2.
			runtime = managedChainRuntime(t, cfg, orderedWriter)
			next := mustCandidates(t, managedCandidateConfig(t), []ScopedSpan{
				scopedHotRow(testWorkspace, testProject, hotRow(testProject, testLastSeen, map[string]string{"plan": "Enterprise"}, nil)),
			})[0]
			if duplicate, err := runtime.AcceptCandidate(next); err != nil || duplicate {
				t.Fatalf("new candidate failed after restart: duplicate=%v err=%v", duplicate, err)
			}
			if _, err := runtime.spool.Replay(ctx, runtime.publisher); err != nil {
				t.Fatal(err)
			}
			if len(orderedWriter.records) != 2 {
				t.Fatalf("new ordered record count=%d", len(orderedWriter.records))
			}
			nextEnvelope, err := ParseWireEnvelope(orderedWriter.records[1].Value)
			if err != nil || nextEnvelope.Snapshot().Sequence != 2 || nextEnvelope.Snapshot().PreviousPayloadSHA256 != issued.PayloadSHA256 {
				t.Fatalf("producer restarted with a broken chain: %v", err)
			}
			guard.failAt, guard.role = 0, "hot_values"
			consumerSource.record = orderedWriter.records[1]
			consumerSource.record.Partition, consumerSource.record.Offset = 0, 38
			if err := consumer.ProcessOne(ctx); err != nil {
				t.Fatalf("restarted consumer rejected next sequence: %v", err)
			}
			afterRows, _ := json.Marshal(rows)
			afterCandidate, _ := candidate.MarshalBinary()
			afterIdentity, err := os.ReadFile(identityPath)
			if err != nil || !bytes.Equal(beforeRows, afterRows) || !bytes.Equal(original, afterCandidate) || !bytes.Equal(rawIdentity, afterIdentity) {
				t.Fatal("processing/restart mutated canonical source, candidate, or persisted identity")
			}
		})
	}
}

func managedChainSpoolBytes(t *testing.T, directory string) map[string]string {
	t.Helper()
	files := make(map[string]string)
	err := filepath.WalkDir(directory, func(path string, entry fs.DirEntry, err error) error {
		if err != nil || entry.IsDir() {
			return err
		}
		raw, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		name, err := filepath.Rel(directory, path)
		files[name] = string(raw)
		return err
	})
	if err != nil {
		t.Fatal(err)
	}
	return files
}

func TestManagedChainFreshDescriptorCannotRebindPreviousProducerSpool(t *testing.T) {
	for _, persisted := range []string{"pending_spool", "ack_state", "ack_and_retained_spool"} {
		t.Run(persisted, func(t *testing.T) {
			cfg := validRuntimeConfig(t).WithDefaults()
			cfg.Mode = RuntimeSequencer
			rawIdentity := alteredInstallationIdentity(t, func(doc map[string]any) { doc["ordered_topic"] = cfg.Kafka.Topic })
			if err := os.WriteFile(filepath.Join(filepath.Dir(cfg.RevisionFenceFile), InstallationIdentityFilename), rawIdentity, 0o400); err != nil {
				t.Fatal(err)
			}
			fence := testRevisionFence(17, "building")
			writeCandidateTestFence(t, cfg.RevisionFenceFile, fence)
			writer := &recordingRecordWriter{}
			runtime := managedChainRuntime(t, cfg, writer)
			if _, err := runtime.AcceptCandidate(managedCandidate(t)); err != nil {
				t.Fatal(err)
			}
			if persisted == "ack_state" {
				if _, err := runtime.spool.Replay(context.Background(), runtime.publisher); err != nil {
					t.Fatal(err)
				}
			} else if persisted == "ack_and_retained_spool" {
				pending, err := runtime.spool.PendingEnvelopes()
				if err != nil || len(pending) != 1 {
					t.Fatalf("pending=%d err=%v", len(pending), err)
				}
				if err := runtime.publisher.Publish(context.Background(), pending[0]); err != nil {
					t.Fatal(err)
				}
			}
			before := managedChainSpoolBytes(t, cfg.SpoolDirectory)
			published := len(writer.records)
			if len(before) == 0 {
				t.Fatal("test did not persist any previous producer state")
			}
			// A new DB/runtime volume has a different Python-compatible descriptor,
			// but the operator mistakenly reuses the old, separate spool volume.
			cfg.RevisionFenceFile = filepath.Join(t.TempDir(), "revision-fence.json")
			writeCandidateTestFence(t, cfg.RevisionFenceFile, fence)
			newPath := filepath.Join(filepath.Dir(cfg.RevisionFenceFile), InstallationIdentityFilename)
			fresh := alteredInstallationIdentity(t, func(doc map[string]any) {
				doc["target_database"] = "property_catalog_new_empty_destination"
				doc["producer_stream_id"] = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
				doc["ordered_topic"] = cfg.Kafka.Topic
			})
			if err := os.WriteFile(newPath, fresh, 0o400); err != nil {
				t.Fatal(err)
			}
			identity, err := LoadInstallationIdentity(newPath)
			if err != nil {
				t.Fatal(err)
			}
			cfg.ProducerStreamID = identity.ProducerStreamID
			provider, err := NewFileRevisionProvider(cfg.RevisionFenceFile)
			if err != nil {
				t.Fatal(err)
			}
			producer, err := NewProducer(cfg.Kafka.Topic, writer)
			if err != nil {
				t.Fatal(err)
			}
			if _, err := NewHotRuntime(cfg, provider, producer); err == nil {
				t.Fatal("fresh descriptor silently rebound the previous producer's durable state")
			}
			if len(writer.records) != published || !reflect.DeepEqual(before, managedChainSpoolBytes(t, cfg.SpoolDirectory)) {
				t.Fatal("rejected startup published or changed previous producer state")
			}
			if after, err := os.ReadFile(newPath); err != nil || !bytes.Equal(after, fresh) {
				t.Fatalf("rejection changed the new descriptor: %v", err)
			}
		})
	}
}
