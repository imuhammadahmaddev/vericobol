"""Property test for the per-Case pass criterion of the PBT_Verifier.

Runs against the real GnuCOBOL binary (use the Docker image; no cobc locally).
"""

from __future__ import annotations

from decimal import Decimal
from functools import lru_cache

from hypothesis import HealthCheck, given, settings, strategies as st

from app import reference_models
from app.pbt_verifier import (
    MAX_COUNTEREXAMPLES,
    MIN_CASES,
    _OUTPUT_FIELDS,
    generate_cases,
    run_verification,
)

PRESETS = tuple(_OUTPUT_FIELDS)
SEEDS = (11, 22, 33)

# Nonzero perturbations, in whole cents (+/- 0.01 up to +/- 500.00).
_DELTAS = st.one_of(
    st.sampled_from([Decimal("0.01"), Decimal("-0.01"), Decimal("0.02"), Decimal("-1.00")]),
    st.integers(1, 50_000).flatmap(
        lambda c: st.sampled_from([1, -1]).map(lambda s: Decimal(s * c).scaleb(-2))
    ),
)


@lru_cache(maxsize=None)
def _cases(preset_id: str, seed: int):
    return generate_cases(preset_id, MIN_CASES, seed)


def _key(inputs) -> tuple:
    return tuple((k, str(v)) for k, v in inputs.items())


def _make_target(preset_id: str, field: str, perturb: dict[tuple, Decimal]):
    ref = reference_models.REGISTRY[preset_id]

    def target(**inputs):
        out = dict(ref(**inputs))
        delta = perturb.get(_key(inputs))
        if delta is not None:
            out[field] = Decimal(out[field]) + delta
        return out

    return target


# Feature: cobol-verification-engine, Property 12: A Case passes if and only if every output field matches at penny precision
# **Validates: Requirements 12.6, 12.8**
@settings(max_examples=100, database=None, deadline=None,
          suppress_health_check=list(HealthCheck))
@given(data=st.data(), preset_id=st.sampled_from(PRESETS), seed=st.sampled_from(SEEDS))
def test_case_passes_iff_all_fields_match(data, preset_id, seed):
    cases = _cases(preset_id, seed)
    field = data.draw(st.sampled_from(_OUTPUT_FIELDS[preset_id]), label="field")
    indices = data.draw(
        st.sets(st.integers(0, len(cases) - 1), max_size=MAX_COUNTEREXAMPLES + 3),
        label="indices")
    # Zero deltas are mixed in: they must change nothing.
    deltas = {
        i: data.draw(st.one_of(_DELTAS, st.just(Decimal("0.00"))), label=f"delta{i}")
        for i in sorted(indices)
    }
    perturb = {_key(cases[i].inputs): d for i, d in deltas.items()}
    expected_failing = [i for i in sorted(deltas) if deltas[i] != 0]

    report = run_verification(preset_id, None, _make_target(preset_id, field, perturb),
                              num_cases=MIN_CASES, seed=seed)

    assert report.total_cases == len(cases)
    assert report.failed_cases == len(expected_failing)
    assert report.passed_cases == len(cases) - len(expected_failing)
    assert report.verified is (not expected_failing)

    # Counterexamples: the first (up to 5) perturbed Cases, in Case order.
    shown = expected_failing[:MAX_COUNTEREXAMPLES]
    assert len(report.counterexamples) == len(shown)
    for ce, i in zip(report.counterexamples, shown):
        assert ce.differing_field == field
        assert ce.inputs == {k: format(Decimal(v), "f") for k, v in cases[i].inputs.items()}
        assert Decimal(ce.penny_delta) == abs(deltas[i])
        # The reported outputs differ by exactly the perturbation.
        diff = Decimal(ce.python_output[field]) - Decimal(ce.cobol_output[field])
        assert diff == deltas[i]
