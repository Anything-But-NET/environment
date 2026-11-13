import copy
from pathlib import Path
from typing import Any, Callable

import jsonschema
import pytest
import yaml

CONTRACTS_DIR = Path(__file__).resolve().parent.parent.parent / "contracts"
OPENAPI_PATH = CONTRACTS_DIR / "openapi.yaml"
KAFKA_OPENAPI_PATH = CONTRACTS_DIR / "openapi-kafka.yaml"
SHARED_OPENAPI_PATH = CONTRACTS_DIR / "shared.yaml"


def _load_yaml(path: Path, cache: dict[Path, dict[str, Any]]) -> dict[str, Any]:
    resolved_path = path.resolve()
    if resolved_path not in cache:
        with open(resolved_path, "r", encoding="utf-8") as f:
            cache[resolved_path] = yaml.safe_load(f)
    return cache[resolved_path]


def _resolve_json_pointer(doc: dict[str, Any], pointer: str) -> Any:
    if not pointer or pointer == "/":
        return doc
    current: Any = doc
    for part in pointer.lstrip("/").split("/"):
        unescaped = part.replace("~1", "/").replace("~0", "~")
        current = current[unescaped]
    return current


def _apply_nullable(schema: Any) -> Any:
    if not isinstance(schema, dict):
        return schema
    if "enum" in schema or "oneOf" in schema or "anyOf" in schema:
        return {"anyOf": [schema, {"type": "null"}]}
    if "type" in schema:
        current_type = schema["type"]
        if isinstance(current_type, list):
            if "null" not in current_type:
                schema["type"] = [*current_type, "null"]
        else:
            schema["type"] = [current_type, "null"]
        return schema
    return {"anyOf": [schema, {"type": "null"}]}


def _convert_node(
    node: Any,
    current_file: Path,
    doc_cache: dict[Path, dict[str, Any]],
) -> Any:
    """
    Recursively resolves local ('#/...') and cross-file ('./shared.yaml#/...')
    OpenAPI refs and converts OpenAPI 3.0 'nullable: true' into JSON Schema.
    """
    if isinstance(node, dict):
        is_nullable = node.get("nullable") is True

        if "$ref" in node:
            ref_str = str(node["$ref"])
            file_part, _, pointer_part = ref_str.partition("#")
            target_file = (
                (current_file.parent / file_part).resolve()
                if file_part
                else current_file.resolve()
            )
            target_doc = _load_yaml(target_file, doc_cache)
            target_node = copy.deepcopy(_resolve_json_pointer(target_doc, pointer_part))
            resolved = _convert_node(target_node, target_file, doc_cache)
            return _apply_nullable(resolved) if is_nullable else resolved

        result: dict[str, Any] = {}
        for k, v in node.items():
            if k == "nullable":
                continue
            result[str(k)] = _convert_node(v, current_file, doc_cache)

        if is_nullable:
            return _apply_nullable(result)

        return result

    if isinstance(node, list):
        return [_convert_node(item, current_file, doc_cache) for item in node]

    return node


def _build_schema_validator(
    spec_path: Path,
    doc_cache: dict[Path, dict[str, Any]],
) -> Callable[[dict[str, Any], str], None]:
    spec = _load_yaml(spec_path, doc_cache)
    raw_schemas: dict[str, Any] = spec["components"]["schemas"]

    def _validate(instance: Any, schema_name: str) -> None:
        assert schema_name in raw_schemas, f"Schema '{schema_name}' not found in {spec_path.name}"
        json_schema = _convert_node(copy.deepcopy(raw_schemas[schema_name]), spec_path, doc_cache)
        assert isinstance(json_schema, dict)
        jsonschema.validate(
            instance=instance,
            schema=json_schema,
            format_checker=jsonschema.FormatChecker(),
        )

    return _validate


@pytest.fixture(scope="session")
def doc_cache() -> dict[Path, dict[str, Any]]:
    return {}


@pytest.fixture(scope="session")
def openapi_spec(doc_cache: dict[Path, dict[str, Any]]) -> dict[str, Any]:
    """Loads the raw REST OpenAPI specification from contracts/openapi.yaml."""
    return _load_yaml(OPENAPI_PATH, doc_cache)


@pytest.fixture(scope="session")
def kafka_spec(doc_cache: dict[Path, dict[str, Any]]) -> dict[str, Any]:
    """Loads the raw Kafka OpenAPI specification from contracts/openapi-kafka.yaml."""
    return _load_yaml(KAFKA_OPENAPI_PATH, doc_cache)


@pytest.fixture(scope="session")
def shared_spec(doc_cache: dict[Path, dict[str, Any]]) -> dict[str, Any]:
    """Loads shared schemas/enums from contracts/shared.yaml."""
    return _load_yaml(SHARED_OPENAPI_PATH, doc_cache)


@pytest.fixture(scope="session")
def validate_openapi_schema(
    doc_cache: dict[Path, dict[str, Any]],
) -> Callable[[Any, str], None]:
    """Validates a payload against components.schemas.<schema_name> in contracts/openapi.yaml."""
    return _build_schema_validator(OPENAPI_PATH, doc_cache)


@pytest.fixture(scope="session")
def validate_kafka_schema(
    doc_cache: dict[Path, dict[str, Any]],
) -> Callable[[Any, str], None]:
    """Validates a payload against components.schemas.<schema_name> in contracts/openapi-kafka.yaml."""
    return _build_schema_validator(KAFKA_OPENAPI_PATH, doc_cache)
