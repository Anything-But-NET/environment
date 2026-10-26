import copy
from pathlib import Path
from typing import Any, Callable

import jsonschema
import pytest
import yaml

OPENAPI_PATH = Path(__file__).resolve().parent.parent.parent / "contracts" / "openapi.yaml"


def _convert_node(node: Any, root_schemas: dict[str, Any]) -> Any:
    """
    Recursively resolves local '#/components/schemas/...' refs and converts
    OpenAPI 3.0 'nullable: true' into standard JSON Schema format.
    """
    if isinstance(node, dict):
        if "$ref" in node:
            ref_path = str(node["$ref"])
            schema_name = ref_path.split("/")[-1]
            resolved = copy.deepcopy(root_schemas[schema_name])
            return _convert_node(resolved, root_schemas)

        result: dict[str, Any] = {}
        for k, v in node.items():
            if k == "nullable":
                continue
            result[str(k)] = _convert_node(v, root_schemas)

        if node.get("nullable") is True and "type" in result:
            current_type = result["type"]
            if isinstance(current_type, list):
                if "null" not in current_type:
                    result["type"] = [*current_type, "null"]
            else:
                result["type"] = [current_type, "null"]

        return result

    if isinstance(node, list):
        return [_convert_node(item, root_schemas) for item in node]

    return node


def _convert_openapi_to_jsonschema(
    schema_node: dict[str, Any], root_schemas: dict[str, Any]
) -> dict[str, Any]:
    converted = _convert_node(schema_node, root_schemas)
    assert isinstance(converted, dict)
    return converted


@pytest.fixture(scope="session")
def openapi_spec() -> dict[str, Any]:
    """Loads the raw OpenAPI specification from contracts/openapi.yaml."""
    with open(OPENAPI_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


@pytest.fixture(scope="session")
def validate_openapi_schema(
    openapi_spec: dict[str, Any],
) -> Callable[[dict[str, Any], str], None]:
    """
    Returns a validator function: validate(instance, schema_name).
    Validates any dict/payload against components.schemas.<schema_name> from openapi.yaml.
    """
    raw_schemas: dict[str, Any] = openapi_spec["components"]["schemas"]

    def _validate(instance: dict[str, Any], schema_name: str) -> None:
        assert schema_name in raw_schemas, f"Schema '{schema_name}' not found in openapi.yaml"
        json_schema = _convert_openapi_to_jsonschema(raw_schemas[schema_name], raw_schemas)
        jsonschema.validate(instance=instance, schema=json_schema, format_checker=jsonschema.FormatChecker())

    return _validate
