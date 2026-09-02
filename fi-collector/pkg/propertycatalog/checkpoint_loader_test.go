package propertycatalog

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"reflect"
	"strings"
	"testing"
	"time"
)

func checkpointJSONResponse(t *testing.T, rows ...any) string {
	t.Helper()
	var output bytes.Buffer
	encoder := json.NewEncoder(&output)
	encoder.SetEscapeHTML(false)
	for _, row := range rows {
		if err := encoder.Encode(row); err != nil {
			t.Fatal(err)
		}
	}
	return output.String()
}

func deliveryLeaseResponse(t *testing.T, rows ...deliveryLeaseJSON) string {
	t.Helper()
	var output bytes.Buffer
	encoder := json.NewEncoder(&output)
	encoder.SetEscapeHTML(false)
	for _, row := range rows {
		if err := encoder.Encode(row); err != nil {
			t.Fatal(err)
		}
	}
	return output.String()
}

func clickHouseJSONStringsResponse(t *testing.T, rows ...any) string {
	t.Helper()
	var output bytes.Buffer
	for _, row := range rows {
		encoded, err := json.Marshal(row)
		if err != nil {
			t.Fatal(err)
		}
		var object map[string]json.RawMessage
		if err := json.Unmarshal(encoded, &object); err != nil {
			t.Fatal(err)
		}
		rowType := reflect.TypeOf(row)
		if rowType.Kind() == reflect.Pointer {
			rowType = rowType.Elem()
		}
		for index := 0; index < rowType.NumField(); index++ {
			field := rowType.Field(index)
			if field.Type.Kind() != reflect.Uint64 {
				continue
			}
			name := strings.Split(field.Tag.Get("json"), ",")[0]
			raw, exists := object[name]
			if !exists {
				t.Fatalf("uint64 JSON field %q is absent", name)
			}
			quoted, err := json.Marshal(string(raw))
			if err != nil {
				t.Fatal(err)
			}
			object[name] = quoted
		}
		encoded, err = json.Marshal(object)
		if err != nil {
			t.Fatal(err)
		}
		output.Write(encoded)
		output.WriteByte('\n')
	}
	return output.String()
}

func deliveryLeaseResponseWithReservation(t *testing.T, rows ...deliveryLeaseJSON) string {
	t.Helper()
	return deliveryLeaseResponse(t, append([]deliveryLeaseJSON{validBuildReservationRow()}, rows...)...)
}

func validDeliveryLeaseRow() deliveryLeaseJSON {
	deadline := "2026-08-14 12:01:00.000000"
	plan, lease := validBuildPlan()
	return deliveryLeaseJSON{
		OrganizationID: testOrganization, WorkspaceID: testWorkspace,
		CatalogEpoch: 3, CatalogRevision: 17, BuildToken: testBuildToken,
		ProjectionVersion: 1, SourceAdapter: AdapterSpanAttribute,
		ProducerStreamID: testStream, EnvelopeVersion: EnvelopeVersion,
		LastIssuedSequence: 1, BuildPlanJSON: plan, BuildLeaseSHA256: lease,
		Status: "open", DrainDeadline: &deadline, Version: 1,
	}
}

func validBuildPlan() (string, string) {
	spanSinceUS, spanUntilUS := testSpanWindow()
	stream := func(adapter SourceAdapter, role, streamID string) map[string]any {
		return map[string]any{
			"producer_stream_id": streamID, "role": role, "source_adapter": adapter,
			"source_cutoff": map[string]any{"label": "source_version", "value": uint64(1)},
		}
	}
	plan := map[string]any{
		"build_token": testBuildToken, "catalog_epoch": uint16(3), "catalog_revision": uint64(17),
		"format": "futureagi.property-catalog-build-plan", "organization_id": testOrganization,
		"projection_version": uint16(1),
		"source_scope": map[string]any{
			"project_ids":   []string{testProject, testProjectTwo},
			"span_since_us": spanSinceUS, "span_until_us": spanUntilUS,
		},
		"streams": []any{
			stream(AdapterAnnotationLabel, "definitions", "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
			stream(AdapterDatasetColumn, "definitions", "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
			stream(AdapterEvalConfig, "definitions", "cccccccc-cccc-4ccc-8ccc-cccccccccccc"),
			stream(AdapterEvalTemplate, "definitions", "dddddddd-dddd-4ddd-8ddd-dddddddddddd"),
			stream(AdapterSimulationEvalConfig, "definitions", "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"),
			stream(AdapterSpanAttribute, "definitions", "11111111-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
			stream(AdapterSpanAttribute, "hot_values", testStream),
			stream(AdapterSpanAttribute, "source_audit", "66666666-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
			stream(AdapterSpanAttribute, "values", "77777777-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
			stream(AdapterSystemManifest, "definitions", "88888888-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
		},
		"version": uint16(2), "workspace_id": testWorkspace,
	}
	raw, err := json.Marshal(plan)
	if err != nil {
		panic(err)
	}
	digest := sha256.Sum256(raw)
	return string(raw), fmt.Sprintf("%x", digest)
}

func validBuildReservationRow() deliveryLeaseJSON {
	row := validDeliveryLeaseRow()
	row.SourceAdapter = AdapterSystemManifest
	row.ProducerStreamID = testBuildToken
	row.EnvelopeVersion = 0
	row.LastIssuedSequence = 0
	return row
}

func withBuildReservation(rows ...deliveryLeaseJSON) []deliveryLeaseJSON {
	return append([]deliveryLeaseJSON{validBuildReservationRow()}, rows...)
}

func validDeliveryLeaseRequest() DeliveryLeaseRequest {
	return DeliveryLeaseRequest{
		OrganizationID: testOrganization, WorkspaceID: testWorkspace,
		CatalogEpoch: 3, CatalogRevision: 17, BuildToken: testBuildToken,
		ProjectionVersion: 1, SourceAdapter: AdapterSpanAttribute,
		ProducerStreamID: testStream, EnvelopeVersion: EnvelopeVersion, Sequence: 1,
	}
}

func checkpointLoaderForResponse(t *testing.T, body string, inspect func(*http.Request)) *ClickHouseCheckpointLoader {
	t.Helper()
	transport := roundTripFunc(func(request *http.Request) (*http.Response, error) {
		if inspect != nil {
			inspect(request)
		}
		return &http.Response{
			StatusCode: http.StatusOK, Header: make(http.Header), Body: io.NopCloser(strings.NewReader(body)),
		}, nil
	})
	loader, err := NewClickHouseCheckpointLoader(ClickHouseSinkConfig{
		URL: "http://clickhouse:8123", Database: "property_catalog_dev_checkpoint_test",
		Environment: DevelopmentEnvironment,
		Username:    "ledger_reader", Password: "secret", RequestTimeout: time.Second,
		RoundTripper: transport,
	})
	if err != nil {
		t.Fatal(err)
	}
	return loader
}

func checkpointLoaderForTransport(
	t *testing.T, transport func(*http.Request) (*http.Response, error),
) *ClickHouseCheckpointLoader {
	t.Helper()
	loader, err := NewClickHouseCheckpointLoader(ClickHouseSinkConfig{
		URL: "http://clickhouse:8123", Database: "property_catalog_dev_checkpoint_test",
		Environment: DevelopmentEnvironment,
		Username:    "ledger_reader", Password: "secret", RequestTimeout: time.Second,
		RoundTripper: roundTripFunc(transport),
	})
	if err != nil {
		t.Fatal(err)
	}
	return loader
}

func checkpointHTTPResponse(body string) *http.Response {
	return &http.Response{
		StatusCode: http.StatusOK, Header: make(http.Header),
		Body: io.NopCloser(strings.NewReader(body)),
	}
}

func checkpointRequestStatement(t *testing.T, request *http.Request) string {
	t.Helper()
	if value := request.URL.Query().Get("query"); value != "" {
		return value
	}
	body, err := io.ReadAll(request.Body)
	if err != nil {
		t.Fatal(err)
	}
	return string(body)
}

func validCheckpointInventory(t *testing.T, terminal bool) []checkpointInventoryJSON {
	t.Helper()
	planJSON, lease := validBuildPlan()
	var plan buildPlanDocumentJSON
	if err := json.Unmarshal([]byte(planJSON), &plan); err != nil {
		t.Fatal(err)
	}
	reservationStatus := "open"
	if terminal {
		reservationStatus = "fenced"
	}
	base := checkpointInventoryJSON{
		OrganizationID: testOrganization, WorkspaceID: testWorkspace,
		CatalogEpoch: 3, CatalogRevision: 17, BuildToken: testBuildToken,
		ReservationProjectionVersion: 1, ReservationSourceAdapter: AdapterSystemManifest,
		ReservationProducerStreamID: testBuildToken, ReservationEnvelopeVersion: 0,
		ReservationBuildPlanJSON: planJSON, ReservationBuildLeaseSHA256: lease,
		ReservationStatus: reservationStatus, ReservationVersion: 1, ReservationStateVariants: 1,
	}
	reservation := base
	reservation.StreamEvidenceRows = 1
	reservation.StreamProjectionVersion = 1
	reservation.StreamSourceAdapter = AdapterSystemManifest
	reservation.StreamProducerStreamID = testBuildToken
	reservation.StreamEnvelopeVersion = 0
	reservation.StreamTerminalPayloadSHA256 = ZeroSHA256
	reservation.StreamBuildPlanJSON = planJSON
	reservation.StreamBuildLeaseSHA256 = lease
	reservation.StreamStatus = reservationStatus
	reservation.StreamVersion = 1
	reservation.StreamStateVariants = 1
	rows := []checkpointInventoryJSON{reservation}
	for _, stream := range plan.Streams {
		row := base
		row.StreamEvidenceRows = 1
		row.StreamProjectionVersion = 1
		row.StreamSourceAdapter = stream.SourceAdapter
		row.StreamProducerStreamID = stream.ProducerStreamID
		row.StreamEnvelopeVersion = EnvelopeVersion
		row.StreamTerminalPayloadSHA256 = ZeroSHA256
		row.StreamBuildPlanJSON = planJSON
		row.StreamBuildLeaseSHA256 = lease
		row.StreamStatus = "open"
		row.StreamVersion = 1
		row.StreamStateVariants = 1
		if terminal {
			payload := testDigest("checkpoint-tail-" + stream.ProducerStreamID)
			row.StreamFirstSequence = 1
			row.StreamLastSequence = 3
			row.StreamMaxContiguousSequence = 3
			row.StreamLastIssuedSequence = 3
			row.StreamFencedSequence = 3
			row.StreamTerminalPayloadSHA256 = payload
			row.StreamStatus = "complete"
			row.CheckpointEvidenceRows = 1
			row.CheckpointProjectionVersion = 1
			row.CheckpointStatus = "complete"
			row.CheckpointTerminal = 1
			row.CheckpointFirstSequence = 1
			row.CheckpointLastSequence = 3
			row.CheckpointLastIssuedSequence = 3
			row.CheckpointFencedSequence = 3
			row.CheckpointTerminalPayloadSHA256 = payload
			row.CheckpointVersion = 1
			row.CheckpointStateVariants = 1
			row.ActivationEvidenceRows = 1
			row.ActivationProjectionVersion = 1
			row.ActivationStatus = "active"
			row.ActivationVersion = 1
			row.ActivationStateVariants = 1
		}
		rows = append(rows, row)
	}
	return rows
}

func validCheckpointProof(row checkpointInventoryJSON, terminal bool) checkpointStreamProofJSON {
	payload := testDigest("checkpoint-tail-" + row.StreamProducerStreamID)
	proof := checkpointStreamProofJSON{
		SequenceRows: 3, FirstSequence: 1, LastSequence: 3, DistinctSequences: 3,
		ProjectionVersions: 1, MaxProjectionVersionsAtSequence: 1, MaxIdentityVariants: 1,
		TailProjectionVersion: 1, TailEnvelopeFormat: EnvelopeFormat,
		TailEnvelopeVersion: EnvelopeVersion, TailEnvelopeID: testDigest("checkpoint-envelope-" + row.StreamProducerStreamID),
		TailPayloadSHA256: payload,
	}
	if terminal {
		proof.TerminalSequences = 1
		proof.TerminalSequence = 3
		proof.TailTerminal = 1
	}
	return proof
}

func checkpointBody(t *testing.T, rows ...any) string {
	t.Helper()
	return checkpointJSONResponse(t, rows...)
}

func TestCheckpointLoaderUsesNewestBoundedInventoryAndConstantStreamProofs(t *testing.T) {
	inventory := validCheckpointInventory(t, false)
	inventoryValues := make([]any, len(inventory))
	proofs := make(map[string]string, len(inventory)-1)
	for index, row := range inventory {
		inventoryValues[index] = row
		if row.StreamEnvelopeVersion == EnvelopeVersion {
			proofs[string(row.StreamSourceAdapter)+"\x00"+row.StreamProducerStreamID] =
				checkpointBody(t, validCheckpointProof(row, false))
		}
	}
	calls := 0
	loader := checkpointLoaderForTransport(t, func(request *http.Request) (*http.Response, error) {
		calls++
		if username, password, ok := request.BasicAuth(); !ok || username != "ledger_reader" || password != "secret" {
			t.Fatalf("basic auth=%q/%q ok=%v", username, password, ok)
		}
		values := request.URL.Query()
		query := checkpointRequestStatement(t, request)
		if values.Get("query") != "" || request.Header.Get("Content-Type") != "text/plain; charset=utf-8" {
			t.Fatalf("checkpoint SQL was not carried in the bounded POST body")
		}
		switch {
		case strings.Contains(query, "newest_reservation_revisions"):
			if !strings.Contains(query, "property_catalog_source_streams") ||
				!strings.Contains(query, "property_catalog_checkpoints") ||
				!strings.Contains(query, "property_catalog_activations") ||
				!strings.Contains(query, "FROM latest_source_rows AS raw") ||
				!strings.Contains(query, "uniqExact(tuple(\n      raw.projection_version") ||
				!strings.Contains(query, "FROM latest_checkpoint_rows AS raw") ||
				!strings.Contains(query, "FROM latest_activation_rows AS raw") ||
				!strings.Contains(query, "WHERE _version = latest_version") ||
				!strings.Contains(query, "reservation.state_variants") ||
				!strings.Contains(query, "LIMIT {inventory_limit:UInt64}") ||
				strings.Contains(query, "FROM property_catalog_deliveries") ||
				strings.Contains(query, "status = 'active'") {
				t.Fatalf("inventory query is not newest-reservation anchored and conflict-visible: %s", query)
			}
			wantLimit := fmt.Sprintf("%d", DefaultCheckpointMaxStreams+1)
			if values.Get("max_result_rows") != wantLimit ||
				values.Get("param_inventory_limit") != wantLimit ||
				values.Get("max_execution_time") != "10" {
				t.Fatalf("inventory bounds=%v", values)
			}
			return checkpointHTTPResponse(checkpointBody(t, inventoryValues...)), nil
		case strings.Contains(query, "lagInFrame"):
			if !strings.Contains(query, "FROM property_catalog_deliveries AS delivery") ||
				!strings.Contains(query, "GROUP BY delivery.sequence") ||
				!strings.Contains(query, "uniqExact(delivery.projection_version)") ||
				!strings.Contains(query, "uniqExact(tuple(") ||
				!strings.Contains(query, "chain_breaks") ||
				strings.Contains(query, "LIMIT 100001") ||
				values.Get("max_result_rows") != "2" ||
				values.Get("max_rows_to_group_by") != "100001" ||
				values.Get("group_by_overflow_mode") != "throw" {
				t.Fatalf("stream proof is not a bounded server-side full-chain reduction: %s %v", query, values)
			}
			for name, want := range map[string]string{
				"param_organization_id": testOrganization, "param_workspace_id": testWorkspace,
				"param_catalog_epoch": "3", "param_catalog_revision": "17",
				"param_build_token": testBuildToken, "param_envelope_format": EnvelopeFormat,
				"param_envelope_version": "1", "param_zero_sha256": ZeroSHA256,
			} {
				if values.Get(name) != want {
					t.Fatalf("stream proof %s=%q want=%q", name, values.Get(name), want)
				}
			}
			body, exists := proofs[values.Get("param_source_adapter")+"\x00"+values.Get("param_producer_stream_id")]
			if !exists {
				t.Fatalf("unexpected proof scope: %v", values)
			}
			return checkpointHTTPResponse(body), nil
		default:
			t.Fatalf("unexpected checkpoint query: %s", query)
			return nil, nil
		}
	})
	checkpoints, err := loader.LoadCheckpoints(context.Background())
	if err != nil || len(checkpoints) != 10 || calls != 11 {
		t.Fatalf("checkpoints=%+v calls=%d err=%v", checkpoints, calls, err)
	}
	for _, checkpoint := range checkpoints {
		if checkpoint.Sequence != 3 || checkpoint.Terminal || checkpoint.GapSeen ||
			checkpoint.PayloadSHA256 != testDigest("checkpoint-tail-"+checkpoint.ProducerStreamID) {
			t.Fatalf("checkpoint did not preserve exact aggregate tail: %+v", checkpoint)
		}
	}
}

func TestCheckpointLoaderRestartReconstructsSameTerminalRevision(t *testing.T) {
	inventory := validCheckpointInventory(t, true)
	values := make([]any, len(inventory))
	proofs := make(map[string]string, 10)
	for index, row := range inventory {
		values[index] = row
		if row.StreamEnvelopeVersion == EnvelopeVersion {
			proofs[string(row.StreamSourceAdapter)+"\x00"+row.StreamProducerStreamID] =
				checkpointBody(t, validCheckpointProof(row, true))
		}
	}
	loader := checkpointLoaderForTransport(t, func(request *http.Request) (*http.Response, error) {
		query := checkpointRequestStatement(t, request)
		if strings.Contains(query, "newest_reservation_revisions") {
			return checkpointHTTPResponse(checkpointBody(t, values...)), nil
		}
		key := request.URL.Query().Get("param_source_adapter") + "\x00" +
			request.URL.Query().Get("param_producer_stream_id")
		return checkpointHTTPResponse(proofs[key]), nil
	})
	first, err := loader.LoadCheckpoints(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	second, err := loader.LoadCheckpoints(context.Background())
	if err != nil || len(first) != 10 || len(second) != 10 {
		t.Fatalf("restart checkpoints first=%d second=%d err=%v", len(first), len(second), err)
	}
	for index := range first {
		if first[index] != second[index] || !first[index].Terminal {
			t.Fatalf("restart reconstruction drifted at %d: %+v %+v", index, first[index], second[index])
		}
	}
}

func TestCheckpointLoaderRejectsConflictCrowdingAndBrokenAggregateProofs(t *testing.T) {
	row := validCheckpointInventory(t, false)[1]
	valid := validCheckpointProof(row, false)
	tests := []struct {
		name   string
		mutate func(*checkpointStreamProofJSON)
	}{
		{"missing root or gap", func(proof *checkpointStreamProofJSON) { proof.FirstSequence = 2 }},
		{"distinct sequence crowding", func(proof *checkpointStreamProofJSON) { proof.DistinctSequences = 2 }},
		{"same sequence identity conflict", func(proof *checkpointStreamProofJSON) {
			proof.MaxIdentityVariants, proof.ConflictSequences = 2, 1
		}},
		{"same sequence projection conflict", func(proof *checkpointStreamProofJSON) {
			proof.MaxProjectionVersionsAtSequence = 2
		}},
		{"cross sequence projection drift", func(proof *checkpointStreamProofJSON) { proof.ProjectionVersions = 2 }},
		{"hash chain break", func(proof *checkpointStreamProofJSON) { proof.ChainBreaks = 1 }},
		{"invalid wire", func(proof *checkpointStreamProofJSON) { proof.InvalidWireSequences = 1 }},
		{"invalid outcome", func(proof *checkpointStreamProofJSON) { proof.InvalidOutcomeSequences = 1 }},
		{"terminal carries rows", func(proof *checkpointStreamProofJSON) { proof.InvalidTerminalSequences = 1 }},
		{"early terminal", func(proof *checkpointStreamProofJSON) {
			proof.TerminalSequences, proof.TerminalSequence = 1, 2
		}},
		{"multiple terminals", func(proof *checkpointStreamProofJSON) { proof.TerminalSequences = 2 }},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			proof := valid
			test.mutate(&proof)
			if checkpoint, err := validateCheckpointStreamProof(row, proof); err == nil {
				t.Fatalf("broken aggregate proof accepted: %+v", checkpoint)
			}
		})
	}
	conflictingInventory := validCheckpointInventory(t, false)
	conflictingInventory[0].ReservationStateVariants = 2
	if candidates, err := validateCheckpointInventory(conflictingInventory); err == nil {
		t.Fatalf("same-version reservation conflict accepted: %+v", candidates)
	}
	competingReservation := validCheckpointInventory(t, false)
	competitor := competingReservation[0]
	competitor.BuildToken = "99999999-9999-4999-8999-999999999999"
	competitor.ReservationProducerStreamID = competitor.BuildToken
	competitor.StreamProducerStreamID = competitor.BuildToken
	competingReservation = append(competingReservation, competitor)
	if candidates, err := validateCheckpointInventory(competingReservation); err == nil {
		t.Fatalf("same-scope competing newest reservation accepted: %+v", candidates)
	}
}

func TestCheckpointLoaderCapsInventoryBeforeConflictCanBeCrowdedOut(t *testing.T) {
	row := validCheckpointInventory(t, false)[0]
	rows := make([]any, 3)
	for index := range rows {
		rows[index] = row
	}
	proofCalls := 0
	loader := checkpointLoaderForTransport(t, func(request *http.Request) (*http.Response, error) {
		if strings.Contains(checkpointRequestStatement(t, request), "lagInFrame") {
			proofCalls++
		}
		return checkpointHTTPResponse(checkpointBody(t, rows...)), nil
	})
	loader.maxStreams = 2
	if checkpoints, err := loader.LoadCheckpoints(context.Background()); err == nil ||
		!strings.Contains(err.Error(), "exceeds row limit") || proofCalls != 0 {
		t.Fatalf("crowded inventory checkpoints=%+v proof_calls=%d err=%v", checkpoints, proofCalls, err)
	}
}

func TestCheckpointLoaderPreservesProvenEmptyBootstrapAndRejectsOrphans(t *testing.T) {
	for _, test := range []struct {
		name      string
		probeBody string
		wantError bool
	}{
		{"empty", "", false},
		{"orphan delivery", checkpointBody(t, checkpointLedgerProbeJSON{Present: 1}), true},
	} {
		t.Run(test.name, func(t *testing.T) {
			calls := 0
			loader := checkpointLoaderForTransport(t, func(request *http.Request) (*http.Response, error) {
				calls++
				query := checkpointRequestStatement(t, request)
				if calls == 1 && !strings.Contains(query, "newest_reservation_revisions") {
					t.Fatalf("first query was not inventory: %s", query)
				}
				if calls == 2 && (!strings.Contains(query, "SELECT 1 AS present") ||
					request.URL.Query().Get("max_result_rows") != "2") {
					t.Fatalf("second query was not bounded orphan probe: %s", query)
				}
				if calls == 1 {
					return checkpointHTTPResponse(""), nil
				}
				return checkpointHTTPResponse(test.probeBody), nil
			})
			checkpoints, err := loader.LoadCheckpoints(context.Background())
			if calls != 2 || (err != nil) != test.wantError || (!test.wantError && (checkpoints == nil || len(checkpoints) != 0)) {
				t.Fatalf("checkpoints=%+v calls=%d err=%v", checkpoints, calls, err)
			}
		})
	}
}

func TestCheckpointLoaderAcceptsExactEmptyNewestRevisionOverOlderLedgerHistory(t *testing.T) {
	inventory := validCheckpointInventory(t, false)
	values := make([]any, len(inventory))
	for index, row := range inventory {
		values[index] = row
	}
	calls := 0
	loader := checkpointLoaderForTransport(t, func(request *http.Request) (*http.Response, error) {
		calls++
		query := checkpointRequestStatement(t, request)
		switch {
		case strings.Contains(query, "newest_reservation_revisions"):
			return checkpointHTTPResponse(checkpointBody(t, values...)), nil
		case strings.Contains(query, "lagInFrame"):
			return checkpointHTTPResponse(""), nil
		case strings.Contains(query, "SELECT 1 AS present"):
			t.Fatal("proven-empty newest stream inventory incorrectly probed older ledger history")
			return nil, nil
		default:
			t.Fatalf("unexpected checkpoint query: %s", query)
			return nil, nil
		}
	})
	checkpoints, err := loader.LoadCheckpoints(context.Background())
	if err != nil || len(checkpoints) != 0 || calls != 11 {
		t.Fatalf("empty newest revision checkpoints=%+v calls=%d err=%v", checkpoints, calls, err)
	}
}

func TestEmptyCheckpointStreamAcceptsOnlyPreissuedFirstDrainFence(t *testing.T) {
	row := validCheckpointInventory(t, false)[1]
	row.StreamStatus = "draining"
	row.StreamLastIssuedSequence = 1
	row.StreamFencedSequence = 1
	if err := validateEmptyCheckpointStream(row); err != nil {
		t.Fatalf("exact preissued first drain fence rejected: %v", err)
	}

	for _, test := range []struct {
		name   string
		mutate func(*checkpointInventoryJSON)
	}{
		{"open", func(value *checkpointInventoryJSON) { value.StreamStatus = "open" }},
		{"later sequence", func(value *checkpointInventoryJSON) {
			value.StreamLastIssuedSequence = 2
			value.StreamFencedSequence = 2
		}},
		{"partial fence", func(value *checkpointInventoryJSON) { value.StreamFencedSequence = 0 }},
		{"gap", func(value *checkpointInventoryJSON) { value.StreamGapCount = 1 }},
		{"completed checkpoint", func(value *checkpointInventoryJSON) {
			value.CheckpointEvidenceRows = 1
			value.CheckpointProjectionVersion = 1
			value.CheckpointStatus = "complete"
			value.CheckpointNullFirstSequences = 1
			value.CheckpointNullLastSequences = 1
			value.CheckpointVersion = 1
			value.CheckpointStateVariants = 1
		}},
		{"active", func(value *checkpointInventoryJSON) {
			value.ActivationStatus = "active"
			value.ActivationEvidenceRows = 1
		}},
	} {
		t.Run(test.name, func(t *testing.T) {
			candidate := row
			test.mutate(&candidate)
			if err := validateEmptyCheckpointStream(candidate); err == nil {
				t.Fatal("unsafe empty source-stream state accepted")
			}
		})
	}
}

func TestDeliveryLeaseGuardReadsExactBoundedManifestStreamLease(t *testing.T) {
	row := validDeliveryLeaseRow()
	called := 0
	loader := checkpointLoaderForResponse(t, deliveryLeaseResponseWithReservation(t, row), func(request *http.Request) {
		called++
		query := request.URL.Query().Get("query")
		if !strings.Contains(query, "FROM property_catalog_source_streams") ||
			!strings.Contains(query, "build_plan_json") || !strings.Contains(query, "build_lease_sha256") ||
			!strings.Contains(query, "envelope_version = 0") ||
			!strings.Contains(query, "producer_stream_id = build_token") || !strings.Contains(query, "LIMIT 33") ||
			strings.Contains(query, "FROM spans") || strings.Contains(query, "property_definition_catalog") ||
			strings.Contains(query, "span_attribute_value_catalog") {
			t.Fatalf("lease query escaped or omitted source-stream evidence: %s", query)
		}
		values := request.URL.Query()
		for name, want := range map[string]string{
			"database": "property_catalog_dev_checkpoint_test", "param_organization_id": testOrganization,
			"param_workspace_id": testWorkspace, "param_catalog_epoch": "3",
			"param_catalog_revision": "17", "param_build_token": testBuildToken,
			"param_source_adapter": string(AdapterSpanAttribute), "param_producer_stream_id": testStream,
			"max_execution_time": "2", "max_result_rows": "33", "result_overflow_mode": "throw",
		} {
			if got := values.Get(name); got != want {
				t.Fatalf("lease query %s=%q want=%q", name, got, want)
			}
		}
	})
	loader.now = func() time.Time { return time.Date(2026, 8, 14, 12, 0, 0, 0, time.UTC) }
	evidence, err := loader.AuthorizeDelivery(context.Background(), validDeliveryLeaseRequest())
	if err != nil || called != 1 || evidence.BuildLeaseSHA256 != row.BuildLeaseSHA256 ||
		evidence.StreamRole != "hot_values" ||
		!sameStrings(evidence.ProjectIDs, []string{testProject, testProjectTwo}) ||
		evidence.SpanSinceUS == 0 || evidence.SpanUntilUS <= evidence.SpanSinceUS {
		t.Fatalf("authorization evidence=%+v err=%v called=%d", evidence, err, called)
	}
}

func TestDeliveryLeaseGuardAcceptsClickHouseJSONStringsUInt64s(t *testing.T) {
	reservation := validBuildReservationRow()
	stream := validDeliveryLeaseRow()
	loader := checkpointLoaderForResponse(
		t, clickHouseJSONStringsResponse(t, reservation, stream), nil,
	)
	loader.now = func() time.Time {
		return time.Date(2026, 8, 14, 12, 0, 0, 0, time.UTC)
	}

	evidence, err := loader.AuthorizeDelivery(
		context.Background(), validDeliveryLeaseRequest(),
	)
	if err != nil || evidence.BuildLeaseSHA256 != stream.BuildLeaseSHA256 ||
		evidence.StreamRole != "hot_values" {
		t.Fatalf("JSONStrings authorization evidence=%+v err=%v", evidence, err)
	}
}

func TestCheckpointJSONAcceptsQuotedUInt64sAcrossLoaderRows(t *testing.T) {
	lease := validDeliveryLeaseRow()
	inventory := validCheckpointInventory(t, true)[1]
	proof := validCheckpointProof(inventory, true)
	for _, test := range []struct {
		name string
		row  any
		new  func() any
	}{
		{"delivery lease", lease, func() any { return &deliveryLeaseJSON{} }},
		{"checkpoint inventory", inventory, func() any { return &checkpointInventoryJSON{} }},
		{"checkpoint proof", proof, func() any { return &checkpointStreamProofJSON{} }},
	} {
		t.Run(test.name, func(t *testing.T) {
			destination := test.new()
			line := strings.TrimSpace(clickHouseJSONStringsResponse(t, test.row))
			if err := decodeCheckpointJSON([]byte(line), destination); err != nil {
				t.Fatalf("quoted UInt64 row was rejected: %v", err)
			}
			if !reflect.DeepEqual(reflect.ValueOf(destination).Elem().Interface(), test.row) {
				t.Fatalf("decoded row drifted: got=%+v want=%+v", destination, test.row)
			}
		})
	}
}

func TestCheckpointJSONRejectsNoncanonicalUInt64s(t *testing.T) {
	type uint64Row struct {
		Value uint64 `json:"value"`
	}
	for _, valid := range []string{
		`{"value":0}`,
		`{"value":"0"}`,
		`{"value":18446744073709551615}`,
		`{"value":"18446744073709551615"}`,
	} {
		var row uint64Row
		if err := decodeCheckpointJSON([]byte(valid), &row); err != nil {
			t.Fatalf("valid UInt64 %s rejected: %v", valid, err)
		}
	}
	for _, invalid := range []string{
		`{"value":-1}`,
		`{"value":1.0}`,
		`{"value":1e0}`,
		`{"value":18446744073709551616}`,
		`{"value":"-1"}`,
		`{"value":"01"}`,
		`{"value":"1.0"}`,
		`{"value":"1e0"}`,
		`{"value":"18446744073709551616"}`,
		`{"value":"1\\u0030"}`,
		`{"value":null}`,
	} {
		var row uint64Row
		if err := decodeCheckpointJSON([]byte(invalid), &row); err == nil {
			t.Fatalf("noncanonical UInt64 %s was accepted as %d", invalid, row.Value)
		}
	}
}

func TestDeliveryLeaseGuardRequiresCanonicalBuildPlanV2SourceScope(t *testing.T) {
	for _, test := range []struct {
		name   string
		mutate func(map[string]any)
	}{
		{
			name:   "stale v1",
			mutate: func(plan map[string]any) { plan["version"] = float64(1) },
		},
		{
			name:   "missing source scope",
			mutate: func(plan map[string]any) { delete(plan, "source_scope") },
		},
		{
			name: "unsorted projects",
			mutate: func(plan map[string]any) {
				plan["source_scope"].(map[string]any)["project_ids"] = []any{testProjectTwo, testProject}
			},
		},
		{
			name: "duplicate projects",
			mutate: func(plan map[string]any) {
				plan["source_scope"].(map[string]any)["project_ids"] = []any{testProject, testProject}
			},
		},
		{
			name: "empty half-open window",
			mutate: func(plan map[string]any) {
				scope := plan["source_scope"].(map[string]any)
				scope["span_until_us"] = scope["span_since_us"]
			},
		},
		{
			name: "unknown scope field",
			mutate: func(plan map[string]any) {
				plan["source_scope"].(map[string]any)["workspace_mode"] = true
			},
		},
	} {
		t.Run(test.name, func(t *testing.T) {
			valid, _ := validBuildPlan()
			var plan map[string]any
			if err := json.Unmarshal([]byte(valid), &plan); err != nil {
				t.Fatal(err)
			}
			test.mutate(plan)
			raw, err := json.Marshal(plan)
			if err != nil {
				t.Fatal(err)
			}
			digest := sha256.Sum256(raw)
			if evidence, err := validateBuildPlan(
				string(raw), fmt.Sprintf("%x", digest), validDeliveryLeaseRequest(),
			); err == nil {
				t.Fatalf("unsafe plan accepted: %+v", evidence)
			}
		})
	}
}

func TestDrainIntentAllowsOnlyPreIssuedNonTerminalDelivery(t *testing.T) {
	stream := validDeliveryLeaseRow()
	stream.Status = "draining"
	stream.LastIssuedSequence = 0
	stream.FencedSequence = 0
	reservation := validBuildReservationRow()
	reservation.Status = "draining"
	request := validDeliveryLeaseRequest()
	evidence, err := validateDeliveryLeaseRows(
		request, []deliveryLeaseJSON{reservation, stream},
		time.Date(2026, 8, 14, 12, 0, 0, 0, time.UTC),
	)
	if err != nil || evidence.StreamRole != "hot_values" {
		t.Fatalf("intent non-terminal evidence=%+v err=%v", evidence, err)
	}
	request.Terminal = true
	if _, err := validateDeliveryLeaseRows(
		request, []deliveryLeaseJSON{reservation, stream},
		time.Date(2026, 8, 14, 12, 0, 0, 0, time.UTC),
	); err == nil {
		t.Fatal("drain intent accepted a terminal before exact boundary binding")
	}
}

func TestDeliveryLeaseGuardRejectsExpiredMismatchedAndConflictingEvidence(t *testing.T) {
	now := time.Date(2026, 8, 14, 12, 0, 0, 0, time.UTC)
	for _, test := range []struct {
		name string
		rows []deliveryLeaseJSON
	}{
		{name: "missing", rows: nil},
		{name: "missing reservation", rows: []deliveryLeaseJSON{validDeliveryLeaseRow()}},
		{name: "expired", rows: func() []deliveryLeaseJSON {
			row := validDeliveryLeaseRow()
			deadline := "2026-08-14 12:00:00.000000"
			row.DrainDeadline = &deadline
			return withBuildReservation(row)
		}()},
		{name: "mismatched scope", rows: func() []deliveryLeaseJSON {
			row := validDeliveryLeaseRow()
			row.WorkspaceID = testWorkspaceTwo
			return withBuildReservation(row)
		}()},
		{name: "invalid build lease", rows: func() []deliveryLeaseJSON {
			row := validDeliveryLeaseRow()
			row.BuildLeaseSHA256 = strings.Repeat("z", 64)
			return withBuildReservation(row)
		}()},
		{name: "fenced", rows: func() []deliveryLeaseJSON {
			row := validDeliveryLeaseRow()
			row.Status = "fenced"
			return withBuildReservation(row)
		}()},
		{name: "complete", rows: func() []deliveryLeaseJSON {
			row := validDeliveryLeaseRow()
			row.Status = "complete"
			reservation := validBuildReservationRow()
			reservation.Status = "complete"
			return []deliveryLeaseJSON{reservation, row}
		}()},
		{name: "conflicting latest build lease", rows: func() []deliveryLeaseJSON {
			left := validDeliveryLeaseRow()
			right := left
			left.Version, right.Version = 2, 2
			right.BuildLeaseSHA256 = testDigest("other-build-lease")
			return withBuildReservation(left, right)
		}()},
		{name: "reservation stream mismatch", rows: func() []deliveryLeaseJSON {
			reservation := validBuildReservationRow()
			stream := validDeliveryLeaseRow()
			stream.BuildLeaseSHA256 = testDigest("other-build-lease")
			return []deliveryLeaseJSON{reservation, stream}
		}()},
		{name: "stream lease mutates in history", rows: func() []deliveryLeaseJSON {
			old := validDeliveryLeaseRow()
			current := old
			current.Version = 2
			current.BuildLeaseSHA256 = testDigest("other-build-lease")
			return []deliveryLeaseJSON{validBuildReservationRow(), old, current}
		}()},
		{name: "noncanonical build plan", rows: func() []deliveryLeaseJSON {
			stream := validDeliveryLeaseRow()
			stream.BuildPlanJSON += " "
			digest := sha256.Sum256([]byte(stream.BuildPlanJSON))
			stream.BuildLeaseSHA256 = fmt.Sprintf("%x", digest)
			reservation := validBuildReservationRow()
			reservation.BuildPlanJSON, reservation.BuildLeaseSHA256 = stream.BuildPlanJSON, stream.BuildLeaseSHA256
			return []deliveryLeaseJSON{reservation, stream}
		}()},
		{name: "current stream absent from plan", rows: func() []deliveryLeaseJSON {
			stream := validDeliveryLeaseRow()
			stream.BuildPlanJSON = strings.Replace(
				stream.BuildPlanJSON, testStream, "99999999-9999-4999-8999-999999999999", 1,
			)
			digest := sha256.Sum256([]byte(stream.BuildPlanJSON))
			stream.BuildLeaseSHA256 = fmt.Sprintf("%x", digest)
			reservation := validBuildReservationRow()
			reservation.BuildPlanJSON, reservation.BuildLeaseSHA256 = stream.BuildPlanJSON, stream.BuildLeaseSHA256
			return []deliveryLeaseJSON{reservation, stream}
		}()},
	} {
		t.Run(test.name, func(t *testing.T) {
			loader := checkpointLoaderForResponse(t, deliveryLeaseResponse(t, test.rows...), nil)
			loader.now = func() time.Time { return now }
			if _, err := loader.AuthorizeDelivery(context.Background(), validDeliveryLeaseRequest()); err == nil {
				t.Fatal("unsafe lease evidence was accepted")
			}
		})
	}
}

func TestDeliveryLeaseGuardAllowsOnlyExactDrainingHighWater(t *testing.T) {
	row := validDeliveryLeaseRow()
	row.Status = "draining"
	row.LastIssuedSequence = 2
	row.FencedSequence = 2
	reservation := validBuildReservationRow()
	reservation.Status = "draining"
	loader := checkpointLoaderForResponse(t, deliveryLeaseResponse(t, reservation, row), nil)
	loader.now = func() time.Time { return time.Date(2026, 8, 14, 12, 0, 0, 0, time.UTC) }
	request := validDeliveryLeaseRequest()
	request.Sequence, request.Terminal = 2, true
	if _, err := loader.AuthorizeDelivery(context.Background(), request); err != nil {
		t.Fatalf("exact terminal fence was rejected: %v", err)
	}
	request.Sequence, request.Terminal = 1, false
	if _, err := loader.AuthorizeDelivery(context.Background(), request); err == nil {
		t.Fatal("non-terminal draining delivery below the terminal fence was accepted")
	}
	request.Sequence, request.Terminal = 2, false
	if _, err := loader.AuthorizeDelivery(context.Background(), request); err == nil {
		t.Fatal("non-terminal write at terminal fence was accepted")
	}
	request.Sequence, request.Terminal = 1, true
	if _, err := loader.AuthorizeDelivery(context.Background(), request); err == nil {
		t.Fatal("terminal before the high-water fence was accepted")
	}
	row.LastIssuedSequence = 1
	loader = checkpointLoaderForResponse(t, deliveryLeaseResponse(t, reservation, row), nil)
	loader.now = func() time.Time { return time.Date(2026, 8, 14, 12, 0, 0, 0, time.UTC) }
	request.Sequence, request.Terminal = 1, false
	if _, err := loader.AuthorizeDelivery(context.Background(), request); err == nil {
		t.Fatal("draining lease with last-issued below the terminal fence was accepted")
	}
}

func TestDeliveryLeaseGuardRequiresPhaseCoupledEqualLiveDeadlines(t *testing.T) {
	now := time.Date(2026, 8, 14, 12, 0, 0, 0, time.UTC)
	request := validDeliveryLeaseRequest()
	request.Sequence, request.Terminal = 2, true
	for _, test := range []struct {
		name   string
		mutate func(*deliveryLeaseJSON, *deliveryLeaseJSON)
	}{
		{
			name: "mixed reservation and stream phases",
			mutate: func(reservation, stream *deliveryLeaseJSON) {
				reservation.Status = "open"
				stream.Status = "draining"
			},
		},
		{
			name: "deadline mismatch",
			mutate: func(reservation, stream *deliveryLeaseJSON) {
				reservation.Status, stream.Status = "draining", "draining"
				deadline := "2026-08-14 12:02:00.000000"
				reservation.DrainDeadline = &deadline
			},
		},
		{
			name: "equal but expired deadlines",
			mutate: func(reservation, stream *deliveryLeaseJSON) {
				reservation.Status, stream.Status = "draining", "draining"
				deadline := "2026-08-14 12:00:00.000000"
				reservation.DrainDeadline, stream.DrainDeadline = &deadline, &deadline
			},
		},
	} {
		t.Run(test.name, func(t *testing.T) {
			reservation := validBuildReservationRow()
			stream := validDeliveryLeaseRow()
			stream.LastIssuedSequence, stream.FencedSequence = 2, 2
			test.mutate(&reservation, &stream)
			loader := checkpointLoaderForResponse(t, deliveryLeaseResponse(t, reservation, stream), nil)
			loader.now = func() time.Time { return now }
			if _, err := loader.AuthorizeDelivery(context.Background(), request); err == nil {
				t.Fatal("phase/deadline mismatch was accepted")
			}
		})
	}
}
