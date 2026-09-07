package propertycatalog

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
	"time"
)

// Only HTTP observations are synthetic: inventory parsing, exact build-plan
// validation, all-member agreement, and the consumer journal are real.
type checkpointOwnershipFixture struct {
	t         *testing.T
	loader    *ClickHouseCheckpointLoader
	sink      *ClickHouseSink
	proof     *HTTPWriteProof
	metadata  *proofHTTPServer
	journal   *WriteAttemptJournal
	directory string
	inventory []checkpointInventoryJSON
	streams   map[string]checkpointStreamProofJSON
	reads     map[string]int
	reply     func(stage, member, stream string, rows []any) ([]any, error)
}

func newCheckpointOwnershipFixture(t *testing.T, members int) *checkpointOwnershipFixture {
	t.Helper()
	proof, metadata := proofHTTPFixture(t, members)
	f := &checkpointOwnershipFixture{
		t: t, proof: proof, metadata: metadata, directory: t.TempDir(),
		inventory: validCheckpointInventory(t, false),
		streams:   make(map[string]checkpointStreamProofJSON), reads: make(map[string]int),
	}
	// Nine backend/reconcile streams have regular envelopes and completed
	// checkpoints. The Kafka hot stream is still empty in this reservation.
	completed := validCheckpointInventory(t, true)
	for i, row := range f.inventory {
		if row.StreamEnvelopeVersion != EnvelopeVersion || row.StreamProducerStreamID == testStream {
			continue
		}
		row = completed[i]
		row.ReservationStatus = "open"
		row.ActivationEvidenceRows, row.ActivationProjectionVersion = 0, 0
		row.ActivationStatus, row.ActivationVersion, row.ActivationStateVariants = "", 0, 0
		f.inventory[i] = row
		f.streams[row.StreamProducerStreamID] = validCheckpointProof(row, true)
	}
	transport := roundTripFunc(func(r *http.Request) (*http.Response, error) {
		statement := checkpointRequestStatement(t, r)
		stage, stream := "", r.URL.Query().Get("param_producer_stream_id")
		var rows []any
		switch {
		case statement == checkpointInventoryQuery:
			stage = "inventory"
			for _, row := range f.inventory {
				rows = append(rows, row)
			}
		case statement == checkpointStreamQuery:
			stage = "stream"
			q := r.URL.Query()
			if q.Get("param_organization_id") != testOrganization || q.Get("param_workspace_id") != testWorkspace ||
				q.Get("param_build_token") != testBuildToken || q.Get("param_catalog_revision") != "17" ||
				q.Get("max_rows_to_group_by") != "100001" || q.Get("group_by_overflow_mode") != "throw" {
				t.Fatal("stream proof lost exact scope or full-prefix bounds")
			}
			if row, ok := f.streams[stream]; ok {
				rows = append(rows, row)
			}
		case strings.HasPrefix(statement, "SELECT sequence,envelope_id FROM property_catalog_deliveries"):
			stage, stream = "prefix", r.URL.Query().Get("param_stream")
			if stream != testStream {
				t.Fatalf("consumer tried to load backend-owned manifests for %s", stream)
			}
			for sequence := 1; sequence <= 3; sequence++ {
				rows = append(rows, map[string]any{"sequence": sequence, "envelope_id": testDigest(fmt.Sprintf("hot-prefix-%d", sequence))})
			}
		default:
			r.Body = io.NopCloser(strings.NewReader(statement))
			return metadata.RoundTrip(r)
		}
		q := r.URL.Query()
		if q.Get("wait_end_of_query") != "1" || q.Get("use_query_cache") != "0" || q.Get("max_result_bytes") == "" {
			t.Fatal("checkpoint proof lost complete-response/cache/byte bounds")
		}
		member := r.URL.Hostname()
		f.reads[stage+":"+member+":"+stream]++
		if f.reply != nil {
			var err error
			rows, err = f.reply(stage, member, stream, rows)
			if err != nil {
				return nil, err
			}
		}
		return proofJSONResponse(t, rows...), nil
	})
	proof.reader.client.Transport = transport
	var err error
	f.journal, err = OpenWriteAttemptJournal(f.directory)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { f.journal.Close() })
	cfg := ClickHouseSinkConfig{
		URL: metadata.doc.Members[0].URL, Database: metadata.doc.Database,
		Environment: metadata.doc.Environment, Username: "writer", RoundTripper: transport,
		WritePolicy: proof.policy, WriteJournal: f.journal, WriteProof: proof,
	}
	f.sink, err = NewClickHouseSink(cfg)
	if err != nil {
		t.Fatal(err)
	}
	proof.writer = f.sink
	cfg.Username = "reader"
	f.loader, err = NewClickHouseCheckpointLoader(cfg, CheckpointLoaderLimits{
		MaxStreams: 16, InventoryMaxBytes: 1 << 20, InventoryTimeout: 5 * time.Second,
	})
	if err != nil {
		t.Fatal(err)
	}
	if err := f.loader.BindCompletionProof(proof, f.sink); err != nil {
		t.Fatal(err)
	}
	return f
}

func (f *checkpointOwnershipFixture) load() ([]StreamCheckpoint, error) {
	f.t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	return f.loader.LoadCheckpoints(ctx)
}

func (f *checkpointOwnershipFixture) requireNoJournalWrites() {
	f.t.Helper()
	entries, err := os.ReadDir(filepath.Join(f.directory, WriteAttemptDirectory))
	if err != nil || len(entries) != 0 || f.metadata.inserts != 0 {
		f.t.Fatalf("proof-only restart mutated journal/data: entries=%d inserts=%d err=%v", len(entries), f.metadata.inserts, err)
	}
}

func TestCheckpointOwnershipBackendRegularEnvelopesSeedWithoutGoReceipts(t *testing.T) {
	for _, members := range []int{1, 3} {
		t.Run(fmt.Sprint(members), func(t *testing.T) {
			f := newCheckpointOwnershipFixture(t, members)
			first, err := f.load()
			if err != nil || len(first) != 9 {
				t.Fatalf("backend checkpoints=%d err=%v", len(first), err)
			}
			f.requireNoJournalWrites()
			if err := f.journal.Close(); err != nil {
				t.Fatal(err)
			}
			f.journal, err = OpenWriteAttemptJournal(f.directory)
			if err != nil {
				t.Fatal(err)
			}
			f.sink.writer.journal = f.journal
			second, err := f.load()
			if err != nil || !reflect.DeepEqual(first, second) {
				t.Fatalf("reopened journal changed checkpoint seed: %v", err)
			}
			for _, member := range f.metadata.doc.Members {
				if f.reads["inventory:"+member.Name+":"] != 2 {
					t.Fatal("inventory was not re-read on every member")
				}
				for _, row := range f.inventory[1:] {
					if f.reads["stream:"+member.Name+":"+row.StreamProducerStreamID] != 2 {
						t.Fatal("cold/hot full-chain proof was not re-read on every member")
					}
				}
			}
			for _, checkpoint := range first {
				if checkpoint.ProducerStreamID == testStream || !checkpoint.Terminal || checkpoint.Sequence != 3 {
					t.Fatal("checkpoint ownership or terminal chain changed")
				}
			}
			f.requireNoJournalWrites()
		})
	}
}

func TestCheckpointOwnershipHotMissingManifestStillFails(t *testing.T) {
	f := newCheckpointOwnershipFixture(t, 3)
	for _, row := range f.inventory {
		if row.StreamProducerStreamID == testStream {
			f.streams[testStream] = validCheckpointProof(row, false)
		}
	}
	checkpoints, err := f.load()
	if checkpoints != nil || !errors.Is(err, ErrWriteUnresolved) || !errors.Is(err, os.ErrNotExist) {
		t.Fatalf("missing hot receipt was accepted: checkpoints=%v err=%v", checkpoints, err)
	}
	for _, member := range f.metadata.doc.Members {
		if f.reads["prefix:"+member.Name+":"+testStream] != 1 {
			t.Fatal("hot receipt prefix was not agreed across every member")
		}
	}
	f.requireNoJournalWrites()
}

func TestCheckpointOwnershipValidatedPhysicalSnapshotHotRestartNeedsNoGoReceipt(t *testing.T) {
	for _, sequence := range []uint64{1, 2} {
		t.Run(fmt.Sprint(sequence), func(t *testing.T) {
			f := newCheckpointOwnershipFixture(t, 3)
			f.inventory = validCheckpointInventory(t, true)
			for i, row := range f.inventory {
				if row.StreamEnvelopeVersion != EnvelopeVersion {
					continue
				}
				proof := validCheckpointProof(row, true)
				if row.StreamProducerStreamID == testStream {
					row.StreamLastSequence, row.StreamMaxContiguousSequence = sequence, sequence
					row.StreamLastIssuedSequence, row.StreamFencedSequence = sequence, sequence
					row.CheckpointLastSequence, row.CheckpointLastIssuedSequence, row.CheckpointFencedSequence = sequence, sequence, sequence
					proof.SequenceRows, proof.LastSequence, proof.DistinctSequences = sequence, sequence, sequence
					proof.TerminalSequence, proof.PhysicalSnapshotSequences = sequence, sequence
					proof.TailEnvelopeFormat = physicalSnapshotEnvelopeFormat
				}
				f.inventory[i], f.streams[row.StreamProducerStreamID] = row, proof
			}
			first, err := f.load()
			if err != nil || len(first) != 10 {
				t.Fatalf("validated snapshot rejected: checkpoints=%d err=%v", len(first), err)
			}
			if err := f.journal.Close(); err != nil {
				t.Fatal(err)
			}
			f.journal, err = OpenWriteAttemptJournal(f.directory)
			if err != nil {
				t.Fatal(err)
			}
			f.sink.writer.journal = f.journal
			second, err := f.load()
			if err != nil || !reflect.DeepEqual(first, second) {
				t.Fatalf("physical snapshot restart changed evidence: %v", err)
			}
			for _, checkpoint := range second {
				if checkpoint.ProducerStreamID == testStream && (!checkpoint.Terminal || checkpoint.Sequence != sequence) {
					t.Fatal("snapshot sequence/terminal identity changed")
				}
			}
			if f.streams[testStream].TailEnvelopeFormat != physicalSnapshotEnvelopeFormat {
				t.Fatal("snapshot was relabeled")
			}
			f.requireNoJournalWrites()
		})
	}
}

func TestCheckpointOwnershipColdStillRequiresAllMemberProof(t *testing.T) {
	for _, fault := range []string{"inventory-disagreement", "stream-disagreement", "member-unavailable", "topology-drift"} {
		t.Run(fault, func(t *testing.T) {
			f := newCheckpointOwnershipFixture(t, 3)
			f.metadata.drift = fault == "topology-drift"
			f.reply = func(stage, member, stream string, rows []any) ([]any, error) {
				if member != f.metadata.doc.Members[2].Name {
					return rows, nil
				}
				if fault == "member-unavailable" {
					return nil, io.ErrUnexpectedEOF
				}
				if fault == "inventory-disagreement" && stage == "inventory" {
					row := rows[1].(checkpointInventoryJSON)
					row.CheckpointVersion++
					rows[1] = row
				}
				if fault == "stream-disagreement" && stage == "stream" && len(rows) != 0 {
					row := rows[0].(checkpointStreamProofJSON)
					row.TailPayloadSHA256 = testDigest("different member")
					rows[0] = row
				}
				return rows, nil
			}
			if checkpoints, err := f.load(); err == nil || checkpoints != nil {
				t.Fatalf("cold proof fault bypassed: checkpoints=%v err=%v", checkpoints, err)
			}
			f.requireNoJournalWrites()
		})
	}
}

func TestCheckpointOwnershipRejectsChangedRoleScopeOrDigest(t *testing.T) {
	for _, fault := range []string{"role", "lease-digest", "stream-role-mismatch", "stream-scope", "role-from-sql"} {
		t.Run(fault, func(t *testing.T) {
			f := newCheckpointOwnershipFixture(t, 1)
			switch fault {
			case "role", "lease-digest":
				plan := f.inventory[0].ReservationBuildPlanJSON
				if fault == "role" {
					plan = strings.Replace(plan, `"role":"hot_values"`, `"role":"definitions"`, 1)
				}
				digest := sha256Hex([]byte(plan))
				if fault == "lease-digest" {
					digest = testDigest("not the plan")
				}
				for i := range f.inventory {
					f.inventory[i].ReservationBuildPlanJSON, f.inventory[i].StreamBuildPlanJSON = plan, plan
					f.inventory[i].ReservationBuildLeaseSHA256, f.inventory[i].StreamBuildLeaseSHA256 = digest, digest
				}
			case "stream-role-mismatch":
				f.inventory[1].StreamBuildPlanJSON = strings.Replace(f.inventory[1].StreamBuildPlanJSON, `"role":"hot_values"`, `"role":"values"`, 1)
				f.inventory[1].StreamBuildLeaseSHA256 = sha256Hex([]byte(f.inventory[1].StreamBuildPlanJSON))
			case "stream-scope":
				f.inventory[1].StreamProducerStreamID = "99999999-9999-4999-8999-999999999999"
			case "role-from-sql":
				f.reply = func(stage, _, _ string, rows []any) ([]any, error) {
					if stage == "inventory" {
						raw, _ := json.Marshal(rows[1])
						var row map[string]any
						if err := json.Unmarshal(raw, &row); err != nil {
							t.Fatal(err)
						}
						row["validatedStreamRole"] = "definitions"
						rows[1] = row
					}
					return rows, nil
				}
			}
			if checkpoints, err := f.load(); err == nil || checkpoints != nil {
				t.Fatalf("invalid ownership accepted: checkpoints=%v err=%v", checkpoints, err)
			}
			for key := range f.reads {
				if !strings.HasPrefix(key, "inventory:") {
					t.Fatal("invalid role reached stream/receipt proof")
				}
			}
			f.requireNoJournalWrites()
		})
	}
}

func TestCheckpointOwnershipColdStillRejectsBrokenChainAndCheckpoint(t *testing.T) {
	for _, fault := range []string{"chain", "conflict", "terminal", "checkpoint"} {
		t.Run(fault, func(t *testing.T) {
			f := newCheckpointOwnershipFixture(t, 3)
			row := &f.inventory[1]
			proof := f.streams[row.StreamProducerStreamID]
			switch fault {
			case "chain":
				proof.ChainBreaks = 1
			case "conflict":
				proof.MaxIdentityVariants, proof.ConflictSequences = 2, 1
			case "terminal":
				proof.TailTerminal = 0
			case "checkpoint":
				row.CheckpointTerminalPayloadSHA256 = testDigest("different checkpoint")
			}
			f.streams[row.StreamProducerStreamID] = proof
			if checkpoints, err := f.load(); err == nil || checkpoints != nil {
				t.Fatalf("cold validation bypassed: checkpoints=%v err=%v", checkpoints, err)
			}
			f.requireNoJournalWrites()
		})
	}
}
