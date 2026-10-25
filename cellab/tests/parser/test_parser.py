from pathlib import Path

import pytest

from parser import SpecError, load_spec, parse_spec

EXAMPLES = Path(__file__).parents[2] / "parser" / "examples"

LIFE = """
schema_version: 1
name: conway-life
world: {size: [8, 8], boundary: torus}
neighborhood: {type: moore, radius: 1}
cell_types:
  dead: {symbol: ".", color: "#ffffff"}
  alive: {symbol: "#", color: "#111111"}
rules:
  - when: {cell: alive, count: {alive: [2, 3]}}
    then: alive
  - when: {cell: dead, count: {alive: 3}}
    then: alive
  - otherwise: dead
initial_state:
  fill: dead
  cells:
    alive: [[3, 3], [3, 4], [3, 5]]
"""


def assert_error(text: str, *fragments: str) -> SpecError:
    with pytest.raises(SpecError) as exc:
        parse_spec(text)
    message = str(exc.value)
    for fragment in fragments:
        assert fragment in message, message
    return exc.value


def test_parse_metadata():
    spec = parse_spec(LIFE)
    assert spec.name == "conway-life"
    assert spec.cell_type_names == ("dead", "alive")
    assert spec.world.size == [8, 8]
    assert spec.world.grid_shape == (8, 8)
    assert spec.initial_state is not None


def test_all_examples_parse():
    paths = sorted(EXAMPLES.glob("*.cell.yaml"))
    assert len(paths) == 4
    for path in paths:
        spec = load_spec(path)
        assert spec.name == path.name.removesuffix(".cell.yaml")


def test_load_spec_from_path():
    spec = load_spec(EXAMPLES / "conway-life.cell.yaml")
    assert spec.name == "conway-life"


def test_empty_document_is_rejected():
    assert_error("", "empty")


def test_top_level_must_be_a_mapping():
    assert_error("- one\n- two\n", "top-level", "mapping")


def test_invalid_yaml_reports_position():
    assert_error("schema_version: [1", "line", "column")


def test_duplicate_yaml_keys_are_rejected():
    assert_error("schema_version: 1\nschema_version: 1\n", "duplicate key", "schema_version")


def test_unknown_top_level_key_is_rejected():
    assert_error(LIFE + "\nfoo: 1\n", "foo", "Extra inputs are not permitted")


def test_both_rule_forms_are_rejected():
    text = LIFE.replace(
        "rules:",
        "rule: {type: totalistic, counted: alive, transitions: {}}\nrules:",
        1,
    )
    assert_error(text, "exactly one of 'rules' and 'rule'")


def test_neither_rule_form_is_rejected():
    text = LIFE.split("rules:")[0]
    assert_error(text, "exactly one of 'rules' and 'rule'")


def test_missing_otherwise_is_rejected():
    text = LIFE.replace("  - otherwise: dead\n", "")
    assert_error(text, "otherwise")


def test_otherwise_must_be_last():
    text = LIFE.replace("  - otherwise: dead\n", "").replace(
        "rules:\n", "rules:\n  - otherwise: dead\n", 1
    )
    assert_error(text, "must be last")


def test_unknown_cell_type_in_rule_has_a_path():
    text = LIFE.replace("when: {cell: alive", "when: {cell: aliv", 1)
    assert_error(text, "rules[0].when.cell", "aliv")


def test_count_above_neighbourhood_size_is_rejected():
    text = LIFE.replace("count: {alive: [2, 3]}", "count: {alive: 9}", 1)
    assert_error(text, "rules[0].when.count.alive", "can never hold")


def test_count_minimum_above_maximum_is_rejected():
    text = LIFE.replace("count: {alive: [2, 3]}", "count: {alive: [3, 2]}", 1)
    assert_error(text, "rules[0].when.count.alive", "minimum greater than maximum")


def test_count_range_needs_a_bound():
    text = LIFE.replace("count: {alive: [2, 3]}", "count: {alive: [null, null]}", 1)
    assert_error(text, "rules[0].when.count.alive", "at least one bound")


def test_random_probabilities_must_sum_to_one():
    text = LIFE.replace(
        "    then: alive\n",
        "    then: {random: {alive: 0.5, dead: 0.2}}\n",
        1,
    )
    assert_error(text, "rules[0].then.random", "sum to 0.7")


def test_initial_state_out_of_bounds_has_a_path():
    text = LIFE.replace("alive: [[3, 3], [3, 4], [3, 5]]", "alive: [[99, 0]]")
    assert_error(text, "initial_state.cells.alive[0]", "outside the grid")


def test_initial_state_conflicting_assignment_is_rejected():
    text = LIFE.replace(
        "    alive: [[3, 3], [3, 4], [3, 5]]",
        "    alive: [[0, 0]]\n    dead: [[0, 0]]",
    )
    assert_error(text, "initial_state.cells.dead[0]", "already assigned")


def test_symbol_must_be_one_character():
    text = LIFE.replace('symbol: "."', 'symbol: ".."', 1)
    assert_error(text, "cell_types.dead.symbol", "one non-whitespace character")


def test_color_must_be_six_digit_hex():
    text = LIFE.replace('color: "#ffffff"', 'color: "#fff"', 1)
    assert_error(text, "cell_types.dead.color", "6-digit hex")


def test_line_neighbourhood_requires_one_dimension():
    text = LIFE.replace("{type: moore, radius: 1}", "{type: line, radius: 1}")
    assert_error(text, "neighborhood", "1D world")


def test_custom_points_must_match_world_dimensions():
    text = LIFE.replace(
        "{type: moore, radius: 1}", "{type: custom, points: [[-1], [1]]}"
    )
    assert_error(text, "neighborhood", "custom points are 1D")


def test_fixed_boundary_value_must_exist():
    text = LIFE.replace("boundary: torus", "boundary: {type: fixed, value: nope}")
    assert_error(text, "world.boundary.value", "nope")


def test_fixed_boundary_requires_a_value():
    text = LIFE.replace("boundary: torus", "boundary: {type: fixed}")
    assert_error(text, "world.boundary.value", "Field required")


def test_union_target_error_paths_are_cleaned():
    text = LIFE.replace("    then: alive\n", "    then: {foo: 1}\n", 1)
    exc = assert_error(text, "rules[0].then.foo", "Extra inputs are not permitted")
    assert "RandomTargetSpec" not in str(exc)


def test_schema_version_must_be_one():
    text = LIFE.replace("schema_version: 1", "schema_version: 2")
    assert_error(text, "schema_version")


def test_cell_type_metadata_is_required():
    text = LIFE.replace('{symbol: ".", color: "#ffffff"}', '{color: "#ffffff"}', 1)
    assert_error(text, "cell_types.dead.symbol", "Field required")


def test_lookup_pattern_length_must_match_neighbourhood():
    text = LIFE.replace(
        """rules:
  - when: {cell: alive, count: {alive: [2, 3]}}
    then: alive
  - when: {cell: dead, count: {alive: 3}}
    then: alive
  - otherwise: dead""",
        """rule:
  type: lookup
  table:
    - {pattern: [dead, alive], then: alive}
  default: dead""",
    )
    assert_error(text, "rule.table[0].pattern", "neighbourhood has 8 cells")


def test_duplicate_lookup_patterns_are_rejected():
    path = EXAMPLES / "rule-110.cell.yaml"
    text = path.read_text(encoding="utf-8")
    text = text.replace(
        "    - {pattern: [dead, dead, alive], then: alive}\n",
        "    - {pattern: [dead, dead, dead], then: alive}\n",
        1,
    )
    assert_error(text, "rule.table[1].pattern", "duplicate pattern")


def test_totalistic_transition_count_is_validated():
    path = EXAMPLES / "life-totalistic.cell.yaml"
    text = path.read_text(encoding="utf-8").replace("dead: {3: alive}", "dead: {9: alive}")
    assert_error(text, "rule.transitions.dead.9", "impossible")


def test_unknown_rule_family_is_rejected():
    text = LIFE.replace(
        """rules:
  - when: {cell: alive, count: {alive: [2, 3]}}
    then: alive
  - when: {cell: dead, count: {alive: 3}}
    then: alive
  - otherwise: dead""",
        "rule: {type: bogus}",
    )
    assert_error(text, "rule", "does not match any of the expected tags")


def test_unknown_neighbourhood_is_rejected():
    text = LIFE.replace("{type: moore, radius: 1}", "{type: bogus}")
    assert_error(text, "neighborhood", "does not match any of the expected tags")
