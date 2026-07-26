"""EVERY structured-output schema, checked against OpenAI's strict subset. Offline.

WHY THIS FILE EXISTS. `InterviewResult` shipped to production with a schema the API rejects:

    Invalid schema for response_format 'InterviewResult':
    context=('properties', 'profile'), $ref cannot have keywords {'description'}

Pydantic puts a field's description beside the `$ref` when the field's type is another model.
Nothing caught it. The unit tests build schemas and assert on their shape, but "the shape we
happen to produce" is not "the shape the API accepts", and the only thing that knew the
difference was OpenAI — at request time, in a live deployment, on a family's message.

So this is a CONTRACT test, not a shape test: it encodes the published rules from
https://developers.openai.com/api/docs/guides/structured-outputs#supported-schemas and runs them
over every model the gateway can be handed. A new model, or a new field on an existing one, is
checked the moment it is added, for free, with no network and no key.

It cannot prove acceptance — only OpenAI can — but every rule below is one the API states, and
each one was cheaper to write than the incident that would have taught it.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel

from app.assistant import AssistantReply
from app.interview import InterviewResult
from app.llm.schemas import ExtractedEvent, ExtractionResult, to_strict_json_schema
from app.reports import ReportDraft

# Every model the gateway is asked to enforce. Keep in step with `schema=` at the call sites;
# `test_every_gateway_schema_is_covered_here` fails when they drift apart.
STRICT_MODELS: list[type[BaseModel]] = [
    AssistantReply,
    ExtractedEvent,
    ExtractionResult,
    InterviewResult,
    ReportDraft,
]

# "Composition: allOf, not, dependentRequired, dependentSchemas, if, then, else" — unsupported
# outright. Pydantic reaches for `allOf` on its own, wrapping a lone `$ref`, which is the same
# family of bug as the one that caused the incident.
UNSUPPORTED_KEYWORDS = frozenset(
    {"allOf", "not", "dependentRequired", "dependentSchemas", "if", "then", "else"}
)

MAX_NESTING_DEPTH = 10
MAX_PROPERTIES = 5000
MAX_TOTAL_STRING_CHARS = 120_000
MAX_ENUM_VALUES = 1000


def walk(node: Any, path: str = "$", depth: int = 0):
    """Every dict in the schema, with the path that reaches it. Paths make failures findable."""
    if isinstance(node, dict):
        yield path, node, depth
        for key, value in node.items():
            # `properties` keys are field NAMES, not schema keywords: descending into them as
            # if they were adds a phantom level to every depth measurement.
            step = f"{path}.{key}" if key != "properties" else path
            yield from walk(value, step, depth + (1 if key == "properties" else 0))
    elif isinstance(node, list):
        for index, item in enumerate(node):
            yield from walk(item, f"{path}[{index}]", depth)


@pytest.mark.parametrize("model", STRICT_MODELS, ids=lambda m: m.__name__)
class TestStrictSubset:
    """One class per rule, run against every model, so a failure names both."""

    def test_a_ref_is_never_emitted_with_a_sibling_keyword(self, model: type[BaseModel]) -> None:
        """THE REGRESSION. `$ref cannot have keywords {'description'}` — a live 400."""
        for path, node, _ in walk(to_strict_json_schema(model)):
            if "$ref" in node:
                assert set(node) == {"$ref"}, (
                    f"{model.__name__} at {path}: a $ref must stand alone, "
                    f"found siblings {sorted(set(node) - {'$ref'})}"
                )

    def test_the_root_is_an_object_and_not_an_anyof(self, model: type[BaseModel]) -> None:
        """ "The root level object of a schema must be an object, and not use anyOf."""
        schema = to_strict_json_schema(model)
        assert schema.get("type") == "object", f"{model.__name__} root is not an object"
        assert "anyOf" not in schema, f"{model.__name__} root uses anyOf"

    def test_every_object_forbids_additional_properties(self, model: type[BaseModel]) -> None:
        """ "additionalProperties: false must always be set in objects."""
        for path, node, _ in walk(to_strict_json_schema(model)):
            if node.get("type") == "object":
                assert node.get("additionalProperties") is False, (
                    f"{model.__name__} at {path}: additionalProperties must be false"
                )

    def test_every_property_is_required(self, model: type[BaseModel]) -> None:
        """ "All fields must be required." Optionality is anyOf[T, null], never omission."""
        for path, node, _ in walk(to_strict_json_schema(model)):
            if node.get("type") == "object":
                declared = set(node.get("properties", {}))
                required = set(node.get("required", []))
                assert declared == required, (
                    f"{model.__name__} at {path}: not required -> {sorted(declared - required)}"
                )

    def test_no_unsupported_composition_keyword_appears(self, model: type[BaseModel]) -> None:
        """allOf/not/if/then/else are rejected outright. Pydantic emits `allOf` unprompted."""
        for path, node, _ in walk(to_strict_json_schema(model)):
            found = UNSUPPORTED_KEYWORDS & set(node)
            assert not found, f"{model.__name__} at {path}: unsupported {sorted(found)}"

    def test_no_default_survives(self, model: type[BaseModel]) -> None:
        """Strict mode has no notion of a default; every field is always present."""
        for path, node, _ in walk(to_strict_json_schema(model)):
            assert "default" not in node, f"{model.__name__} at {path}: `default` must be stripped"

    def test_it_fits_inside_the_documented_limits(self, model: type[BaseModel]) -> None:
        """5000 properties, 10 levels, 120k of names, 1000 enum values.

        Nowhere near any of them today. The assertion is here because `conditions` and
        `medications` are family-supplied lists and the care profile is the schema most likely
        to grow a field a year from now.
        """
        schema = to_strict_json_schema(model)
        properties = enums = depth = 0
        strings: list[str] = []
        for _, node, node_depth in walk(schema):
            depth = max(depth, node_depth)
            names = list(node.get("properties", {}))
            properties += len(names)
            strings.extend(names)
            values = node.get("enum", [])
            enums += len(values)
            strings.extend(str(v) for v in values)
        strings.extend(schema.get("$defs", {}))

        assert depth <= MAX_NESTING_DEPTH, f"{model.__name__}: {depth} levels deep"
        assert properties <= MAX_PROPERTIES, f"{model.__name__}: {properties} properties"
        assert enums <= MAX_ENUM_VALUES, f"{model.__name__}: {enums} enum values"
        assert sum(len(s) for s in strings) <= MAX_TOTAL_STRING_CHARS


def test_every_gateway_schema_is_covered_here() -> None:
    """The list above must not fall behind the call sites.

    A model that is handed to the gateway but never checked here is exactly the position
    `InterviewResult` was in, and the cost of that was a silent production outage rather than a
    red test. Greps the source for `schema=` instead of trusting anyone to remember.
    """
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[1] / "app"
    names: set[str] = set()
    for path in root.rglob("*.py"):
        names.update(re.findall(r"\bschema=([A-Z][A-Za-z0-9_]*)", path.read_text(encoding="utf-8")))

    covered = {m.__name__ for m in STRICT_MODELS}
    assert not (names - covered), (
        f"handed to the gateway but unchecked: {sorted(names - covered)} — add them to "
        f"STRICT_MODELS, they are one nested field away from a 400 in production"
    )
