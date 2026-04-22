"""
Application-layer ports (driven abstractions).

Ports express "what the application needs from the outside world" as
plain ``Protocol`` classes so use cases stay infrastructure-agnostic.
Concrete implementations live under ``app/infrastructure/`` and are
wired into use cases in ``app/composition.py``.

New in ADR-0010: :mod:`app.application.ports.progress_reporter`.
"""
