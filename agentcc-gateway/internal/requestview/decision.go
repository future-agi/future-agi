package requestview

import (
	"encoding/json"
	"errors"
)

// Decision is a decision body (POST /v1/systemone and POST /v1/decisions):
// System One's {model, state, questions: {<id>: {type, ...}}} or OpenAI
// Decisions' {model, input, questions: [{type, name, ...}]}, which the
// gateway sends the vendor as the client sent it, with only the model
// rewritten. Its answer is a decision, not model text the checks on the
// answer read, and the handler serves it whatever they say (NoChatAnswerView).
func Decision(body []byte) NoChatAnswerView {
	return NoChatAnswerView{&View{body: body, read: readDecision}}
}

// readDecision reads a decision body into the chat shape, in the body's
// order: each field but the model and the questions (System One's state,
// OpenAI Decisions' input, and any other field the client sends) as a user
// message, and each question as a system message: its id (a System One
// question's key), then each of its fields (its type and name, its
// instructions and any other field). A string is read as its text, any other
// value as its JSON text as the vendor decodes it (canonicalJSON), so the
// keys and numbers the model reads are read too. A question's id, name and
// type are labels the gateway sends as they are: the checks read them, and
// a rewrite of one is refused (text.fixed). The model is the one the vendor
// is sent, chosen by the gateway, and is not read. The handler refuses a
// body that repeats a key in an object (models.ParseDecisionRequest), so
// what is read is what the vendor gets; of a repeated key, this reads the
// last.
func readDecision(d *doc) (*chat, error) {
	keys, values, ok := d.members(d.body)
	if !ok {
		return nil, errors.New("the body is not a JSON object")
	}
	b := newBuilder(d)
	last := lastOf(keys)
	for i, k := range keys {
		if !last(i) {
			continue
		}
		v, at := values[i], path{k}
		switch k {
		case "model":
			b.c.req.Model, _ = asString(v)
		case "questions":
			if err := decisionQuestions(b, v, at); err != nil {
				return nil, err
			}
		default:
			m := &message{role: "user"}
			if err := decisionValue(d, m, v, at, ""); err != nil {
				return nil, err
			}
			b.add(m)
		}
	}
	return &b.c, nil
}

// decisionQuestions reads the questions at at: an object keyed by question
// id (System One) or a list (OpenAI Decisions).
func decisionQuestions(b *builder, raw json.RawMessage, at path) error {
	switch first(raw) {
	case '{':
		ids, questions, ok := b.d.members(raw)
		if !ok {
			return readError(at, "is not an object")
		}
		last := lastOf(ids)
		for i, id := range ids {
			if !last(i) {
				continue
			}
			if err := decisionQuestion(b, questions[i], at.to(id), &id); err != nil {
				return err
			}
		}
	case '[':
		questions, ok := b.d.array(raw)
		if !ok {
			return readError(at, "is not a list")
		}
		for i, q := range questions {
			if err := decisionQuestion(b, q, at.to(i), nil); err != nil {
				return err
			}
		}
	default:
		return readError(at, "is neither an object nor a list of questions")
	}
	return nil
}

// decisionQuestion reads the question at at, an object, as one system
// message: its id (a System One question's key, id; nil for OpenAI
// Decisions, whose questions are a list) and each of its fields. The id,
// the type and the name are labels (fixed).
func decisionQuestion(b *builder, raw json.RawMessage, at path, id *string) error {
	keys, values, ok := b.d.members(raw)
	if !ok {
		return readError(at, "is not an object")
	}
	m := &message{role: "system"}
	if id != nil {
		m.addText(text{at: at, value: *id, fixed: "a question's id"})
	}
	last := lastOf(keys)
	for i, k := range keys {
		if !last(i) {
			continue
		}
		fixed := ""
		switch k {
		case "type":
			fixed = "a question's type"
		case "name":
			fixed = "a question's name"
		}
		if err := decisionValue(b.d, m, values[i], at.to(k), fixed); err != nil {
			return err
		}
	}
	b.add(m)
	return nil
}

// decisionValue adds to m the text of raw, the value at at: a string's text,
// or any other value's JSON text; fixed says what label it is, if it is one.
// One nested more than MaxDepth deep is not read, and the view says so (a
// check that would block or rewrite the request refuses it).
func decisionValue(d *doc, m *message, raw json.RawMessage, at path, fixed string) error {
	if absent(raw) {
		return nil
	}
	if s, ok := asString(raw); ok {
		m.addText(text{at: at, value: s, fixed: fixed})
		return nil
	}
	value, err := d.canonicalJSON(raw, at)
	switch {
	case errors.Is(err, errTooDeep):
		d.tooDeep(at)
	case err != nil:
		return readError(at, "is not JSON: %v", err)
	default:
		m.addText(text{at: at, value: string(value), json: true, fixed: fixed})
	}
	return nil
}
