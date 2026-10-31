package ingestbench

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"reflect"
	"strconv"
	"strings"
	"time"
)

// ValidateResultJSON checks the wire shape BEFORE encoding/json can discard
// duplicate keys or turn null/missing scalar measurements into zero. Schema
// requirements come from Result's field types/tags, not a second numeric list.
// Collections and pointers may be absent/null; omitempty permits absence (not
// null scalars). receipt:"optional" marks an entire optional disclosure. Once
// supplied, its nonnullable measurements are required like any other struct.
func ValidateResultJSON(raw []byte) error {
	d := json.NewDecoder(bytes.NewReader(raw))
	d.UseNumber()
	if err := receiptJSONValue(d, reflect.TypeOf(Result{}), "", false); err != nil {
		return err
	}
	if _, err := d.Token(); err != io.EOF {
		return fmt.Errorf("trailing JSON input")
	}
	return nil
}

func receiptJSONPath(parent, name string) string {
	if parent == "" {
		return name
	}
	return parent + "." + name
}

func receiptJSONFields(t reflect.Type) []reflect.StructField {
	var fields []reflect.StructField
	for i := 0; i < t.NumField(); i++ {
		f := t.Field(i)
		if f.PkgPath != "" || f.Tag.Get("json") == "-" {
			continue
		}
		// All receipt structs use named JSON fields; untagged exported fields follow
		// encoding/json's default spelling, so newly added fields are covered too.
		fields = append(fields, f)
	}
	return fields
}

func receiptJSONName(f reflect.StructField) string {
	name := strings.Split(f.Tag.Get("json"), ",")[0]
	if name == "" {
		return f.Name
	}
	return name
}

func receiptJSONOptional(f reflect.StructField) bool {
	return f.Tag.Get("receipt") == "optional" || strings.Contains(","+f.Tag.Get("json")+",", ",omitempty,") || f.Type.Kind() == reflect.Pointer || f.Type.Kind() == reflect.Slice || f.Type.Kind() == reflect.Map
}

func receiptJSONValue(d *json.Decoder, t reflect.Type, path string, nullable bool) error {
	if t != nil {
		if t.Kind() == reflect.Pointer {
			nullable = true
			t = t.Elem()
		}
		if t.Kind() == reflect.Map || t.Kind() == reflect.Slice || t.Kind() == reflect.Interface {
			nullable = true
		}
		if t == reflect.TypeOf(json.RawMessage{}) {
			t = nil
			nullable = true
		} // opaque external metadata
	}
	token, err := d.Token()
	if err != nil {
		return fmt.Errorf("%s: %w", path, err)
	}
	if token == nil {
		if nullable || t == nil {
			return nil
		}
		return fmt.Errorf("%s must not be null", path)
	}
	// time.Time is a JSON string, not its private Go struct representation.
	if t == reflect.TypeOf(time.Time{}) {
		if _, ok := token.(string); !ok {
			return fmt.Errorf("%s must be a timestamp string", path)
		}
		return nil
	}
	if delim, ok := token.(json.Delim); ok {
		switch delim {
		case '{':
			if t != nil && t.Kind() != reflect.Struct && t.Kind() != reflect.Map && t.Kind() != reflect.Interface {
				return fmt.Errorf("%s must be %s, not an object", path, t)
			}
			var fields []reflect.StructField
			if t != nil && t.Kind() == reflect.Struct {
				fields = receiptJSONFields(t)
			}
			seen := map[string]bool{}
			for d.More() {
				keyToken, err := d.Token()
				if err != nil {
					return fmt.Errorf("%s: %w", path, err)
				}
				key := keyToken.(string)
				canonical := key
				var child reflect.Type
				optional := false
				if t != nil && t.Kind() == reflect.Struct {
					index := -1
					// encoding/json prefers exact spelling, then a case-insensitive match.
					for i, f := range fields {
						if receiptJSONName(f) == key {
							index = i
							break
						}
					}
					if index < 0 {
						for i, f := range fields {
							if strings.EqualFold(receiptJSONName(f), key) {
								index = i
								break
							}
						}
					}
					if index < 0 {
						return fmt.Errorf("%s: unknown field", receiptJSONPath(path, key))
					}
					f := fields[index]
					canonical = receiptJSONName(f)
					child = f.Type
					optional = f.Tag.Get("receipt") == "optional"
				} else if t != nil && t.Kind() == reflect.Map {
					child = t.Elem()
					switch t.Key().Kind() {
					case reflect.Int, reflect.Int8, reflect.Int16, reflect.Int32, reflect.Int64:
						n, e := strconv.ParseInt(key, 10, t.Key().Bits())
						if e != nil {
							return fmt.Errorf("%s[%s]: invalid integer map key", path, key)
						}
						canonical = strconv.FormatInt(n, 10)
					case reflect.Uint, reflect.Uint8, reflect.Uint16, reflect.Uint32, reflect.Uint64:
						n, e := strconv.ParseUint(key, 10, t.Key().Bits())
						if e != nil {
							return fmt.Errorf("%s[%s]: invalid integer map key", path, key)
						}
						canonical = strconv.FormatUint(n, 10)
					}
				}
				p := receiptJSONPath(path, canonical)
				if seen[canonical] {
					return fmt.Errorf("%s: duplicate JSON key %q", p, key)
				}
				seen[canonical] = true
				if err := receiptJSONValue(d, child, p, optional); err != nil {
					return err
				}
			}
			if _, err := d.Token(); err != nil {
				return fmt.Errorf("%s: %w", path, err)
			}
			for _, f := range fields {
				if !seen[receiptJSONName(f)] && !receiptJSONOptional(f) {
					return fmt.Errorf("%s is required", receiptJSONPath(path, receiptJSONName(f)))
				}
			}
			return nil
		case '[':
			var child reflect.Type
			if t != nil && t.Kind() != reflect.Interface {
				if t.Kind() != reflect.Slice && t.Kind() != reflect.Array {
					return fmt.Errorf("%s must be %s, not an array", path, t)
				}
				child = t.Elem()
			}
			for i := 0; d.More(); i++ {
				if err := receiptJSONValue(d, child, fmt.Sprintf("%s[%d]", path, i), false); err != nil {
					return err
				}
			}
			_, err := d.Token()
			return err
		}
		return fmt.Errorf("%s: unexpected delimiter", path)
	}
	if t == nil || t.Kind() == reflect.Interface {
		return nil
	}
	valid := false
	switch t.Kind() {
	case reflect.String:
		_, valid = token.(string)
	case reflect.Bool:
		_, valid = token.(bool)
	case reflect.Int, reflect.Int8, reflect.Int16, reflect.Int32, reflect.Int64:
		if n, ok := token.(json.Number); ok {
			_, e := strconv.ParseInt(string(n), 10, t.Bits())
			valid = e == nil
		}
	case reflect.Uint, reflect.Uint8, reflect.Uint16, reflect.Uint32, reflect.Uint64:
		if n, ok := token.(json.Number); ok {
			_, e := strconv.ParseUint(string(n), 10, t.Bits())
			valid = e == nil
		}
	case reflect.Float32, reflect.Float64:
		if n, ok := token.(json.Number); ok {
			_, e := strconv.ParseFloat(string(n), t.Bits())
			valid = e == nil
		}
	}
	if !valid {
		return fmt.Errorf("%s must be a JSON %s value", path, t.Kind())
	}
	return nil
}
