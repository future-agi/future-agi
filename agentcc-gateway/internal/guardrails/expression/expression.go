package expression

import (
	"context"
	"encoding/json"
	"fmt"
	"regexp"
	"strconv"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/guardrails"
)

// ExpressionGuardrail evaluates a user-defined expression against
// request/response data to determine if a guardrail should trigger.
type ExpressionGuardrail struct {
	name    string
	expr    string
	message string
	parsed  node
}

// New creates an ExpressionGuardrail from config.
func New(name string, cfg map[string]interface{}) *ExpressionGuardrail {
	g := &ExpressionGuardrail{name: name}
	if cfg != nil {
		if v, ok := cfg["expression"].(string); ok {
			g.expr = v
		}
		if v, ok := cfg["message"].(string); ok {
			g.message = v
		}
	}
	if g.expr != "" {
		parsed, err := parse(g.expr)
		if err == nil {
			g.parsed = parsed
		}
	}
	if g.message == "" {
		g.message = "Expression rule triggered: " + g.expr
	}
	return g
}

func (g *ExpressionGuardrail) Name() string           { return g.name }
func (g *ExpressionGuardrail) Stage() guardrails.Stage { return guardrails.StagePre }

func (g *ExpressionGuardrail) Check(ctx context.Context, input *guardrails.CheckInput) *guardrails.CheckResult {
	if g.parsed == nil || input == nil {
		return &guardrails.CheckResult{Pass: true}
	}

	env := buildEnv(input)
	result := eval(g.parsed, env)

	triggered, _ := toBool(result)
	if !triggered {
		return &guardrails.CheckResult{Pass: true, Score: 0.0}
	}

	return &guardrails.CheckResult{
		Pass:    false,
		Score:   1.0,
		Message: g.message,
	}
}

// IsExpressionConfig returns true if the config has an "expression" key.
func IsExpressionConfig(cfg map[string]interface{}) bool {
	if cfg == nil {
		return false
	}
	_, ok := cfg["expression"].(string)
	return ok
}

// --- Environment ---

// env maps field paths to values for expression evaluation.
type env map[string]interface{}

func buildEnv(input *guardrails.CheckInput) env {
	e := make(env)

	if input.Request != nil {
		reqJSON, _ := json.Marshal(input.Request)
		var reqMap map[string]interface{}
		json.Unmarshal(reqJSON, &reqMap)
		e["request"] = reqMap
	}

	if input.Response != nil {
		respJSON, _ := json.Marshal(input.Response)
		var respMap map[string]interface{}
		json.Unmarshal(respJSON, &respMap)
		e["response"] = respMap
	}

	if input.Metadata != nil {
		metaMap := make(map[string]interface{}, len(input.Metadata))
		for k, v := range input.Metadata {
			metaMap[k] = v
		}
		e["metadata"] = metaMap
	}

	return e
}

func resolveField(e env, path string) interface{} {
	parts := splitFieldPath(path)
	var current interface{} = map[string]interface{}(e)
	for _, p := range parts {
		switch v := current.(type) {
		case map[string]interface{}:
			current = v[p]
		case []interface{}:
			idx, err := strconv.Atoi(p)
			if err != nil || idx < 0 || idx >= len(v) {
				return nil
			}
			current = v[idx]
		default:
			return nil
		}
	}
	return current
}

func splitFieldPath(path string) []string {
	var parts []string
	var current strings.Builder
	for i := 0; i < len(path); i++ {
		switch path[i] {
		case '.':
			if current.Len() > 0 {
				parts = append(parts, current.String())
				current.Reset()
			}
		case '[':
			if current.Len() > 0 {
				parts = append(parts, current.String())
				current.Reset()
			}
		case ']':
			if current.Len() > 0 {
				parts = append(parts, current.String())
				current.Reset()
			}
		default:
			current.WriteByte(path[i])
		}
	}
	if current.Len() > 0 {
		parts = append(parts, current.String())
	}
	return parts
}

// --- AST ---

type node interface {
	nodeType() string
}

type literalNode struct {
	value interface{}
}

func (n *literalNode) nodeType() string { return "literal" }

type fieldNode struct {
	path string
}

func (n *fieldNode) nodeType() string { return "field" }

type binaryNode struct {
	op    string
	left  node
	right node
}

func (n *binaryNode) nodeType() string { return "binary" }

type unaryNode struct {
	op      string
	operand node
}

func (n *unaryNode) nodeType() string { return "unary" }

type callNode struct {
	fn  string
	arg node
}

func (n *callNode) nodeType() string { return "call" }

type listNode struct {
	items []node
}

func (n *listNode) nodeType() string { return "list" }

// --- Tokenizer ---

type tokenKind int

const (
	tokEOF tokenKind = iota
	tokIdent
	tokNumber
	tokString
	tokOp
	tokLParen
	tokRParen
	tokLBrack
	tokRBrack
	tokComma
	tokDot
	tokBang
)

type token struct {
	kind tokenKind
	val  string
}

func tokenize(input string) []token {
	var tokens []token
	i := 0
	for i < len(input) {
		ch := input[i]

		// Skip whitespace.
		if ch == ' ' || ch == '\t' || ch == '\n' || ch == '\r' {
			i++
			continue
		}

		// String literal.
		if ch == '"' || ch == '\'' {
			quote := ch
			i++
			start := i
			for i < len(input) && input[i] != quote {
				if input[i] == '\\' {
					i++
				}
				i++
			}
			tokens = append(tokens, token{tokString, input[start:i]})
			if i < len(input) {
				i++ // skip closing quote
			}
			continue
		}

		// Number.
		if ch >= '0' && ch <= '9' {
			start := i
			for i < len(input) && ((input[i] >= '0' && input[i] <= '9') || input[i] == '.') {
				i++
			}
			tokens = append(tokens, token{tokNumber, input[start:i]})
			continue
		}

		// Identifier or keyword.
		if isIdentStart(ch) {
			start := i
			for i < len(input) && isIdentPart(input[i]) {
				i++
			}
			tokens = append(tokens, token{tokIdent, input[start:i]})
			continue
		}

		// Operators.
		if ch == '=' && i+1 < len(input) && input[i+1] == '=' {
			tokens = append(tokens, token{tokOp, "=="})
			i += 2
			continue
		}
		if ch == '!' && i+1 < len(input) && input[i+1] == '=' {
			tokens = append(tokens, token{tokOp, "!="})
			i += 2
			continue
		}
		if ch == '>' && i+1 < len(input) && input[i+1] == '=' {
			tokens = append(tokens, token{tokOp, ">="})
			i += 2
			continue
		}
		if ch == '<' && i+1 < len(input) && input[i+1] == '=' {
			tokens = append(tokens, token{tokOp, "<="})
			i += 2
			continue
		}
		if ch == '&' && i+1 < len(input) && input[i+1] == '&' {
			tokens = append(tokens, token{tokOp, "&&"})
			i += 2
			continue
		}
		if ch == '|' && i+1 < len(input) && input[i+1] == '|' {
			tokens = append(tokens, token{tokOp, "||"})
			i += 2
			continue
		}
		if ch == '>' {
			tokens = append(tokens, token{tokOp, ">"})
			i++
			continue
		}
		if ch == '<' {
			tokens = append(tokens, token{tokOp, "<"})
			i++
			continue
		}
		if ch == '!' {
			tokens = append(tokens, token{tokBang, "!"})
			i++
			continue
		}
		if ch == '(' {
			tokens = append(tokens, token{tokLParen, "("})
			i++
			continue
		}
		if ch == ')' {
			tokens = append(tokens, token{tokRParen, ")"})
			i++
			continue
		}
		if ch == '[' {
			tokens = append(tokens, token{tokLBrack, "["})
			i++
			continue
		}
		if ch == ']' {
			tokens = append(tokens, token{tokRBrack, "]"})
			i++
			continue
		}
		if ch == ',' {
			tokens = append(tokens, token{tokComma, ","})
			i++
			continue
		}
		if ch == '.' {
			tokens = append(tokens, token{tokDot, "."})
			i++
			continue
		}

		// Unknown char — skip.
		i++
	}
	tokens = append(tokens, token{tokEOF, ""})
	return tokens
}

func isIdentStart(ch byte) bool {
	return (ch >= 'a' && ch <= 'z') || (ch >= 'A' && ch <= 'Z') || ch == '_'
}

func isIdentPart(ch byte) bool {
	return isIdentStart(ch) || (ch >= '0' && ch <= '9')
}

// --- Parser ---

type parser struct {
	tokens []token
	pos    int
}

func parse(expr string) (node, error) {
	tokens := tokenize(expr)
	p := &parser{tokens: tokens}
	n := p.parseOr()
	if p.current().kind != tokEOF {
		return nil, fmt.Errorf("unexpected token: %s", p.current().val)
	}
	return n, nil
}

func (p *parser) current() token {
	if p.pos >= len(p.tokens) {
		return token{tokEOF, ""}
	}
	return p.tokens[p.pos]
}

func (p *parser) advance() token {
	t := p.current()
	p.pos++
	return t
}

func (p *parser) parseOr() node {
	left := p.parseAnd()
	for p.current().kind == tokOp && p.current().val == "||" {
		p.advance()
		right := p.parseAnd()
		left = &binaryNode{op: "||", left: left, right: right}
	}
	return left
}

func (p *parser) parseAnd() node {
	left := p.parseComparison()
	for p.current().kind == tokOp && p.current().val == "&&" {
		p.advance()
		right := p.parseComparison()
		left = &binaryNode{op: "&&", left: left, right: right}
	}
	return left
}

func (p *parser) parseComparison() node {
	left := p.parseUnary()

	// Handle infix operators: ==, !=, >, <, >=, <=, contains, matches, in, startsWith, endsWith.
	// Do NOT consume && or || here — those are handled by parseAnd/parseOr.
	for {
		cur := p.current()
		if cur.kind == tokOp && cur.val != "&&" && cur.val != "||" {
			op := cur.val
			p.advance()
			right := p.parseUnary()
			left = &binaryNode{op: op, left: left, right: right}
			continue
		}
		if cur.kind == tokIdent {
			switch cur.val {
			case "contains", "matches", "in", "startsWith", "endsWith":
				op := cur.val
				p.advance()
				right := p.parseUnary()
				left = &binaryNode{op: op, left: left, right: right}
				continue
			}
		}
		break
	}

	return left
}

func (p *parser) parseUnary() node {
	if p.current().kind == tokBang {
		p.advance()
		operand := p.parseUnary()
		return &unaryNode{op: "!", operand: operand}
	}
	return p.parsePrimary()
}

func (p *parser) parsePrimary() node {
	cur := p.current()

	// Parenthesized expression.
	if cur.kind == tokLParen {
		p.advance()
		n := p.parseOr()
		if p.current().kind == tokRParen {
			p.advance()
		}
		return n
	}

	// List literal.
	if cur.kind == tokLBrack {
		return p.parseList()
	}

	// String literal.
	if cur.kind == tokString {
		p.advance()
		return &literalNode{value: cur.val}
	}

	// Number literal.
	if cur.kind == tokNumber {
		p.advance()
		if strings.Contains(cur.val, ".") {
			f, _ := strconv.ParseFloat(cur.val, 64)
			return &literalNode{value: f}
		}
		i, _ := strconv.ParseInt(cur.val, 10, 64)
		return &literalNode{value: float64(i)}
	}

	// Boolean literals.
	if cur.kind == tokIdent && (cur.val == "true" || cur.val == "false") {
		p.advance()
		return &literalNode{value: cur.val == "true"}
	}

	// Function call: len(...), lower(...), upper(...), has(...).
	if cur.kind == tokIdent && p.peek().kind == tokLParen {
		fn := cur.val
		p.advance() // skip fn name
		p.advance() // skip (
		arg := p.parseOr()
		if p.current().kind == tokRParen {
			p.advance()
		}
		return &callNode{fn: fn, arg: arg}
	}

	// Field path: request.model, metadata.key, etc.
	if cur.kind == tokIdent {
		return p.parseFieldPath()
	}

	// Fallback.
	p.advance()
	return &literalNode{value: nil}
}

func (p *parser) parseFieldPath() node {
	var path strings.Builder
	path.WriteString(p.advance().val)

	for {
		if p.current().kind == tokDot {
			p.advance()
			if p.current().kind == tokIdent {
				path.WriteByte('.')
				path.WriteString(p.advance().val)
			}
		} else if p.current().kind == tokLBrack {
			p.advance()
			if p.current().kind == tokNumber {
				path.WriteByte('[')
				path.WriteString(p.advance().val)
				path.WriteByte(']')
			}
			if p.current().kind == tokRBrack {
				p.advance()
			}
		} else {
			break
		}
	}

	return &fieldNode{path: path.String()}
}

func (p *parser) parseList() node {
	p.advance() // skip [
	var items []node
	for p.current().kind != tokRBrack && p.current().kind != tokEOF {
		items = append(items, p.parseOr())
		if p.current().kind == tokComma {
			p.advance()
		}
	}
	if p.current().kind == tokRBrack {
		p.advance()
	}
	return &listNode{items: items}
}

func (p *parser) peek() token {
	if p.pos+1 >= len(p.tokens) {
		return token{tokEOF, ""}
	}
	return p.tokens[p.pos+1]
}

// --- Evaluator ---

func eval(n node, e env) interface{} {
	switch v := n.(type) {
	case *literalNode:
		return v.value
	case *fieldNode:
		return resolveField(e, v.path)
	case *listNode:
		items := make([]interface{}, len(v.items))
		for i, item := range v.items {
			items[i] = eval(item, e)
		}
		return items
	case *callNode:
		return evalCall(v, e)
	case *unaryNode:
		return evalUnary(v, e)
	case *binaryNode:
		return evalBinary(v, e)
	}
	return nil
}

func evalCall(n *callNode, e env) interface{} {
	arg := eval(n.arg, e)
	switch n.fn {
	case "len":
		switch v := arg.(type) {
		case string:
			return float64(len(v))
		case []interface{}:
			return float64(len(v))
		case map[string]interface{}:
			return float64(len(v))
		}
		return float64(0)
	case "lower":
		return strings.ToLower(toString(arg))
	case "upper":
		return strings.ToUpper(toString(arg))
	case "has":
		return arg != nil
	}
	return nil
}

func evalUnary(n *unaryNode, e env) interface{} {
	val := eval(n.operand, e)
	if n.op == "!" {
		b, _ := toBool(val)
		return !b
	}
	return nil
}

func evalBinary(n *binaryNode, e env) interface{} {
	left := eval(n.left, e)
	right := eval(n.right, e)

	switch n.op {
	case "&&":
		lb, _ := toBool(left)
		rb, _ := toBool(right)
		return lb && rb
	case "||":
		lb, _ := toBool(left)
		rb, _ := toBool(right)
		return lb || rb
	case "==":
		return compare(left, right) == 0
	case "!=":
		return compare(left, right) != 0
	case ">":
		return compare(left, right) > 0
	case "<":
		return compare(left, right) < 0
	case ">=":
		return compare(left, right) >= 0
	case "<=":
		return compare(left, right) <= 0
	case "contains":
		return strings.Contains(toString(left), toString(right))
	case "startsWith":
		return strings.HasPrefix(toString(left), toString(right))
	case "endsWith":
		return strings.HasSuffix(toString(left), toString(right))
	case "matches":
		re, err := regexp.Compile(toString(right))
		if err != nil {
			return false
		}
		return re.MatchString(toString(left))
	case "in":
		if list, ok := right.([]interface{}); ok {
			for _, item := range list {
				if compare(left, item) == 0 {
					return true
				}
			}
		}
		return false
	}
	return nil
}

// --- Helpers ---

func toBool(v interface{}) (bool, bool) {
	switch b := v.(type) {
	case bool:
		return b, true
	case float64:
		return b != 0, true
	case string:
		return b != "", true
	case nil:
		return false, true
	}
	return false, false
}

func toFloat(v interface{}) (float64, bool) {
	switch n := v.(type) {
	case float64:
		return n, true
	case int:
		return float64(n), true
	case string:
		f, err := strconv.ParseFloat(n, 64)
		return f, err == nil
	}
	return 0, false
}

func toString(v interface{}) string {
	switch s := v.(type) {
	case string:
		return s
	case float64:
		if s == float64(int64(s)) {
			return strconv.FormatInt(int64(s), 10)
		}
		return strconv.FormatFloat(s, 'f', -1, 64)
	case bool:
		return strconv.FormatBool(s)
	case nil:
		return ""
	default:
		return fmt.Sprintf("%v", v)
	}
}

func compare(a, b interface{}) int {
	// Try numeric comparison.
	af, aok := toFloat(a)
	bf, bok := toFloat(b)
	if aok && bok {
		if af < bf {
			return -1
		}
		if af > bf {
			return 1
		}
		return 0
	}

	// Fall back to string comparison.
	as := toString(a)
	bs := toString(b)
	if as < bs {
		return -1
	}
	if as > bs {
		return 1
	}
	return 0
}
