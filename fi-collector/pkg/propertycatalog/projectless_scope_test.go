package propertycatalog

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// Independently generated with Python json.dumps(sort_keys=True,
// separators=(",", ":")) for the build plan and the existing v2 length-prefixed
// SHA-256/ordered assignment serializer in coordinator.py for the fence. No new
// schema version, omitted field, or null inventory is implied by zero projects.
const pythonProjectlessBuildLeaseSHA256 = "bc3c82ed775fc444d89afd04cd674f9f2eeebc55183d3874dca603480d47490f"

const pythonProjectlessFenceV2Fixture = `{"format":"futureagi.property-catalog-revision-fence","version":2,"fences":[{"organization_id":"11111111-1111-4111-8111-111111111111","workspace_id":"22222222-2222-4222-8222-222222222222","catalog_epoch":3,"catalog_revision":17,"projection_version":1,"build_lease_sha256":"bc3c82ed775fc444d89afd04cd674f9f2eeebc55183d3874dca603480d47490f","build_token":"55555555-5555-4555-8555-555555555555","project_ids":[],"span_since_us":1786708800000000,"span_until_us":1786712400000000,"issued_at":"2026-08-14 11:59:00.000000","expires_at":"2026-08-14 12:02:00.000000","drain_deadline":"","fenced_sequence":0,"status":"building","fence_sha256":"a9f8f3c77638b65098ce747aa3b8c0411af8695e6d8b356a41ecba0de3085028"}]}
`

func projectlessBuildPlan(t *testing.T) (string, string) {
	t.Helper()
	value, _ := validBuildPlan()
	var plan buildPlanDocumentJSON
	if err := json.Unmarshal([]byte(value), &plan); err != nil {
		t.Fatal(err)
	}
	plan.SourceScope.ProjectIDs = []string{}
	raw, err := json.Marshal(plan)
	if err != nil {
		t.Fatal(err)
	}
	return string(raw), testDigest(string(raw))
}

func projectlessFence() RevisionFence {
	fence := testRevisionFence(17, "building")
	fence.ProjectIDs = []string{}
	fence.BuildLeaseSHA256 = pythonProjectlessBuildLeaseSHA256
	fence.FenceSHA256 = RevisionFenceSHA256(fence)
	return fence
}

func projectlessNow() time.Time {
	return time.Date(2026, 8, 14, 12, 0, 0, 0, time.UTC)
}

func TestProjectlessCanonicalBuildPlanAndFenceRoundTrip(t *testing.T) {
	value, lease := projectlessBuildPlan(t)
	if lease != pythonProjectlessBuildLeaseSHA256 {
		t.Fatalf("Go build plan differs from Python canonical bytes: digest=%s", lease)
	}
	var plan buildPlanDocumentJSON
	if err := json.Unmarshal([]byte(value), &plan); err != nil {
		t.Fatal(err)
	}
	if len(plan.Streams) != 10 {
		t.Fatalf("empty scope lost planned physical streams: %d", len(plan.Streams))
	}
	for _, stream := range plan.Streams {
		request := validDeliveryLeaseRequest()
		request.SourceAdapter, request.ProducerStreamID = stream.SourceAdapter, stream.ProducerStreamID
		evidence, err := validateBuildPlan(value, lease, request)
		if err != nil || evidence.StreamRole != stream.Role || evidence.ProjectIDs == nil || len(evidence.ProjectIDs) != 0 {
			t.Fatalf("stream=%+v evidence=%+v err=%v", stream, evidence, err)
		}
		raw, err := json.Marshal(buildPlanSourceScopeJSON{
			ProjectIDs: evidence.ProjectIDs, SpanSinceUS: evidence.SpanSinceUS, SpanUntilUS: evidence.SpanUntilUS,
		})
		if err != nil || string(raw) != `{"project_ids":[],"span_since_us":1786708800000000,"span_until_us":1786712400000000}` {
			t.Fatalf("source scope round trip lost Python [] contract: %s err=%v", raw, err)
		}
	}
	raw, err := EncodeRevisionFenceFile([]RevisionFence{projectlessFence()})
	if err != nil || string(raw) != pythonProjectlessFenceV2Fixture {
		t.Fatalf("Go/Python empty fence bytes drifted: %s err=%v", raw, err)
	}
	path := filepath.Join(t.TempDir(), "fence.json")
	if err := os.WriteFile(path, []byte(pythonProjectlessFenceV2Fixture), 0o600); err != nil {
		t.Fatal(err)
	}
	provider, err := NewFileRevisionProvider(path)
	if err != nil {
		t.Fatal(err)
	}
	provider.now = projectlessNow
	fences, err := provider.CurrentRevisions(context.Background())
	if err != nil || len(fences) != 1 || fences[0].ProjectIDs == nil || len(fences[0].ProjectIDs) != 0 {
		t.Fatalf("loaded fence lost explicit empty inventory: %+v err=%v", fences, err)
	}
	again, err := EncodeRevisionFenceFile(fences)
	if err != nil || !bytes.Equal(raw, again) {
		t.Fatalf("file-provider round trip changed canonical fence: %s err=%v", again, err)
	}
	if _, err := provider.CurrentRevision(context.Background(), testOrganization, testWorkspaceTwo); !errors.Is(err, ErrRevisionNotAssigned) {
		t.Fatalf("empty project inventory admitted foreign workspace: %v", err)
	}
}

func TestProjectlessFenceClonePreservesNilEmptyAndIndependentProjects(t *testing.T) {
	for _, projects := range [][]string{nil, {}, {testProject}} {
		fence := testRevisionFence(17, "building")
		fence.ProjectIDs = projects
		cloned := cloneRevisionFence(fence)
		if (cloned.ProjectIDs == nil) != (projects == nil) || !sameStrings(cloned.ProjectIDs, projects) {
			t.Fatalf("clone changed project inventory representation: source=%#v clone=%#v", projects, cloned.ProjectIDs)
		}
		if len(cloned.ProjectIDs) > 0 {
			cloned.ProjectIDs[0] = testProjectTwo
			if fence.ProjectIDs[0] != testProject {
				t.Fatal("clone aliases the source inventory")
			}
		}
	}
}

func TestProjectlessFenceRejectsMissingNullAndNoncanonicalInventory(t *testing.T) {
	for _, replacement := range []string{
		``, `"project_ids":null,`, `"PROJECT_IDS":[],`, `"project_ids":[],"project_ids":[],`,
		`"project_ids":[ ],`, `"project_ids":{},`, `"project_ids":[null],`,
	} {
		t.Run(replacement, func(t *testing.T) {
			raw := strings.Replace(pythonProjectlessFenceV2Fixture, `"project_ids":[],`, replacement, 1)
			path := filepath.Join(t.TempDir(), "fence.json")
			if err := os.WriteFile(path, []byte(raw), 0o600); err != nil {
				t.Fatal(err)
			}
			provider, err := NewFileRevisionProvider(path)
			if err != nil {
				t.Fatal(err)
			}
			provider.now = projectlessNow
			if fences, err := provider.CurrentRevisions(context.Background()); err == nil {
				t.Fatalf("ambiguous inventory admitted: %+v", fences)
			}
		})
	}
}

func TestProjectlessScopeRetainsProjectAndTimeBounds(t *testing.T) {
	since, until := testSpanWindow()
	tooMany := make([]string, maxRevisionProjects+1)
	for index := range tooMany {
		tooMany[index] = fmt.Sprintf("00000000-0000-4000-8000-%012x", index+1)
	}
	for _, test := range []struct {
		name     string
		projects []string
		since    uint64
		until    uint64
	}{
		{"nil", nil, since, until},
		{"unsorted", []string{testProjectTwo, testProject}, since, until},
		{"duplicate", []string{testProject, testProject}, since, until},
		{"noncanonical UUID", []string{"AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA"}, since, until},
		{"missing UUID", []string{""}, since, until},
		{"too many", tooMany, since, until},
		{"zero start", []string{}, 0, until},
		{"zero end", []string{}, since, 0},
		{"empty window", []string{}, since, since},
		{"backwards window", []string{}, until, since},
	} {
		t.Run(test.name, func(t *testing.T) {
			if err := validateRevisionSourceScope(test.projects, test.since, test.until); err == nil {
				t.Fatal("invalid source scope accepted")
			}
		})
	}
}

func TestProjectlessBuildPlanRejectsAmbiguousAndForeignData(t *testing.T) {
	value, lease := projectlessBuildPlan(t)
	scope := `{"project_ids":[],"span_since_us":1786708800000000,"span_until_us":1786712400000000}`
	for _, test := range []struct{ name, old, replacement string }{
		{"missing projects", `"project_ids":[],`, ""},
		{"null projects", `"project_ids":[]`, `"project_ids":null`},
		{"project field alias", `"project_ids":[]`, `"PROJECT_IDS":[]`},
		{"conflicting field alias", `"project_ids":[]`, `"PROJECT_IDS":null,"project_ids":[]`},
		{"duplicate field", `"project_ids":[]`, `"project_ids":[],"project_ids":[]`},
		{"noncanonical whitespace", `"project_ids":[]`, `"project_ids":[ ]`},
		{"null scope", scope, "null"},
		{"missing scope", `"source_scope":` + scope + ",", ""},
		{"scope field alias", `"source_scope":`, `"SOURCE_SCOPE":`},
		{"foreign workspace", testWorkspace, testWorkspaceTwo},
		{"foreign organization", testOrganization, testWorkspaceTwo},
		{"foreign build", testBuildToken, testProject},
		{"foreign revision", `"catalog_revision":17`, `"catalog_revision":18`},
		{"foreign projection", `"projection_version":1`, `"projection_version":2`},
		{"missing declared stream", testStream, testProject},
	} {
		t.Run(test.name, func(t *testing.T) {
			changed := strings.Replace(value, test.old, test.replacement, 1)
			if changed == value {
				t.Fatal("test did not mutate the plan")
			}
			// Rehash so a digest mismatch cannot mask weakened scope validation.
			if evidence, err := validateBuildPlan(changed, testDigest(changed), validDeliveryLeaseRequest()); err == nil {
				t.Fatalf("invalid empty-scope plan accepted: %+v", evidence)
			}
		})
	}
	if _, err := validateBuildPlan(value, testDigest(lease), validDeliveryLeaseRequest()); err == nil {
		t.Fatal("empty scope bypassed exact build lease digest")
	}
}

func TestProjectlessLeaseGuardRequiresMatchingReservationAndStream(t *testing.T) {
	plan, lease := projectlessBuildPlan(t)
	reservation, stream := validBuildReservationRow(), validDeliveryLeaseRow()
	reservation.BuildPlanJSON, reservation.BuildLeaseSHA256 = plan, lease
	stream.BuildPlanJSON, stream.BuildLeaseSHA256 = plan, lease
	for _, test := range []struct {
		name string
		rows []deliveryLeaseJSON
	}{
		{"missing reservation", []deliveryLeaseJSON{stream}},
		{"missing stream", []deliveryLeaseJSON{reservation}},
		{"scope conflict", []deliveryLeaseJSON{reservation, validDeliveryLeaseRow()}},
		{"mutable scope", []deliveryLeaseJSON{reservation, stream, validDeliveryLeaseRow()}},
		{"foreign stream", func() []deliveryLeaseJSON {
			other := stream
			other.WorkspaceID = testWorkspaceTwo
			return []deliveryLeaseJSON{reservation, other}
		}()},
	} {
		t.Run(test.name, func(t *testing.T) {
			if evidence, err := validateDeliveryLeaseRows(validDeliveryLeaseRequest(), test.rows, projectlessNow()); err == nil {
				t.Fatalf("invalid reservation/stream evidence accepted: %+v", evidence)
			}
		})
	}
	loader := checkpointLoaderForResponse(t, deliveryLeaseResponse(t, reservation, stream), nil)
	loader.now = projectlessNow
	evidence, err := loader.AuthorizeDelivery(context.Background(), validDeliveryLeaseRequest())
	if err != nil || evidence.ProjectIDs == nil || len(evidence.ProjectIDs) != 0 || evidence.BuildLeaseSHA256 != lease {
		t.Fatalf("validated lease lost explicit empty scope: %+v err=%v", evidence, err)
	}
}

func TestProjectlessCheckpointRecoveryStillRequiresTenPhysicalStreamProofs(t *testing.T) {
	for _, failure := range []string{"", "missing stream", "foreign stream", "conflicting reservation", "missing checkpoint", "missing delivery", "conflicting delivery", "broken chain"} {
		t.Run(failure, func(t *testing.T) {
			plan, lease := projectlessBuildPlan(t)
			inventory := validCheckpointInventory(t, true)
			proofs := make(map[string]string, 10)
			for index := range inventory {
				row := &inventory[index]
				row.ReservationBuildPlanJSON, row.ReservationBuildLeaseSHA256 = plan, lease
				row.StreamBuildPlanJSON, row.StreamBuildLeaseSHA256 = plan, lease
				if row.StreamEnvelopeVersion != EnvelopeVersion {
					continue
				}
				// Empty backfill streams still have physical terminal receipts and
				// checkpoints; the hot stream still has its native Kafka terminal.
				row.StreamLastSequence, row.StreamMaxContiguousSequence = 1, 1
				row.StreamLastIssuedSequence, row.StreamFencedSequence = 1, 1
				row.CheckpointLastSequence, row.CheckpointLastIssuedSequence, row.CheckpointFencedSequence = 1, 1, 1
				proof := validCheckpointProof(*row, true)
				proof.SequenceRows, proof.LastSequence, proof.DistinctSequences, proof.TerminalSequence = 1, 1, 1, 1
				if row.StreamProducerStreamID != testStream {
					proof.PhysicalSnapshotSequences = 1
					proof.TailEnvelopeFormat = physicalSnapshotEnvelopeFormat
				}
				if index == 1 {
					switch failure {
					case "conflicting delivery":
						proof.ConflictSequences = 1
					case "broken chain":
						proof.ChainBreaks = 1
					}
				}
				proofs[row.StreamProducerStreamID] = checkpointBody(t, proof)
			}
			switch failure {
			case "missing stream":
				inventory = inventory[:len(inventory)-1]
			case "foreign stream":
				inventory[1].StreamProducerStreamID = testProject
			case "conflicting reservation":
				inventory[1].ReservationStateVariants = 2
			case "missing checkpoint":
				inventory[1].CheckpointEvidenceRows = 0
			case "missing delivery":
				proofs[inventory[1].StreamProducerStreamID] = ""
			}
			values := make([]any, len(inventory))
			for index, row := range inventory {
				values[index] = row
			}
			calls := 0
			seen := make(map[string]bool, 10)
			loader := checkpointLoaderForTransport(t, func(request *http.Request) (*http.Response, error) {
				calls++
				query := checkpointRequestStatement(t, request)
				if strings.Contains(query, "newest_reservation_revisions") {
					return checkpointHTTPResponse(checkpointBody(t, values...)), nil
				}
				if !strings.Contains(query, "FROM property_catalog_deliveries AS delivery") {
					t.Fatalf("expected physical stream proof, got %s", query)
				}
				streamID := request.URL.Query().Get("param_producer_stream_id")
				body, exists := proofs[streamID]
				if !exists || seen[streamID] {
					t.Fatalf("unexpected/duplicate stream proof: %s", streamID)
				}
				seen[streamID] = true
				return checkpointHTTPResponse(body), nil
			})
			checkpoints, err := loader.LoadCheckpoints(context.Background())
			if failure != "" {
				if err == nil || checkpoints != nil {
					t.Fatalf("incomplete/ambiguous physical evidence accepted: checkpoints=%+v err=%v", checkpoints, err)
				}
				return
			}
			if err != nil || len(checkpoints) != 10 || len(seen) != 10 || calls != 11 {
				t.Fatalf("projectless recovery skipped physical proof: checkpoints=%d proofs=%d calls=%d err=%v", len(checkpoints), len(seen), calls, err)
			}
			for _, checkpoint := range checkpoints {
				if checkpoint.Sequence != 1 || !checkpoint.Terminal || checkpoint.GapSeen {
					t.Fatalf("invalid empty terminal checkpoint: %+v", checkpoint)
				}
			}
		})
	}
}

func TestProjectlessFenceRejectsLiveSpanCandidatesBeforeSpooling(t *testing.T) {
	for _, version := range []uint16{CandidateVersion, CandidateManagedVersion} {
		t.Run(fmt.Sprint(version), func(t *testing.T) {
			runtime, provider, publisher := newCandidateV2Runtime(t, 3, 1)
			writeCandidateTestFence(t, runtime.cfg.RevisionFenceFile, projectlessFence())
			if fence, err := provider.CurrentRevision(context.Background(), testOrganization, testWorkspace); err != nil || fence.ProjectIDs == nil || len(fence.ProjectIDs) != 0 {
				t.Fatalf("test did not install a valid empty fence: %+v err=%v", fence, err)
			}
			cfg := candidateRuntimeConfig(t)
			if version == CandidateManagedVersion {
				cfg = managedCandidateConfig(t)
			}
			candidate := mustCandidates(t, cfg, []ScopedSpan{
				scopedHotRow(testWorkspace, testProject, hotRow(testProject, testSeen, map[string]string{"a": "one"}, nil)),
			})[0]
			duplicate, err := runtime.AcceptCandidate(candidate)
			var rejected *CandidateNotAdmittedError
			if duplicate || !errors.As(err, &rejected) || rejected.Reason != CandidateOutsideBuildSourceScope || rejected.ProjectID != testProject {
				t.Fatalf("empty fence admitted a live project: duplicate=%v err=%v", duplicate, err)
			}
			if pending, err := runtime.spool.PendingEnvelopes(); err != nil || len(pending) != 0 || len(publisher.envelopes) != 0 {
				t.Fatalf("rejected candidate created an ordered effect: pending=%d published=%d err=%v", len(pending), len(publisher.envelopes), err)
			}
		})
	}
}

func TestProjectlessHotDeliveryAllowsEmptyTerminalButRejectsLiveRow(t *testing.T) {
	for _, terminal := range []bool{false, true} {
		t.Run(fmt.Sprint(terminal), func(t *testing.T) {
			plan, lease := projectlessBuildPlan(t)
			reservation, stream := validBuildReservationRow(), validDeliveryLeaseRow()
			reservation.BuildPlanJSON, reservation.BuildLeaseSHA256 = plan, lease
			stream.BuildPlanJSON, stream.BuildLeaseSHA256 = plan, lease
			envelope := mustEnvelope(t, hotValueDeliveryInput(t, testValue()))
			if terminal {
				reservation.Status, stream.Status = "draining", "draining"
				stream.LastIssuedSequence, stream.FencedSequence = 1, 1
				var err error
				envelope, err = buildHotTerminalEnvelope(validRuntimeConfig(t).WithDefaults(), projectlessFence(), 1, ZeroSHA256)
				if err != nil {
					t.Fatal(err)
				}
			}
			loader := checkpointLoaderForResponse(t, deliveryLeaseResponse(t, reservation, stream), nil)
			loader.now = projectlessNow
			sink := &recordingSink{}
			handler, err := NewDeliveryHandler(sink, loader, time.Second)
			if err != nil {
				t.Fatal(err)
			}
			err = handler.Deliver(context.Background(), Delivery{
				Envelope: envelope, Transport: TransportKafka, KafkaPartition: 0, KafkaOffset: 0,
			})
			if !terminal {
				if err == nil || len(sink.calls) != 0 {
					t.Fatalf("empty scope admitted live hot data: writes=%v err=%v", sink.calls, err)
				}
				return
			}
			if err != nil || fmt.Sprint(sink.calls) != "[ledger]" {
				t.Fatalf("empty hot terminal lost physical delivery evidence: writes=%v err=%v", sink.calls, err)
			}
			row := sink.rows[0][0]
			if row["terminal"] != uint8(1) || row["build_token"] != testBuildToken ||
				row["catalog_revision"] != uint64(17) || row["sequence"] != uint64(1) ||
				row["envelope_id"] != envelope.Snapshot().EnvelopeID ||
				row["source_rows"] != uint64(0) || row["value_rows"] != uint64(0) {
				t.Fatalf("empty hot terminal ledger identity/counts drifted: %+v", row)
			}
		})
	}
}
