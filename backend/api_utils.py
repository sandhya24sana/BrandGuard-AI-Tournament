import re
from typing import Any, Dict, Iterable, List, Optional, Type

from flask import abort, request

MAX_JSON_REQUEST_BYTES = 1_048_576
MAX_STRING_FIELD_LENGTH = 10_000
MAX_GEMINI_TEXT_LENGTH = 50_000
MAX_VISUAL_IMAGE_BYTES = 5 * 1024 * 1024
MAX_COLLECTION_ITEMS = 100
MAX_DATABASE_ID = 9_223_372_036_854_775_807


def json_object_payload(
    max_bytes: int = MAX_JSON_REQUEST_BYTES,
) -> Dict[str, Any]:
    if request.content_length is not None and request.content_length > max_bytes:
        abort(413, description="Request body exceeds the allowed size")
    if len(request.get_data(cache=True)) > max_bytes:
        abort(413, description="Request body exceeds the allowed size")
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        abort(400, description="Request body must be a JSON object")
    return payload


def validate_required_fields(
    payload: Dict[str, Any],
    field_names: Iterable[str],
) -> None:
    for field_name in field_names:
        value = payload.get(field_name)
        if value is None or (isinstance(value, str) and not value.strip()):
            abort(400, description=f"{field_name} is required")


def validate_string_fields(
    payload: Dict[str, Any],
    field_names: Iterable[str],
    allow_none: bool = True,
    max_length: int = MAX_STRING_FIELD_LENGTH,
    max_lengths: Optional[Dict[str, int]] = None,
) -> None:
    for field_name in field_names:
        value = payload.get(field_name)
        if field_name not in payload:
            continue
        if value is None and allow_none:
            continue
        if not isinstance(value, str):
            abort(400, description=f"{field_name} must be a string")
        field_limit = (max_lengths or {}).get(field_name, max_length)
        if len(value) > field_limit:
            abort(
                400,
                description=f"{field_name} must be {field_limit} characters or fewer",
            )


def validate_id_field(
    payload: Dict[str, Any],
    field_name: str,
    required: bool = False,
) -> Optional[int]:
    if field_name not in payload and not required:
        return None
    value = payload.get(field_name)
    if not is_positive_int(value):
        abort(400, description=f"{field_name} must be a positive integer")
    return value


def validate_list_field(
    payload: Dict[str, Any],
    field_name: str,
    max_items: int = MAX_COLLECTION_ITEMS,
    item_type: Optional[Type[Any]] = None,
    required: bool = False,
) -> List[Any]:
    if field_name not in payload:
        if required:
            abort(400, description=f"{field_name} is required")
        return []
    value = payload[field_name]
    if not isinstance(value, list):
        abort(400, description=f"{field_name} must be a list")
    if len(value) > max_items:
        abort(
            400,
            description=f"{field_name} must contain {max_items} items or fewer",
        )
    if item_type is not None and not all(isinstance(item, item_type) for item in value):
        abort(400, description=f"{field_name} items must be {item_type.__name__} values")
    return value


def validate_query_id(field_name: str, required: bool = False) -> Optional[int]:
    raw_value = request.args.get(field_name)
    if raw_value is None:
        if required:
            abort(400, description=f"{field_name} is required")
        return None
    if len(raw_value) > 19 or not re.fullmatch(r"[0-9]+", raw_value):
        abort(400, description=f"{field_name} must be a positive integer")
    value = int(raw_value)
    if not is_positive_int(value):
        abort(400, description=f"{field_name} must be a positive integer")
    return value


def is_positive_int(value: Any) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and 0 < value <= MAX_DATABASE_ID
    )
