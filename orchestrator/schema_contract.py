import json
import math
import re

MAX_EXTRACTION_SCHEMA_BYTES = 16_384
MAX_EXTRACTION_SCHEMA_DEPTH = 5
MAX_EXTRACTION_SCHEMA_PROPERTIES = 32
MAX_EXTRACTION_SCHEMA_ENUM_VALUES = 32
MAX_EXTRACTION_SCHEMA_DESCRIPTION_BYTES = 1_024
MAX_EXTRACTION_SCHEMA_NAME_CHARS = 128
MAX_EXTRACTION_PATH_CHARS = 1_024
MAX_EXTRACTION_PROMPT_BYTES = 131_072
MAX_EXTRACTION_OUTPUT_BYTES = 65_536
MAX_EXTRACTION_VALIDATION_FAILURES = 32
MAX_EXTRACTION_EVIDENCE_CHARS = 4_096
MAX_EXTRACTION_URLS = 4
MAX_EXTRACTION_FIELDS = 32
MAX_EXTRACTION_OUTPUT_TOKENS = 4_096
MAX_STRUCTURED_MODEL_CONCURRENCY = 4
STRUCTURED_MODEL_DEADLINE_S = 20.0

_PROPERTY_NAME = re.compile(rf"[^\x00-\x1f]{{1,{MAX_EXTRACTION_SCHEMA_NAME_CHARS}}}")
_SCHEMA_TYPES = frozenset({"object", "array", "string", "number", "integer", "boolean"})
_KEYWORDS = frozenset({"type", "properties", "required", "items", "description", "enum"})


class SchemaContractError(ValueError):
    pass


def _json_bytes(value: object) -> int:
    try:
        return len(
            json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        )
    except (TypeError, ValueError, RecursionError) as exc:
        raise SchemaContractError("extraction_schema must contain only JSON values") from exc


def validate_extraction_schema(schema: object) -> dict[str, object]:
    if not isinstance(schema, dict):
        raise SchemaContractError("extraction_schema must be a JSON object")
    if _json_bytes(schema) > MAX_EXTRACTION_SCHEMA_BYTES:
        raise SchemaContractError(
            f"extraction_schema must not exceed {MAX_EXTRACTION_SCHEMA_BYTES} UTF-8 bytes"
        )
    return _ExtractionSchemaValidator().validate(schema)


class _ExtractionSchemaValidator:
    def __init__(self) -> None:
        self._property_count = 0

    def validate(self, schema: dict[str, object]) -> dict[str, object]:
        self._visit(schema, 1, "$")
        if schema.get("type") != "object":
            raise SchemaContractError("extraction_schema root type must be object")
        return schema

    def _visit(self, node: object, depth: int, path: str) -> None:
        if depth > MAX_EXTRACTION_SCHEMA_DEPTH:
            raise SchemaContractError(
                f"extraction_schema must not exceed depth {MAX_EXTRACTION_SCHEMA_DEPTH}"
            )
        if not isinstance(node, dict):
            raise SchemaContractError(f"schema at {path} must be an object")
        unknown = sorted(set(node) - _KEYWORDS)
        if unknown:
            raise SchemaContractError(f"unsupported schema keyword at {path}: {unknown[0]}")
        schema_type = node.get("type")
        if not isinstance(schema_type, str) or schema_type not in _SCHEMA_TYPES:
            raise SchemaContractError(f"schema at {path} requires one supported type")
        self._validate_description(node, path)
        self._validate_enum(node, schema_type, path)
        self._visit_children(node, schema_type, depth, path)

    @staticmethod
    def _validate_description(node: dict[object, object], path: str) -> None:
        description = node.get("description")
        if description is not None and (
            not isinstance(description, str)
            or len(description.encode("utf-8")) > MAX_EXTRACTION_SCHEMA_DESCRIPTION_BYTES
        ):
            raise SchemaContractError(
                f"schema description at {path} exceeds its byte limit"
            )

    @staticmethod
    def _validate_enum(node: dict[object, object], schema_type: str, path: str) -> None:
        enum = node.get("enum")
        if enum is None:
            return
        if (
            not isinstance(enum, list)
            or not enum
            or len(enum) > MAX_EXTRACTION_SCHEMA_ENUM_VALUES
        ):
            raise SchemaContractError(f"schema enum at {path} is invalid")
        if any(isinstance(value, (dict, list)) for value in enum):
            raise SchemaContractError(f"schema enum at {path} must contain scalar values")
        if schema_type in {"object", "array"} or any(
            not _instance_type_matches(schema_type, value) for value in enum
        ):
            raise SchemaContractError(f"schema enum at {path} does not match its type")

    def _visit_children(
        self,
        node: dict[object, object],
        schema_type: str,
        depth: int,
        path: str,
    ) -> None:
        properties = node.get("properties")
        required = node.get("required", [])
        items = node.get("items")
        if schema_type == "object":
            self._visit_object(properties, required, depth, path)
        elif properties is not None or required:
            raise SchemaContractError(f"non-object schema at {path} cannot define properties")
        if schema_type == "array":
            if items is None:
                raise SchemaContractError(f"array schema at {path} requires items")
            self._visit(items, depth + 1, f"{path}/*")
        elif items is not None:
            raise SchemaContractError(f"non-array schema at {path} cannot define items")

    def _visit_object(self, properties: object, required: object, depth: int, path: str) -> None:
        if not isinstance(properties, dict):
            raise SchemaContractError(f"object schema at {path} requires properties")
        self._property_count += len(properties)
        if self._property_count > MAX_EXTRACTION_SCHEMA_PROPERTIES:
            raise SchemaContractError(
                "extraction_schema must not exceed "
                f"{MAX_EXTRACTION_SCHEMA_PROPERTIES} properties"
            )
        if (
            not isinstance(required, list)
            or any(not isinstance(value, str) for value in required)
            or len(required) != len(set(required))
            or any(value not in properties for value in required)
        ):
            raise SchemaContractError(f"required fields at {path} are invalid")
        for name, child in properties.items():
            if not isinstance(name, str) or not _PROPERTY_NAME.fullmatch(name):
                raise SchemaContractError(f"property name at {path} is invalid")
            self._visit(child, depth + 1, f"{path}/{_pointer_token(name)}")


def _pointer_token(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _same_json_scalar(left: object, right: object) -> bool:
    return type(left) is type(right) and left == right


def _instance_type_matches(schema_type: str, value: object) -> bool:
    if schema_type == "object":
        return isinstance(value, dict)
    if schema_type == "array":
        return isinstance(value, list)
    if schema_type == "string":
        return isinstance(value, str)
    if schema_type == "boolean":
        return isinstance(value, bool)
    if schema_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if schema_type == "number":
        return (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
        )
    return False


def validate_extracted_value(
    schema: dict[str, object],
    value: object,
) -> list[tuple[str, str]]:
    return _ExtractedValueValidator().validate(schema, value)


class _ExtractedValueValidator:
    def __init__(self) -> None:
        self._failures: list[tuple[str, str]] = []

    def validate(
        self, schema: dict[str, object], value: object
    ) -> list[tuple[str, str]]:
        self._visit(schema, value, "")
        return self._failures

    def _fail(self, path: str, code: str) -> None:
        if len(self._failures) < MAX_EXTRACTION_VALIDATION_FAILURES:
            self._failures.append((path, code))

    def _visit(self, node: dict[str, object], current: object, path: str) -> None:
        schema_type = str(node["type"])
        if not _instance_type_matches(schema_type, current):
            self._fail(path, "type_mismatch")
            return
        enum = node.get("enum")
        if isinstance(enum, list) and not any(
            _same_json_scalar(current, candidate) for candidate in enum
        ):
            self._fail(path, "enum_mismatch")
        if schema_type == "object":
            self._visit_object(node, current, path)
        elif schema_type == "array" and isinstance(current, list):
            self._visit_array(node, current, path)

    def _visit_object(self, node: dict[str, object], current: object, path: str) -> None:
        properties = node["properties"]
        required = node.get("required", [])
        if not isinstance(properties, dict) or not isinstance(current, dict):
            return
        for name in required if isinstance(required, list) else []:
            if name not in current:
                self._fail(f"{path}/{_pointer_token(str(name))}", "required_missing")
        for name in current:
            if name not in properties:
                self._fail(f"{path}/{_pointer_token(str(name))}", "additional_property")
        for name, child in properties.items():
            if name in current and isinstance(child, dict):
                self._visit(child, current[name], f"{path}/{_pointer_token(str(name))}")

    def _visit_array(
        self, node: dict[str, object], current: list[object], path: str
    ) -> None:
        child = node.get("items")
        if not isinstance(child, dict):
            return
        for index, item in enumerate(current):
            self._visit(child, item, f"{path}/{index}")


def scalar_fields(
    value: object,
    path: str = "",
    max_fields: int = MAX_EXTRACTION_FIELDS + 1,
) -> list[tuple[str, object]]:
    fields: list[tuple[str, object]] = []
    stack = [(path, value)]
    while stack and len(fields) < max_fields:
        current_path, current = stack.pop()
        if isinstance(current, dict):
            stack.extend(
                (
                    f"{current_path}/{_pointer_token(str(name))}",
                    child,
                )
                for name, child in reversed(list(current.items()))
            )
        elif isinstance(current, list):
            stack.extend(
                (f"{current_path}/{index}", child)
                for index, child in reversed(list(enumerate(current)))
            )
        else:
            fields.append((current_path, current))
    return fields
