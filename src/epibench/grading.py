from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any


def grade_submission(config: dict[str, Any], submission: Any) -> dict[str, Any]:
    """Grade a structured EpiBench answer against reconstructed public references."""
    task_id = _required_string(config, "task_id")
    fields = config.get("fields")
    variants = config.get("accepted_variants")
    if not isinstance(fields, dict) or not fields:
        raise ValueError("fallback grader config has no fields")
    if not isinstance(variants, list) or not variants:
        raise ValueError("fallback grader config has no accepted variants")
    if not isinstance(submission, dict):
        return _invalid_result(task_id, "submission_root_must_be_an_object")

    required_keys = set(fields)
    missing_keys = sorted(required_keys - submission.keys())
    if missing_keys:
        return {
            "eval_id": task_id,
            "fallback_grader": True,
            "official_verifier_available": False,
            "passed": False,
            "score": 0.0,
            "status": "missing_required_fields",
            "missing_fields": missing_keys,
        }

    candidate_results: list[dict[str, Any]] = []
    for index, variant in enumerate(variants):
        if not isinstance(variant, dict) or not isinstance(variant.get("answer"), dict):
            raise ValueError(f"accepted variant {index} has no answer object")
        reference = variant["answer"]
        if set(reference) != required_keys:
            raise ValueError(f"accepted variant {index} fields do not match grader fields")

        details = []
        for field_name, field_config in fields.items():
            if not isinstance(field_config, dict):
                raise ValueError(f"invalid field config for {field_name}")
            details.append(
                _compare_field(
                    field_name,
                    submission[field_name],
                    reference[field_name],
                    field_config,
                )
            )
        passed_count = sum(detail["passed"] is True for detail in details)
        candidate_results.append(
            {
                "variant_index": index,
                "passed": passed_count == len(details),
                "score": passed_count / len(details),
                "details": details,
            }
        )

    best = max(
        candidate_results,
        key=lambda result: (result["passed"], result["score"], -result["variant_index"]),
    )
    extra_fields = sorted(submission.keys() - required_keys)
    return {
        "eval_id": task_id,
        "fallback_grader": True,
        "official_verifier_available": False,
        "passed": best["passed"],
        "score": best["score"],
        "status": "passed" if best["passed"] else "field_mismatch",
        "matched_variant": best["variant_index"] if best["passed"] else None,
        "extra_fields_ignored": extra_fields,
        "details": best["details"],
    }


def _compare_field(
    name: str,
    actual: Any,
    expected: Any,
    config: dict[str, Any],
) -> dict[str, Any]:
    comparison = config.get("comparison")
    if comparison == "numeric":
        return _compare_numeric(name, actual, expected, config)
    if comparison == "exact":
        return _compare_exact(name, actual, expected, config)
    raise ValueError(f"unsupported comparison for {name}: {comparison!r}")


def _compare_numeric(
    name: str,
    actual: Any,
    expected: Any,
    config: dict[str, Any],
) -> dict[str, Any]:
    try:
        actual_number = _decimal(actual)
        expected_number = _decimal(expected)
        absolute_tolerance = _decimal(config.get("absolute_tolerance", 0))
        relative_tolerance = _decimal(config.get("relative_tolerance", 0))
    except (InvalidOperation, TypeError, ValueError):
        return {
            "field": name,
            "comparison": "numeric",
            "passed": False,
            "status": "not_finite_numeric",
            "actual": actual,
        }

    if config.get("integer") is True and actual_number != actual_number.to_integral_value():
        return {
            "field": name,
            "comparison": "numeric",
            "passed": False,
            "status": "expected_integer",
            "actual": actual,
        }

    tolerance = max(absolute_tolerance, abs(expected_number) * relative_tolerance)
    difference = abs(actual_number - expected_number)
    return {
        "field": name,
        "comparison": "numeric",
        "passed": difference <= tolerance,
        "status": "within_tolerance" if difference <= tolerance else "outside_tolerance",
        "actual": str(actual_number),
        "expected": str(expected_number),
        "absolute_difference": str(difference),
        "allowed_difference": str(tolerance),
    }


def _compare_exact(
    name: str,
    actual: Any,
    expected: Any,
    config: dict[str, Any],
) -> dict[str, Any]:
    actual_text = str(actual).strip()
    expected_text = str(expected).strip()
    if config.get("case_sensitive", True) is False:
        actual_text = actual_text.casefold()
        expected_text = expected_text.casefold()
    passed = actual_text == expected_text
    return {
        "field": name,
        "comparison": "exact",
        "passed": passed,
        "status": "exact_match" if passed else "value_mismatch",
        "actual": actual,
        "expected": expected,
    }


def _decimal(value: Any) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise TypeError("boolean and null values are not numeric answers")
    number = Decimal(str(value).strip())
    if not number.is_finite():
        raise ValueError("numeric answer must be finite")
    return number


def _required_string(config: dict[str, Any], key: str) -> str:
    value = config.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"fallback grader config has no {key}")
    return value


def _invalid_result(task_id: str, status: str) -> dict[str, Any]:
    return {
        "eval_id": task_id,
        "fallback_grader": True,
        "official_verifier_available": False,
        "passed": False,
        "score": 0.0,
        "status": status,
    }
