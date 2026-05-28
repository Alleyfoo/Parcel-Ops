"""Parcel Ops Control Tower — LLM vs Regex classification comparison.

Demonstrates where LLMs outperform regex-based classification:
- Ambiguous product descriptions
- Multi-language inputs
- Context-aware classification
- Natural language explanations

This module provides mock LLM responses for demonstration purposes.
In production, this would integrate with an actual LLM API (OpenAI, Anthropic, local model).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class ClassificationResult:
    method: str
    hs_code: str
    confidence: float
    reasoning: str
    correct: bool


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

LLM_TEST_CASES = [
    {
        "id": "ambiguous_1",
        "description": "Plastic housing for electronic device",
        "context": "Shipment contains plastic enclosures that house electronic circuit boards",
        "regex_result": ClassificationResult(
            method="regex",
            hs_code="3926.90",
            confidence=0.72,
            reasoning="Keyword 'plastic' matches Chapter 39 (Plastics). 'Housing' is generic term. Classified as plastic article.",
            correct=False,
        ),
        "llm_result": ClassificationResult(
            method="llm",
            hs_code="8542.31",
            confidence=0.94,
            reasoning="The plastic housing is specifically designed to contain electronic circuit boards, making it a part of electronic equipment. The essential character is electronic, not plastic. Chapter 85 (Electrical machinery) is correct. The housing is an integral component, not a standalone plastic article.",
            correct=True,
        ),
    },
    {
        "id": "ambiguous_2",
        "description": "Metal frame with rubber mounting for machinery",
        "context": "Steel support structure with vibration-dampening rubber mounts for industrial equipment",
        "regex_result": ClassificationResult(
            method="regex",
            hs_code="7308.90",
            confidence=0.68,
            reasoning="Keyword 'metal' and 'steel' match Chapter 73 (Iron/steel articles). 'Frame' suggests structural component.",
            correct=False,
        ),
        "llm_result": ClassificationResult(
            method="llm",
            hs_code="8431.49",
            confidence=0.91,
            reasoning="This is a specialized mounting system for machinery, not a general steel structure. The rubber mounts indicate it's designed for vibration control in industrial equipment. Chapter 84 (Machinery parts) is appropriate. The metal frame is incidental to its function as a machinery mounting system.",
            correct=True,
        ),
    },
    {
        "id": "multilang_1",
        "description": "Elektronische Spannungsregler, integrierte Schaltkreise",
        "context": "German invoice description",
        "regex_result": ClassificationResult(
            method="regex",
            hs_code="8504.40",
            confidence=0.45,
            reasoning="Partial match on 'Spannungsregler' (voltage regulator). Low confidence due to German language. May need manual review.",
            correct=False,
        ),
        "llm_result": ClassificationResult(
            method="llm",
            hs_code="8542.31",
            confidence=0.96,
            reasoning="German: 'Elektronische Spannungsregler, integrierte Schaltkreise' = 'Electronic voltage regulators, integrated circuits'. Clear match to HS 8542.31 (Processors and controllers). The term 'integrierte Schaltkreise' (integrated circuits) is definitive.",
            correct=True,
        ),
    },
    {
        "id": "multilang_2",
        "description": "塑料电子元件外壳",
        "context": "Chinese invoice description",
        "regex_result": ClassificationResult(
            method="regex",
            hs_code="3926.90",
            confidence=0.38,
            reasoning="Chinese characters detected. '塑料' (plastic) matches Chapter 39. Cannot parse full description. Manual translation required.",
            correct=False,
        ),
        "llm_result": ClassificationResult(
            method="llm",
            hs_code="8542.31",
            confidence=0.93,
            reasoning="Chinese: '塑料电子元件外壳' = 'Plastic electronic component housing/shell'. While plastic is mentioned, the essential character is electronic component housing. This is a part for electronic integrated circuits (Chapter 85), not a standalone plastic article.",
            correct=True,
        ),
    },
    {
        "id": "ambiguous_3",
        "description": "Ceramic coffee mug with electronic heating element",
        "context": "Smart mug with built-in temperature control and Bluetooth connectivity",
        "regex_result": ClassificationResult(
            method="regex",
            hs_code="6911.10",
            confidence=0.81,
            reasoning="Keyword 'ceramic' and 'mug' strongly match Chapter 69 (Ceramic tableware). 'Coffee mug' is clear product type.",
            correct=False,
        ),
        "llm_result": ClassificationResult(
            method="llm",
            hs_code="8516.71",
            confidence=0.89,
            reasoning="This is not a simple ceramic mug. The electronic heating element, temperature control, and Bluetooth connectivity make it an electric heating appliance. Chapter 85 (Electrical machinery) is correct. The ceramic is just the container; the essential character is the electronic heating and control system.",
            correct=True,
        ),
    },
    {
        "id": "context_1",
        "description": "Replacement parts kit",
        "context": "Kit contains: 2x circuit boards, 1x power supply, 5x connectors, 1x instruction manual. For repair of industrial control system.",
        "regex_result": ClassificationResult(
            method="regex",
            hs_code="8534.00",
            confidence=0.52,
            reasoning="Multiple components detected. 'Circuit boards' match 8534. 'Power supply' match 8504. Cannot determine primary component. Low confidence.",
            correct=False,
        ),
        "llm_result": ClassificationResult(
            method="llm",
            hs_code="8537.10",
            confidence=0.88,
            reasoning="This is a repair kit for an industrial control system. Per General Rule of Interpretation 3(b), kits are classified by the component that gives them their essential character. The circuit boards and power supply together form a control system assembly. Chapter 85 (Electrical control panels) is appropriate. The kit is sold as a unit for a specific purpose.",
            correct=True,
        ),
    },
]


# ---------------------------------------------------------------------------
# Mock LLM classifier
# ---------------------------------------------------------------------------

def classify_with_regex(description: str, context: str = "") -> ClassificationResult:
    """Mock regex-based classification (simplified for demo)."""
    # Find matching test case
    for case in LLM_TEST_CASES:
        if case["description"] == description:
            return case["regex_result"]
    
    # Fallback
    return ClassificationResult(
        method="regex",
        hs_code="9999.99",
        confidence=0.0,
        reasoning="No match found in test cases.",
        correct=False,
    )


def classify_with_llm(description: str, context: str = "") -> ClassificationResult:
    """Mock LLM-based classification (returns pre-defined results for demo)."""
    # Find matching test case
    for case in LLM_TEST_CASES:
        if case["description"] == description:
            return case["llm_result"]
    
    # Fallback
    return ClassificationResult(
        method="llm",
        hs_code="9999.99",
        confidence=0.0,
        reasoning="No match found in test cases.",
        correct=False,
    )


def get_all_test_cases() -> list[dict]:
    """Return all test cases for showcase."""
    return LLM_TEST_CASES


# ---------------------------------------------------------------------------
# Comparison utilities
# ---------------------------------------------------------------------------

def compare_methods(description: str, context: str = "") -> dict:
    """Compare regex vs LLM classification for a given description."""
    regex_result = classify_with_regex(description, context)
    llm_result = classify_with_llm(description, context)
    
    return {
        "description": description,
        "context": context,
        "regex": regex_result,
        "llm": llm_result,
        "agreement": regex_result.hs_code == llm_result.hs_code,
        "regex_correct": regex_result.correct,
        "llm_correct": llm_result.correct,
    }


def get_statistics() -> dict:
    """Calculate statistics across all test cases."""
    total = len(LLM_TEST_CASES)
    regex_correct = sum(1 for case in LLM_TEST_CASES if case["regex_result"].correct)
    llm_correct = sum(1 for case in LLM_TEST_CASES if case["llm_result"].correct)
    
    regex_avg_conf = sum(case["regex_result"].confidence for case in LLM_TEST_CASES) / total
    llm_avg_conf = sum(case["llm_result"].confidence for case in LLM_TEST_CASES) / total
    
    return {
        "total_cases": total,
        "regex_accuracy": regex_correct / total,
        "llm_accuracy": llm_correct / total,
        "regex_avg_confidence": regex_avg_conf,
        "llm_avg_confidence": llm_avg_conf,
        "regex_correct_count": regex_correct,
        "llm_correct_count": llm_correct,
    }
