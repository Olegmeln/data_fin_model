"""Интейк документов: текст → допущения по схеме assumptions.v1."""
from .extractor import IntakeError, extract_assumptions
from .textract import ExtractedDoc, extract_text

__all__ = ["ExtractedDoc", "IntakeError", "extract_assumptions", "extract_text"]
