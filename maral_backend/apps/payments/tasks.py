from celery import shared_task

from . import reconciliation


@shared_task(name="payments.reconcile")
def reconcile_payments() -> dict:
    run = reconciliation.reconcile()
    return {
        "scanned": run.scanned,
        "applied": run.applied,
        "errors": run.errors,
        "discrepancies": len(run.discrepancies),
    }
