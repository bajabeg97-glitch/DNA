"""Safe debugging helpers for DNA MIDI Studio.

The debugger is diagnose-first.  Proposed edits are never committed unless a
caller explicitly asks for a sandbox validation and the configured gates pass.
"""
from .diagnostics import Diagnostic, analyze_failure, error_signature
from .runner import CommandResult, run_command
from .transaction import PatchProposal, PatchValidationResult, validate_patch_in_sandbox

__all__ = [
    "Diagnostic", "analyze_failure", "error_signature",
    "CommandResult", "run_command",
    "PatchProposal", "PatchValidationResult", "validate_patch_in_sandbox",
]
