from django.dispatch import Signal

# Sent (after commit) when usable rates were recorded. kwargs: currencies (set[str]), source (code).
rates_changed = Signal()
