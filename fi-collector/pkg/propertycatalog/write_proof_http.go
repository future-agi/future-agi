package propertycatalog

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"reflect"
	"sort"
	"strconv"
	"strings"
)

const maxWriteProofBytes = 4 << 20

// HTTPWriteProof uses the separately-granted SELECT principal. Construction is
// local; Attest performs real direct-node/schema/registered-membership probes.
// A descriptor is an expected identity, never a substitute for those probes.
type HTTPWriteProof struct {
	reader *ClickHouseSink
	policy *WritePolicy
	writer *ClickHouseSink
}

func NewHTTPWriteProof(cfg ClickHouseSinkConfig, policy *WritePolicy) (*HTTPWriteProof, error) {
	if policy == nil || policy.admission.Database != cfg.Database || policy.admission.Environment != cfg.Environment {
		return nil, errors.New("propertycatalog: proof reader requires matching admission")
	}
	reader, err := newClickHouseTransport(cfg)
	if err != nil {
		return nil, err
	}
	for _, member := range policy.admission.Members {
		origin, err := bareClickHouseOrigin(member.URL)
		if err != nil || origin.Scheme != reader.baseURL.Scheme {
			return nil, errors.New("propertycatalog: proof routes cannot change configured HTTP/TLS scheme")
		}
	}
	return &HTTPWriteProof{reader: reader, policy: policy}, nil
}

// query consumes and bounds the ENTIRE response before exposing any row.
// Truncation, an exception following HTTP 200, extra rows and overflow all fail.
// No result cache and no transport retry are used for proof observations.
func (p *HTTPWriteProof) query(ctx context.Context, endpoint, statement string, params map[string]string, maxBytes int64) ([]map[string]json.RawMessage, error) {
	return p.queryBounded(ctx, endpoint, statement, params, nil, maxBytes)
}

func (p *HTTPWriteProof) queryBounded(ctx context.Context, endpoint, statement string, params, settings map[string]string, maxBytes int64) ([]map[string]json.RawMessage, error) {
	if ctx == nil || maxBytes <= 0 || maxBytes > MaximumCheckpointInventoryMaxBytes {
		return nil, errors.New("propertycatalog: invalid proof query bounds")
	}
	u, err := bareClickHouseOrigin(endpoint)
	if err != nil {
		return nil, err
	}
	q := url.Values{
		"database": {p.reader.database}, "max_execution_time": {"2"}, "max_threads": {"1"},
		"max_result_bytes": {strconv.FormatInt(maxBytes, 10)}, "result_overflow_mode": {"throw"},
		"max_memory_usage": {"134217728"}, "wait_end_of_query": {"1"}, "use_query_cache": {"0"},
		"output_format_json_quote_64bit_integers": {"0"},
	}
	for name, value := range params {
		if !strings.HasPrefix(name, "param_") {
			return nil, errors.New("propertycatalog: proof parameters cannot override settings")
		}
		q.Set(name, value)
	}
	for name, value := range settings {
		// Preserve the inventory/lease reader's existing resource bounds without
		// allowing callers to override identity, caching or response completion.
		if name == "group_by_overflow_mode" && value == "throw" {
			q.Set(name, value)
			continue
		}
		ceiling := uint64(0)
		switch name {
		case "max_execution_time":
			ceiling = uint64(MaxDeliveryTimeout.Seconds())
		case "max_rows_to_group_by":
			ceiling = maxCheckpointSequencesPerStream + 1
		case "max_result_rows":
			ceiling = uint64(MaximumCheckpointInventoryMaxBytes)
		}
		n, err := strconv.ParseUint(value, 10, 64)
		if err != nil || n == 0 || n > ceiling || strconv.FormatUint(n, 10) != value {
			return nil, errors.New("propertycatalog: unapproved proof query setting")
		}
		q.Set(name, value)
	}
	u.RawQuery = q.Encode()
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, u.String(), strings.NewReader(statement))
	if err != nil {
		return nil, err
	}
	request.SetBasicAuth(p.reader.username, p.reader.password)
	request.Header.Set("Content-Type", "text/plain; charset=utf-8")
	response, err := p.reader.client.Do(request)
	if err != nil {
		return nil, err
	}
	defer response.Body.Close()
	raw, err := io.ReadAll(io.LimitReader(response.Body, maxBytes+1))
	if err != nil || int64(len(raw)) > maxBytes || response.ContentLength > 0 && int64(len(raw)) != response.ContentLength {
		return nil, errors.New("propertycatalog: incomplete or oversized proof response")
	}
	if response.StatusCode != http.StatusOK || response.Header.Get("X-ClickHouse-Exception-Code") != "" && response.Header.Get("X-ClickHouse-Exception-Code") != "0" {
		return nil, fmt.Errorf("propertycatalog: proof query failed (HTTP %d)", response.StatusCode)
	}
	rows := make([]map[string]json.RawMessage, 0)
	if len(raw) == 0 {
		return rows, ctx.Err()
	}
	if raw[len(raw)-1] != '\n' {
		return nil, errors.New("propertycatalog: proof response lacks complete JSONEachRow terminator")
	}
	for _, line := range bytes.Split(raw[:len(raw)-1], []byte{'\n'}) {
		if err := uniqueProofJSON(line); err != nil {
			return nil, err
		}
		var row map[string]json.RawMessage
		if err := json.Unmarshal(line, &row); err != nil || row == nil {
			return nil, errors.New("propertycatalog: invalid proof JSONEachRow")
		}
		rows = append(rows, row)
	}
	return rows, ctx.Err()
}

// The standard JSON map decoder silently keeps the last duplicate field.
// Evidence must instead reject duplicate keys, including in nested objects.
func uniqueProofJSON(raw []byte) error {
	d := json.NewDecoder(bytes.NewReader(raw))
	d.UseNumber()
	var consume func(int) error
	consume = func(depth int) error {
		if depth > 32 {
			return errors.New("propertycatalog: proof JSON nesting exceeds bound")
		}
		token, err := d.Token()
		if err != nil {
			return err
		}
		switch token {
		case json.Delim('{'):
			seen := map[string]bool{}
			for d.More() {
				key, err := d.Token()
				name, ok := key.(string)
				if err != nil || !ok || seen[name] {
					return errors.New("propertycatalog: duplicate/invalid proof JSON field")
				}
				seen[name] = true
				if err := consume(depth + 1); err != nil {
					return err
				}
			}
			end, err := d.Token()
			if err != nil || end != json.Delim('}') {
				return errors.New("propertycatalog: incomplete proof JSON object")
			}
		case json.Delim('['):
			for d.More() {
				if err := consume(depth + 1); err != nil {
					return err
				}
			}
			end, err := d.Token()
			if err != nil || end != json.Delim(']') {
				return errors.New("propertycatalog: incomplete proof JSON array")
			}
		}
		return nil
	}
	if err := consume(0); err != nil {
		return err
	}
	return requireJSONEOF(d)
}

// Only these three exact INSERT tables are decoded here. The Kafka wire
// decoder deliberately continues to reject ledger chunks.
func decodeExactWriteRows(raw []byte, table string) ([]map[string]any, error) {
	columns, err := insertColumns(table)
	if err != nil {
		return nil, err
	}
	if len(raw) == 0 || len(raw) > maxCatalogInsertBytes || raw[len(raw)-1] != '\n' {
		return nil, errors.New("propertycatalog: invalid exact INSERT bytes")
	}
	lines := bytes.Split(raw[:len(raw)-1], []byte{'\n'})
	if len(lines) > MaxRowsPerEnvelope {
		return nil, errors.New("propertycatalog: exact INSERT exceeds row bound")
	}
	rows := make([]map[string]any, 0, len(lines))
	for _, line := range lines {
		if err := uniqueProofJSON(line); err != nil {
			return nil, err
		}
		d := json.NewDecoder(bytes.NewReader(line))
		d.UseNumber()
		var row map[string]any
		if err := d.Decode(&row); err != nil {
			return nil, err
		}
		if len(row) != len(columns) {
			return nil, errors.New("propertycatalog: incomplete exact INSERT columns")
		}
		for _, name := range columns {
			if _, present := row[name]; !present {
				return nil, errors.New("propertycatalog: missing exact INSERT column")
			}
		}
		rows = append(rows, row)
	}
	return rows, nil
}

type proofTopologyRow struct {
	Hostname       string   `json:"hostname"`
	ServerUUID     string   `json:"server_uuid"`
	Database       string   `json:"database"`
	DatabaseUUID   string   `json:"database_uuid"`
	DatabaseEngine string   `json:"database_engine"`
	Username       string   `json:"username"`
	Name           string   `json:"name"`
	UUID           string   `json:"uuid"`
	Engine         string   `json:"engine"`
	CreateSHA256   string   `json:"create_sha256"`
	KeeperPath     string   `json:"keeper_path"`
	KeeperName     string   `json:"keeper_name"`
	ReplicaName    string   `json:"replica_name"`
	ReplicaNames   []string `json:"replica_names"`
	TotalReplicas  uint64   `json:"total_replicas"`
	ActiveReplicas uint64   `json:"active_replicas"`
	Readonly       uint8    `json:"readonly"`
	SessionExpired uint8    `json:"session_expired"`
}

const writeTopologyQuery = `SELECT hostName() AS hostname, toString(serverUUID()) AS server_uuid, currentDatabase() AS database,
 (SELECT toString(uuid) FROM system.databases WHERE name=currentDatabase()) AS database_uuid,
 (SELECT engine FROM system.databases WHERE name=currentDatabase()) AS database_engine,
 currentUser() AS username, t.name AS name, toString(t.uuid) AS uuid, t.engine AS engine,
 lower(hex(SHA256(t.create_table_query))) AS create_sha256,
 ifNull(r.zookeeper_path,'') AS keeper_path, ifNull(r.replica_name,'') AS replica_name,
 ifNull(r.zookeeper_name,'') AS keeper_name,
 arraySort(mapKeys(ifNull(r.replica_is_active,map()))) AS replica_names,
 ifNull(r.total_replicas,0) AS total_replicas, ifNull(r.active_replicas,0) AS active_replicas,
 ifNull(r.is_readonly,0) AS readonly, ifNull(r.is_session_expired,0) AS session_expired
 FROM system.tables AS t LEFT JOIN system.replicas AS r ON t.database=r.database AND t.name=r.table
 WHERE t.database=currentDatabase() ORDER BY t.name LIMIT 8 FORMAT JSONEachRow`

func (p *HTTPWriteProof) Attest(ctx context.Context, policy *WritePolicy) error {
	if policy == nil || policy.admission.TopologySHA256 != p.policy.admission.TopologySHA256 {
		return errors.New("propertycatalog: proof topology changed")
	}
	for _, member := range policy.admission.Members {
		rows, err := p.query(ctx, member.URL, writeTopologyQuery, nil, 128<<10)
		if err != nil {
			return err
		}
		if len(rows) != len(member.Tables) {
			return errors.New("propertycatalog: physical schema inventory differs from admission")
		}
		for i, raw := range rows {
			var got proofTopologyRow
			if err := decodeProofRow(raw, &got); err != nil {
				return err
			}
			want := member.Tables[i]
			if got.Hostname != member.Hostname || got.ServerUUID != member.ServerUUID || got.Database != policy.admission.Database || got.DatabaseUUID != member.DatabaseUUID ||
				got.DatabaseEngine != "Atomic" || got.Username != p.reader.username || got.Name != want.Name || got.UUID != want.UUID ||
				got.Engine != want.Engine || got.CreateSHA256 != want.CreateSHA256 || got.KeeperPath != want.KeeperPath ||
				got.ReplicaName != want.ReplicaName || !reflect.DeepEqual(got.ReplicaNames, want.ReplicaNames) ||
				got.Readonly != 0 || got.SessionExpired != 0 {
				return fmt.Errorf("propertycatalog: direct identity/schema/replica drift on %s/%s", member.Name, want.Name)
			}
			n := uint64(0)
			if policy.admission.Family == "replicated" {
				n = uint64(len(policy.admission.Members))
				if got.KeeperName != "default" {
					return errors.New("propertycatalog: catalog requires the attested default Keeper connection")
				}
			} else if got.KeeperName != "" {
				return errors.New("propertycatalog: standalone table unexpectedly binds Keeper")
			}
			if got.TotalReplicas != n || got.ActiveReplicas != n {
				return errors.New("propertycatalog: not every admitted replica is registered and active")
			}
		}
		if err := p.attestReadGrants(ctx, member.URL); err != nil {
			return err
		}
	}
	if policy.admission.Family == "replicated" {
		if err := p.attestKeeper(ctx, policy); err != nil {
			return err
		}
	}
	if p.writer != nil {
		return p.attestWriter(ctx)
	}
	return nil
}

func (p *HTTPWriteProof) attestWriter(ctx context.Context) error {
	writerReader := &HTTPWriteProof{reader: p.writer, policy: p.policy}
	endpoint := p.writer.baseURL.String()
	rows, err := writerReader.query(ctx, endpoint, "SELECT hostName() AS hostname,toString(serverUUID()) AS server_uuid,currentDatabase() AS database,currentUser() AS username FORMAT JSONEachRow", nil, 4096)
	if err != nil {
		return err
	}
	var identity struct {
		Hostname   string `json:"hostname"`
		ServerUUID string `json:"server_uuid"`
		Database   string `json:"database"`
		Username   string `json:"username"`
	}
	if len(rows) != 1 {
		return errors.New("propertycatalog: writer identity absent")
	}
	if err := decodeProofRow(rows[0], &identity); err != nil {
		return err
	}
	matched := false
	for _, m := range p.policy.admission.Members {
		if m.URL == endpoint && m.Hostname == identity.Hostname && m.ServerUUID == identity.ServerUUID {
			matched = true
		}
	}
	if !matched || identity.Database != p.policy.admission.Database || identity.Username != p.writer.username {
		return errors.New("propertycatalog: writer direct identity mismatch")
	}
	grants, err := writerReader.query(ctx, endpoint, "SHOW GRANTS FORMAT JSONEachRow", nil, 32<<10)
	if err != nil {
		return err
	}
	seen := map[string]bool{}
	for _, row := range grants {
		if len(row) != 1 {
			return errors.New("propertycatalog: unexpected writer grant shape")
		}
		for _, raw := range row {
			var grant string
			if json.Unmarshal(raw, &grant) != nil {
				return errors.New("propertycatalog: invalid writer grant")
			}
			grant = strings.ReplaceAll(grant, "`", "")
			prefix, suffix := "GRANT INSERT ON "+p.policy.admission.Database+".", " TO "+p.writer.username
			if !strings.HasPrefix(grant, prefix) || !strings.HasSuffix(grant, suffix) {
				return errors.New("propertycatalog: writer has unreviewed privileges")
			}
			table := strings.TrimSuffix(strings.TrimPrefix(grant, prefix), suffix)
			if _, err := insertColumns(table); err != nil {
				return err
			}
			seen[table] = true
		}
	}
	if len(seen) != 3 {
		return errors.New("propertycatalog: writer requires exactly three INSERT table grants")
	}
	return nil
}

func decodeProofRow(row map[string]json.RawMessage, target any) error {
	raw, err := json.Marshal(row)
	if err != nil {
		return err
	}
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(target); err != nil {
		return err
	}
	// JSON missing/null evidence must not silently become a successful zero.
	t := reflect.TypeOf(target).Elem()
	if len(row) != t.NumField() {
		return errors.New("propertycatalog: proof row has missing fields")
	}
	for i := 0; i < t.NumField(); i++ {
		v, present := row[t.Field(i).Tag.Get("json")]
		if !present || bytes.Equal(v, []byte("null")) {
			return errors.New("propertycatalog: proof row has missing/null fields")
		}
	}
	return nil
}

func (p *HTTPWriteProof) attestReadGrants(ctx context.Context, endpoint string) error {
	rows, err := p.query(ctx, endpoint, "SHOW GRANTS FORMAT JSONEachRow", nil, 32<<10)
	if err != nil {
		return err
	}
	if len(rows) == 0 {
		return errors.New("propertycatalog: proof principal has no explicit grants")
	}
	for _, row := range rows {
		if len(row) != 1 {
			return errors.New("propertycatalog: unexpected SHOW GRANTS shape")
		}
		for _, raw := range row {
			var grant string
			if json.Unmarshal(raw, &grant) != nil || !safeProofGrant(grant, p.reader.database, p.reader.username) {
				return errors.New("propertycatalog: proof principal has unreviewed grants")
			}
		}
	}
	return nil
}

func safeProofGrant(grant, database, user string) bool {
	grant = strings.ReplaceAll(grant, "`", "")
	if !strings.HasPrefix(grant, "GRANT SELECT ON ") || !strings.HasSuffix(grant, " TO "+user) {
		return false
	}
	target := strings.TrimSuffix(strings.TrimPrefix(grant, "GRANT SELECT ON "), " TO "+user)
	if strings.HasPrefix(target, database+".") {
		_, allowed := catalogEngineFamilies[strings.TrimPrefix(target, database+".")]
		return allowed
	}
	switch target {
	case "system.clusters", "system.databases", "system.tables", "system.replicas", "system.parts", "system.zookeeper", "system.zookeeper_connection", "system.query_log":
		return true
	}
	return false
}

func (p *HTTPWriteProof) Cover(ctx context.Context, policy *WritePolicy, attempt ExactWriteAttempt) error {
	if policy == nil || attempt.TopologySHA256 != policy.admission.TopologySHA256 {
		return errors.New("propertycatalog: coverage topology does not match attempt")
	}
	rows, err := decodeExactWriteRows(attempt.Body, attempt.Table)
	if err != nil || len(rows) == 0 || len(rows) > MaxRowsPerEnvelope {
		return errors.New("propertycatalog: invalid exact coverage payload")
	}
	for _, member := range policy.admission.Members {
		for start := 0; start < len(rows); start += 32 {
			end := min(start+32, len(rows))
			query, err := coverageQuery(attempt.Table, rows[start:end])
			if err != nil {
				return err
			}
			observed, err := p.query(ctx, member.URL, query, nil, 4096)
			if err != nil {
				return err
			}
			if len(observed) != 1 || len(observed[0]) != end-start {
				return errors.New("propertycatalog: incomplete all-replica coverage result")
			}
			for i := 0; i < end-start; i++ {
				if string(observed[0][fmt.Sprintf("r%d", i)]) != "1" {
					return fmt.Errorf("propertycatalog: exact row coverage absent/conflicting on %s", member.Name)
				}
			}
		}
	}
	return nil
}

func coverageQuery(table string, rows []map[string]any) (string, error) {
	columns, err := insertColumns(table)
	if err != nil {
		return "", err
	}
	checks := make([]string, 0, len(rows))
	for i, row := range rows {
		if len(row) != len(columns) {
			return "", errors.New("propertycatalog: invalid exact coverage column set")
		}
		predicates, identity := []string{}, []string{}
		for _, column := range columns {
			value, present := row[column]
			if !present {
				return "", errors.New("propertycatalog: missing exact coverage column")
			}
			literal, err := proofLiteral(value)
			if err != nil {
				return "", err
			}
			predicate := column + "=" + literal
			if value == nil {
				predicate = "isNull(" + column + ")"
			}
			predicates = append(predicates, predicate)
			if table == string(AttributeValueTable) && column != "first_seen" && column != "last_seen" && column != "value_json" && column != "value_search_text_folded" {
				identity = append(identity, predicate)
			}
		}
		check := "countIf(" + strings.Join(predicates, " AND ") + ")>0"
		if table == string(AttributeValueTable) {
			key := "(" + strings.Join(identity, " AND ") + ")"
			value, _ := proofLiteral(row["value_json"])
			search, _ := proofLiteral(row["value_search_text_folded"])
			first, _ := proofLiteral(row["first_seen"])
			last, _ := proofLiteral(row["last_seen"])
			check = "countIf(" + key + ")>0 AND countIf(" + key + " AND (value_json!=" + value + " OR value_search_text_folded!=" + search + "))=0" +
				" AND minIf(first_seen," + key + ")<=" + first + " AND maxIf(last_seen," + key + ")>=" + last
		}
		checks = append(checks, "toUInt8("+check+") AS r"+strconv.Itoa(i))
	}
	// Every envelope is scope-validated before reaching the sink. Keep reads
	// on its exact tenant/revision/build prefix, never a global catalog scan.
	prefix := make([]string, 0, 5)
	for _, column := range []string{"organization_id", "workspace_id", "catalog_epoch", "catalog_revision", "build_token"} {
		literal, err := proofLiteral(rows[0][column])
		if err != nil {
			return "", err
		}
		prefix = append(prefix, column+"="+literal)
	}
	return "SELECT " + strings.Join(checks, ",") + " FROM " + table + " WHERE " + strings.Join(prefix, " AND ") + " FORMAT JSONEachRow", nil
}

func proofLiteral(value any) (string, error) {
	switch v := value.(type) {
	case nil:
		return "NULL", nil
	case string:
		return "'" + strings.NewReplacer("\\", "\\\\", "'", "\\'", "\x00", "\\0", "\n", "\\n", "\r", "\\r").Replace(v) + "'", nil
	case json.Number:
		if _, err := strconv.ParseInt(string(v), 10, 64); err != nil {
			if _, err := strconv.ParseUint(string(v), 10, 64); err != nil {
				return "", errors.New("propertycatalog: non-integral proof number")
			}
		}
		return string(v), nil
	case []any:
		values := make([]string, len(v))
		for i, item := range v {
			literal, err := proofLiteral(item)
			if err != nil {
				return "", err
			}
			values[i] = literal
		}
		return "[" + strings.Join(values, ",") + "]", nil
	default:
		return "", fmt.Errorf("propertycatalog: unsupported exact proof value %T", value)
	}
}

func insertColumns(table string) ([]string, error) {
	switch table {
	case string(DefinitionTable):
		return definitionColumns, nil
	case string(AttributeValueTable):
		return attributeValueColumns, nil
	case "property_catalog_deliveries":
		return deliveryColumns, nil
	}
	return nil, errors.New("propertycatalog: forbidden exact INSERT table")
}

func (p *HTTPWriteProof) Resolve(ctx context.Context, policy *WritePolicy, attempt ExactWriteAttempt) (AttemptSettlement, error) {
	unknown := AttemptSettlement{Outcome: "unresolved"}
	if policy == nil || attempt.TopologySHA256 != policy.admission.TopologySHA256 {
		return unknown, errors.New("propertycatalog: settlement topology mismatch")
	}
	// Optional POSITIVE query-log evidence. Empty, unavailable, truncated or
	// exception records never authorize a retry. No query logging is enabled by
	// this adapter. The saved query_id is unique and dispatched at most once.
	query := `SELECT query_id, type, exception_code, current_database, query,
 Settings['async_insert'] AS async_insert, Settings['insert_quorum'] AS insert_quorum,
 Settings['insert_quorum_parallel'] AS insert_quorum_parallel
 FROM system.query_log WHERE query_id={attempt_id:String} AND is_initial_query=1
 AND type IN ('QueryFinish','ExceptionBeforeStart','ExceptionWhileProcessing')
 ORDER BY event_time_microseconds LIMIT 2 FORMAT JSONEachRow`
	rows, err := p.query(ctx, attempt.Endpoint, query, map[string]string{"param_attempt_id": attempt.QueryID}, 4096)
	if err != nil {
		return unknown, err
	}
	if len(rows) != 1 {
		return unknown, nil
	}
	var result struct {
		QueryID       string `json:"query_id"`
		Type          string `json:"type"`
		ExceptionCode uint64 `json:"exception_code"`
		Database      string `json:"current_database"`
		Query         string `json:"query"`
		AsyncInsert   string `json:"async_insert"`
		Quorum        string `json:"insert_quorum"`
		Parallel      string `json:"insert_quorum_parallel"`
	}
	if err := decodeProofRow(rows[0], &result); err != nil {
		return unknown, err
	}
	if result.QueryID != attempt.QueryID || result.Type != "QueryFinish" || result.ExceptionCode != 0 || result.Database != policy.admission.Database {
		return unknown, nil
	}
	statement, err := exactInsertStatement(attempt.Table, attempt.Settings)
	if err != nil {
		return unknown, err
	}
	if exactLoggedInsert(result.Query, statement) {
		// A present setting must agree. Missing map entries are justified ONLY
		// by this exact completed SQL statement, never by server defaults.
		if result.AsyncInsert != "" && result.AsyncInsert != "0" ||
			result.Quorum != "" && result.Quorum != attempt.Settings["insert_quorum"] ||
			result.Parallel != "" && result.Parallel != "1" {
			return unknown, nil
		}
	} else {
		// Preserve positive completion of historical HTTP-settings attempts
		// only when every required setting was actually logged. No upgrade,
		// replacement statement, or retransmission of an older Sent attempt.
		columns, _ := insertColumns(attempt.Table)
		legacy := fmt.Sprintf("INSERT INTO %s (%s) FORMAT JSONEachRow", attempt.Table, strings.Join(columns, ","))
		if !exactLoggedInsert(result.Query, legacy) || result.AsyncInsert != "0" || result.Quorum != attempt.Settings["insert_quorum"] || result.Parallel != "1" {
			return unknown, nil
		}
	}
	raw, _ := json.Marshal(rows)
	return AttemptSettlement{Outcome: "settled_success", QueryID: attempt.QueryID, BodySHA256: attempt.BodySHA256,
		TopologySHA256: attempt.TopologySHA256, WitnessSHA256: sha256Hex(raw)}, nil
}

func exactLoggedInsert(logged, statement string) bool {
	// CH25.3's HTTP query-parameter/data boundary appends one LF to the
	// statement in QueryFinish.query. No other normalization is permitted.
	return logged == statement || logged == statement+"\n"
}

// readAgreed is used by the real lease/inventory loader. Each complete bounded
// result is canonicalized without dropping duplicate rows; disagreement fails.
func (p *HTTPWriteProof) readAgreed(ctx context.Context, statement string, params map[string]string, maxBytes int64) ([]map[string]json.RawMessage, error) {
	return p.readAgreedBounded(ctx, statement, params, nil, maxBytes)
}

func (p *HTTPWriteProof) readAgreedBounded(ctx context.Context, statement string, params, settings map[string]string, maxBytes int64) ([]map[string]json.RawMessage, error) {
	if err := p.Attest(ctx, p.policy); err != nil {
		return nil, err
	}
	var agreed []map[string]json.RawMessage
	var expected []string
	for i, member := range p.policy.admission.Members {
		rows, err := p.queryBounded(ctx, member.URL, statement, params, settings, maxBytes)
		if err != nil {
			return nil, err
		}
		canonical := make([]string, len(rows))
		for index, row := range rows {
			encoded, _ := json.Marshal(row)
			canonical[index] = string(encoded)
		}
		sort.Strings(canonical)
		if i == 0 {
			agreed, expected = rows, canonical
		} else if !reflect.DeepEqual(expected, canonical) {
			return nil, errors.New("propertycatalog: admitted replicas disagree on control/checkpoint read")
		}
	}
	return agreed, p.Attest(ctx, p.policy)
}
