package video

import (
	"encoding/json"
	"fmt"
	"reflect"
	"strconv"
	"strings"
	"time"
)

var timeType = reflect.TypeOf(time.Time{})

// Hash fields use the architecture's names; timestamps are Unix seconds. The
// storage tag explicitly includes private fields excluded from public JSON.
func fieldName(f reflect.StructField) string {
	if n := f.Tag.Get("store"); n != "" {
		return n
	}
	n := strings.Split(f.Tag.Get("json"), ",")[0]
	if n == "-" || n == "" {
		return ""
	}
	return n
}
func encodeJob(j *VideoJob) (map[string]string, error) {
	fields := map[string]string{}
	v := reflect.ValueOf(j).Elem()
	typ := v.Type()
	for i := 0; i < v.NumField(); i++ {
		name := fieldName(typ.Field(i))
		if name == "" {
			continue
		}
		f := v.Field(i)
		switch {
		case f.Type() == timeType:
			t := f.Interface().(time.Time)
			fields[name] = "0"
			if !t.IsZero() {
				fields[name] = strconv.FormatInt(t.Unix(), 10)
			}
		case f.Kind() == reflect.Ptr && f.Type().Elem() == timeType:
			fields[name] = ""
			if !f.IsNil() {
				fields[name] = strconv.FormatInt(f.Interface().(*time.Time).Unix(), 10)
			}
		case f.Kind() == reflect.String:
			fields[name] = f.String()
		default:
			b, err := json.Marshal(f.Interface())
			if err != nil {
				return nil, err
			}
			fields[name] = string(b)
		}
	}

	fields["error_code"] = ""
	fields["error_message"] = ""
	fields["error_retryable"] = "false"
	if j.Error != nil {
		fields["error_code"] = j.Error.Code
		fields["error_message"] = j.Error.Message
		fields["error_retryable"] = strconv.FormatBool(j.Error.Retryable)
	}
	return fields, nil
}
func decodeJob(fields map[string]string) (*VideoJob, error) {
	j := new(VideoJob)
	v := reflect.ValueOf(j).Elem()
	typ := v.Type()
	for i := 0; i < v.NumField(); i++ {
		name := fieldName(typ.Field(i))
		raw, ok := fields[name]
		if name == "" || !ok {
			continue
		}
		f := v.Field(i)
		switch {
		case f.Type() == timeType:
			if raw != "" && raw != "0" {
				n, err := strconv.ParseInt(raw, 10, 64)
				if err != nil {
					return nil, fmt.Errorf("invalid stored %s", name)
				}
				f.Set(reflect.ValueOf(time.Unix(n, 0).UTC()))
			}
		case f.Kind() == reflect.Ptr && f.Type().Elem() == timeType:
			if raw != "" {
				n, err := strconv.ParseInt(raw, 10, 64)
				if err != nil {
					return nil, fmt.Errorf("invalid stored %s", name)
				}
				t := time.Unix(n, 0).UTC()
				f.Set(reflect.ValueOf(&t))
			}
		case f.Type() == reflect.TypeOf(json.RawMessage{}) && raw == "null":
			f.SetZero()
		case f.Kind() == reflect.String:
			f.SetString(raw)
		default:
			if err := json.Unmarshal([]byte(raw), f.Addr().Interface()); err != nil {
				return nil, fmt.Errorf("invalid stored %s: %w", name, err)
			}
		}
	}

	if j.Error == nil && fields["error_code"] != "" {
		retryable, _ := strconv.ParseBool(fields["error_retryable"])
		j.Error = &VideoError{Code: fields["error_code"], Message: fields["error_message"], Retryable: retryable, ProviderRequestID: j.ProviderRequestID}
	}
	return j, nil
}
func cloneJob(j *VideoJob) (*VideoJob, error) {
	if j == nil {
		return nil, fmt.Errorf("nil job")
	}
	fields, err := encodeJob(j)
	if err != nil {
		return nil, err
	}
	return decodeJob(fields)
}
