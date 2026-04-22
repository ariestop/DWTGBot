"""Long-running in-process services owned by the bot entrypoint.

Unlike the handler-facing ``use_cases`` and ``services`` layers, modules
here are background tasks / sub-systems with their own lifecycle
(``start`` / ``stop``). They are instantiated in ``composition.build_bot``
and driven from ``app/main_bot.py``.
"""
