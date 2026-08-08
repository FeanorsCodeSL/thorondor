import pytest

import orchestrator.schema_contract as contract


def _schema():
    return {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "stock": {"type": "integer"},
            "tags": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["name", "stock"],
    }


def test_schema_contract_accepts_only_the_bounded_local_subset():
    assert contract.validate_extraction_schema(_schema()) == _schema()

    for schema, message in [
        ({"type": "object", "properties": {}, "$ref": "https://example.com/x"}, "keyword"),
        ({"type": "string"}, "root type"),
        ({"type": "object", "properties": {"x": {"type": ["string", "null"]}}}, "type"),
        ({"type": "object", "properties": {"x": {"type": "array"}}}, "requires items"),
    ]:
        with pytest.raises(contract.SchemaContractError, match=message):
            contract.validate_extraction_schema(schema)


def test_schema_byte_depth_and_property_limits_are_enforced(monkeypatch):
    monkeypatch.setattr(contract, "MAX_EXTRACTION_SCHEMA_BYTES", 64)
    with pytest.raises(contract.SchemaContractError, match="UTF-8 bytes"):
        contract.validate_extraction_schema(_schema())

    monkeypatch.setattr(contract, "MAX_EXTRACTION_SCHEMA_BYTES", 16_384)
    nested = {"type": "string"}
    for index in range(contract.MAX_EXTRACTION_SCHEMA_DEPTH):
        nested = {
            "type": "object",
            "properties": {f"p{index}": nested},
        }
    with pytest.raises(contract.SchemaContractError, match="depth"):
        contract.validate_extraction_schema(nested)

    monkeypatch.setattr(contract, "MAX_EXTRACTION_SCHEMA_PROPERTIES", 1)
    with pytest.raises(contract.SchemaContractError, match="properties"):
        contract.validate_extraction_schema(_schema())


def test_schema_enum_description_and_name_limits_are_enforced(monkeypatch):
    monkeypatch.setattr(contract, "MAX_EXTRACTION_SCHEMA_ENUM_VALUES", 1)
    with pytest.raises(contract.SchemaContractError, match="enum"):
        contract.validate_extraction_schema(
            {
                "type": "object",
                "properties": {
                    "state": {"type": "string", "enum": ["in", "out"]},
                },
            }
        )
    with pytest.raises(contract.SchemaContractError, match="does not match"):
        contract.validate_extraction_schema(
            {
                "type": "object",
                "properties": {"count": {"type": "integer", "enum": ["one"]}},
            }
        )

    monkeypatch.setattr(contract, "MAX_EXTRACTION_SCHEMA_DESCRIPTION_BYTES", 4)
    with pytest.raises(contract.SchemaContractError, match="description"):
        contract.validate_extraction_schema(
            {
                "type": "object",
                "properties": {"name": {"type": "string", "description": "longer"}},
            }
        )
    with pytest.raises(contract.SchemaContractError, match="property name"):
        contract.validate_extraction_schema(
            {
                "type": "object",
                "properties": {"x" * 129: {"type": "string"}},
            }
        )


def test_extracted_values_fail_closed_on_missing_extra_and_wrong_fields():
    assert contract.validate_extracted_value(
        _schema(),
        {"name": "Widget", "stock": 4, "tags": ["new"]},
    ) == []

    assert contract.validate_extracted_value(
        _schema(),
        {"name": "Widget", "stock": True, "extra": "x"},
    ) == [
        ("/extra", "additional_property"),
        ("/stock", "type_mismatch"),
    ]
    assert contract.validate_extracted_value(_schema(), {"name": "Widget"}) == [
        ("/stock", "required_missing")
    ]


def test_scalar_fields_use_escaped_json_pointer_paths():
    assert contract.scalar_fields({"a/b": {"x~y": ["value"]}}) == [
        ("/a~1b/x~0y/0", "value")
    ]
