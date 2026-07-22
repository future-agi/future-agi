from functools import wraps
from inspect import Parameter, iscoroutinefunction, signature

import structlog
from django.conf import settings
from drf_yasg import openapi
from drf_yasg.inspectors import SwaggerAutoSchema
from drf_yasg.utils import swagger_auto_schema
from rest_framework import serializers
from rest_framework.response import Response

from tfc.utils.api_serializers import ManagementAPIErrorResponseSerializer
from tfc.utils.general_methods import GeneralMethods

logger = structlog.get_logger(__name__)

DEFAULT_ERROR_STATUS_CODE = "default"
RUNTIME_REQUEST_VALIDATION_EXTENSION = "x-runtime-request-validation"
RUNTIME_RESPONSE_VALIDATION_EXTENSION = "x-runtime-response-validation"


def hide_swagger_schema_for_actions(*action_names):
    """Hide explicitly unsupported ViewSet actions from generated OpenAPI output."""

    def decorator(viewset_class):
        for action_name in action_names:
            action = getattr(viewset_class, action_name, None)
            if action is None:
                continue

            overrides = dict(getattr(action, "_swagger_auto_schema", {}))
            overrides["auto_schema"] = None
            action._swagger_auto_schema = overrides
        return viewset_class

    return decorator


def _documented_response_has_schema(response):
    if response is None:
        return False
    if isinstance(response, openapi.Response):
        return response.schema is not None
    if isinstance(response, str):
        return False
    return True


class ManagementAPIAutoSchema(SwaggerAutoSchema):
    """Add a common typed error contract to management API operations."""

    def get_operation(self, operation_keys=None):
        operation = super().get_operation(operation_keys)

        if self.overrides.get("runtime_request_validation"):
            operation[RUNTIME_REQUEST_VALIDATION_EXTENSION] = True
        if self.overrides.get("runtime_response_validation"):
            operation[RUNTIME_RESPONSE_VALIDATION_EXTENSION] = True

        return operation

    def get_response_serializers(self):
        responses = super().get_response_serializers()

        if not _documented_response_has_schema(
            responses.get(DEFAULT_ERROR_STATUS_CODE)
        ):
            responses[DEFAULT_ERROR_STATUS_CODE] = openapi.Response(
                "Default error response",
                ManagementAPIErrorResponseSerializer,
            )

        return responses


def _serializer_name(serializer):
    if serializer is None:
        return None
    if isinstance(serializer, openapi.Response):
        return _serializer_name(serializer.schema)
    if isinstance(serializer, serializers.ListSerializer):
        return f"{type(serializer.child).__name__}(many=True)"
    if isinstance(serializer, serializers.BaseSerializer):
        return type(serializer).__name__
    if isinstance(serializer, type) and issubclass(
        serializer, serializers.BaseSerializer
    ):
        return serializer.__name__
    return None


def _response_validator(serializer, data):
    if serializer is None:
        return None
    if isinstance(serializer, openapi.Response):
        return _response_validator(serializer.schema, data)
    if isinstance(serializer, serializers.ListSerializer):
        return type(serializer.child)(data=data, many=True)
    if isinstance(serializer, serializers.BaseSerializer):
        return type(serializer)(data=data)
    if isinstance(serializer, type) and issubclass(
        serializer, serializers.BaseSerializer
    ):
        return serializer(data=data)
    return None


def _unknown_fields(data, serializer):
    if not hasattr(data, "keys") or not hasattr(serializer, "fields"):
        return []
    unknown = set(data.keys()) - set(serializer.fields.keys())
    allows_unknown_field = getattr(serializer, "allows_unknown_field", None)
    if callable(allows_unknown_field):
        unknown = {field for field in unknown if not allows_unknown_field(field)}
    return sorted(unknown)


def _as_error_dict(errors):
    if not errors:
        return {}
    if isinstance(errors, dict):
        return dict(errors)
    if isinstance(errors, list):
        return {"non_field_errors": errors}
    return {"non_field_errors": [errors]}


def _serializer_errors(serializer):
    try:
        return _as_error_dict(serializer.errors)
    except ValueError:
        return _as_error_dict(getattr(serializer, "_errors", None))


def _validate_serializer(
    serializer_class,
    data,
    *,
    reject_unknown_fields=False,
    partial=False,
    context=None,
):
    serializer_kwargs = {"data": data, "partial": partial}
    if context is not None:
        serializer_kwargs["context"] = context
    serializer = serializer_class(**serializer_kwargs)
    if reject_unknown_fields:
        unknown = _unknown_fields(data, serializer)
        if unknown:
            serializer.is_valid()
            errors = _serializer_errors(serializer)
            errors.update({key: ["Unknown field."] for key in unknown})
            return serializer, errors, False
    is_valid = serializer.is_valid()
    return serializer, _serializer_errors(serializer), is_valid


def _query_params_without_framework_params(query_params, framework_query_params):
    framework_owned = set(framework_query_params or ())
    if not framework_owned:
        return query_params
    if not hasattr(query_params, "copy"):
        return {
            key: value
            for key, value in query_params.items()
            if key not in framework_owned
        }
    filtered = query_params.copy()
    for key in framework_owned:
        filtered.pop(key, None)
    return filtered


def _validate_response(view_name, serializer, response, strict):
    validator = _response_validator(serializer, response.data)
    if validator is None:
        return

    if not validator.is_valid(raise_exception=strict):
        logger.warning(
            "API response does not match declared serializer.",
            view_func=view_name,
            status_code=response.status_code,
            serializer_class=_serializer_name(serializer),
            validation_errors=validator.errors,
        )


def validate_request_data(request, serializer_class):
    """Validate a function-based view request body with a declared serializer."""

    serializer = serializer_class(data=request.data)
    serializer.is_valid(raise_exception=True)
    return serializer.validated_data


def validate_query_params(request, serializer_class):
    """Validate a function-based view query string with a declared serializer."""

    serializer = serializer_class(data=request.query_params)
    serializer.is_valid(raise_exception=True)
    return serializer.validated_data


def _request_from_call(args):
    """Return the DRF request from either a function view or APIView method call."""
    if args and hasattr(args[0], "method") and hasattr(args[0], "data"):
        return args[0]
    if len(args) >= 2 and hasattr(args[1], "method") and hasattr(args[1], "data"):
        return args[1]
    raise TypeError("validated_request could not locate a DRF request argument.")


def _view_args_from_call(args):
    """Return URL positional args without the view instance or request object."""

    if args and hasattr(args[0], "method") and hasattr(args[0], "data"):
        return args[1:]
    if len(args) >= 2 and hasattr(args[1], "method") and hasattr(args[1], "data"):
        return args[2:]
    return ()


def _serializer_context(serializer_context, request, view_args=(), view_kwargs=None):
    if serializer_context is None:
        return None
    if callable(serializer_context):
        view_kwargs = view_kwargs or {}
        try:
            context_signature = signature(serializer_context)
        except (TypeError, ValueError):
            return serializer_context(request)

        parameters = context_signature.parameters
        accepts_var_args = any(
            parameter.kind == Parameter.VAR_POSITIONAL
            for parameter in parameters.values()
        )
        accepts_var_kwargs = any(
            parameter.kind == Parameter.VAR_KEYWORD for parameter in parameters.values()
        )
        matched_kwargs = (
            dict(view_kwargs)
            if accepts_var_kwargs
            else {key: value for key, value in view_kwargs.items() if key in parameters}
        )
        accepts_view_args = (
            accepts_var_args
            or sum(
                1
                for parameter in parameters.values()
                if parameter.kind
                in (Parameter.POSITIONAL_ONLY, Parameter.POSITIONAL_OR_KEYWORD)
            )
            > 1
        )

        if accepts_view_args:
            return serializer_context(request, *view_args, **matched_kwargs)
        return serializer_context(request, **matched_kwargs)
    return serializer_context


def validated_request(
    request_serializer=None,
    *,
    query_serializer=None,
    responses=None,
    request_methods=None,
    strict_request_validation=True,
    strict_response_validation=False,
    partial_request_validation=False,
    reject_unknown_fields=False,
    validation_error_response=None,
    serializer_context=None,
    framework_query_params=(),
    **swagger_kwargs,
):
    """Document and validate a DRF view method from the same serializers.

    The serializer declared for Swagger is also the runtime validator. New
    endpoints should prefer this over a doc-only ``swagger_auto_schema`` plus
    ad-hoc ``request.data`` parsing.

    Response serializers are checked in DEBUG to catch contract drift during
    development. Set ``strict_response_validation=True`` only for compact,
    deterministic endpoints where a response mismatch should fail the request in
    production too.
    """
    request_method_set = (
        {method.upper() for method in request_methods} if request_methods else None
    )

    def decorator(view_func):
        swagger_options = dict(swagger_kwargs)
        if request_serializer is not None:
            swagger_options["request_body"] = request_serializer
        if query_serializer is not None:
            swagger_options["query_serializer"] = query_serializer
        if responses is not None:
            swagger_options["responses"] = responses
        if request_serializer is not None or query_serializer is not None:
            swagger_options["runtime_request_validation"] = True
        if responses is not None:
            # This extension means the endpoint has a concrete response contract
            # that is runtime-checked in DEBUG, and strictly enforced when
            # strict_response_validation=True.
            swagger_options["runtime_response_validation"] = True

        def prepare_request(*args, **kwargs):
            request = _request_from_call(args)
            request.validated_data = {}
            request.validated_query_data = {}
            request.validated_serializer = None
            request.validated_query_serializer = None
            gm = GeneralMethods(request=request)
            context = _serializer_context(
                serializer_context,
                request,
                _view_args_from_call(args),
                kwargs,
            )

            if query_serializer is not None:
                query_data = _query_params_without_framework_params(
                    request.query_params,
                    framework_query_params,
                )
                serializer, errors, is_valid = _validate_serializer(
                    query_serializer,
                    query_data,
                    reject_unknown_fields=reject_unknown_fields,
                    context=context,
                )
                if not is_valid:
                    if validation_error_response is not None:
                        return validation_error_response(errors)
                    return gm.bad_request(errors)
                request.validated_query_data = serializer.validated_data
                request.validated_query_serializer = serializer

            should_validate_body = request_serializer is not None and (
                request_method_set is None
                or request.method.upper() in request_method_set
            )
            if should_validate_body:
                serializer, errors, is_valid = _validate_serializer(
                    request_serializer,
                    request.data,
                    reject_unknown_fields=reject_unknown_fields,
                    partial=partial_request_validation,
                    context=context,
                )
                if not is_valid:
                    if strict_request_validation:
                        if validation_error_response is not None:
                            return validation_error_response(errors)
                        return gm.bad_request(errors)
                    if settings.DEBUG:
                        logger.warning(
                            "API request does not match declared serializer.",
                            view_func=view_func.__name__,
                            serializer_class=request_serializer.__name__,
                            validation_errors=errors,
                        )
                request.validated_data = serializer.validated_data
                request.validated_serializer = serializer

            return None

        def finalize_response(response):
            if not responses or not isinstance(response, Response):
                return response

            response_serializer = responses.get(response.status_code)
            if response_serializer is None:
                if strict_response_validation:
                    raise serializers.ValidationError(
                        f"Undocumented response status {response.status_code} "
                        f"for {view_func.__name__}."
                    )
                if settings.DEBUG:
                    logger.warning(
                        "API response status is not declared.",
                        view_func=view_func.__name__,
                        status_code=response.status_code,
                        declared_status_codes=sorted(responses),
                    )
                return response

            if strict_response_validation or settings.DEBUG:
                _validate_response(
                    view_func.__name__,
                    response_serializer,
                    response,
                    strict_response_validation,
                )

            return response

        if iscoroutinefunction(view_func):

            @swagger_auto_schema(**swagger_options)
            @wraps(view_func)
            async def wrapper(*args, **kwargs):
                early_response = prepare_request(*args, **kwargs)
                if early_response is not None:
                    return early_response

                response = await view_func(*args, **kwargs)
                return finalize_response(response)

            return wrapper

        @swagger_auto_schema(**swagger_options)
        @wraps(view_func)
        def wrapper(*args, **kwargs):
            early_response = prepare_request(*args, **kwargs)
            if early_response is not None:
                return early_response

            response = view_func(*args, **kwargs)
            return finalize_response(response)

        return wrapper

    return decorator


def validated_api_request(
    request_serializer=None,
    *,
    query_serializer=None,
    responses=None,
    method=None,
    request_methods=None,
    document=True,
    strict_request_validation=True,
    strict_response_validation=False,
    partial_request_validation=False,
    reject_unknown_fields=False,
    validation_error_response=None,
    serializer_context=None,
    framework_query_params=(),
    **swagger_kwargs,
):
    """Document and validate a function-based DRF view from one serializer set.

    Response validation follows ``validated_request``: DEBUG logs drift by
    default, and ``strict_response_validation=True`` turns drift into request
    failure for endpoints where that cost is intentional.
    """
    request_method_set = (
        {request_method.upper() for request_method in request_methods}
        if request_methods
        else None
    )

    def decorator(view_func):
        swagger_options = dict(swagger_kwargs)
        if method is not None:
            swagger_options["method"] = method
        if request_serializer is not None:
            swagger_options["request_body"] = request_serializer
        if query_serializer is not None:
            swagger_options["query_serializer"] = query_serializer
        if responses is not None:
            swagger_options["responses"] = responses
        if request_serializer is not None or query_serializer is not None:
            swagger_options["runtime_request_validation"] = True
        if responses is not None:
            # This extension means the endpoint has a concrete response contract
            # that is runtime-checked in DEBUG, and strictly enforced when
            # strict_response_validation=True.
            swagger_options["runtime_response_validation"] = True

        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            request.validated_data = {}
            request.validated_query_data = {}
            request.validated_serializer = None
            request.validated_query_serializer = None
            gm = GeneralMethods(request=request)
            context = _serializer_context(serializer_context, request, args, kwargs)

            if query_serializer is not None:
                query_data = _query_params_without_framework_params(
                    request.query_params,
                    framework_query_params,
                )
                serializer, errors, is_valid = _validate_serializer(
                    query_serializer,
                    query_data,
                    reject_unknown_fields=reject_unknown_fields,
                    context=context,
                )
                if not is_valid:
                    if validation_error_response is not None:
                        return validation_error_response(errors)
                    return gm.bad_request(errors)
                request.validated_query_data = serializer.validated_data
                request.validated_query_serializer = serializer

            should_validate_body = request_serializer is not None and (
                request_method_set is None
                or request.method.upper() in request_method_set
            )
            if should_validate_body:
                serializer, errors, is_valid = _validate_serializer(
                    request_serializer,
                    request.data,
                    reject_unknown_fields=reject_unknown_fields,
                    partial=partial_request_validation,
                    context=context,
                )
                if not is_valid:
                    if strict_request_validation:
                        if validation_error_response is not None:
                            return validation_error_response(errors)
                        return gm.bad_request(errors)
                    if settings.DEBUG:
                        logger.warning(
                            "API request does not match declared serializer.",
                            view_func=view_func.__name__,
                            serializer_class=request_serializer.__name__,
                            validation_errors=errors,
                        )
                request.validated_data = serializer.validated_data
                request.validated_serializer = serializer

            response = view_func(request, *args, **kwargs)

            if not responses or not isinstance(response, Response):
                return response

            response_serializer = responses.get(response.status_code)
            if response_serializer is None:
                if strict_response_validation:
                    raise serializers.ValidationError(
                        f"Undocumented response status {response.status_code} "
                        f"for {view_func.__name__}."
                    )
                if settings.DEBUG:
                    logger.warning(
                        "API response status is not declared.",
                        view_func=view_func.__name__,
                        status_code=response.status_code,
                        declared_status_codes=sorted(responses),
                    )
                return response

            if strict_response_validation or settings.DEBUG:
                _validate_response(
                    view_func.__name__,
                    response_serializer,
                    response,
                    strict_response_validation,
                )

            return response

        if not document:
            return wrapper
        return swagger_auto_schema(**swagger_options)(wrapper)

    return decorator
