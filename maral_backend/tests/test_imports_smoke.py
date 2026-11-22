import pytest

from apps.imports.models import JobStatus, JobType
from apps.imports.services import jobs
from apps.product_sources.models import ExternalProduct
from apps.product_sources.providers.mock import InMemoryProvider
from apps.product_sources.providers.registry import use_provider
from tests.imports_helpers import make_product, make_variant, run_job_sync, world

pytestmark = pytest.mark.django_db


def test_full_import_creates_listing_and_canonical_product():
    src, acme, shoes = world()
    prods = [make_product("P1"), make_product("P2", title="Acme Court Shoe", variants=[make_variant("V2", size="41")])]
    with use_provider("trendyol", lambda s, **kw: InMemoryProvider(s, prods, **kw)):
        job, created = jobs.create_job(source=src, job_type=JobType.FULL)
        job = run_job_sync(job)
    assert created and job.status == JobStatus.SUCCEEDED, (job.status, job.error_code, job.error_message)
    assert job.n_created == 2
    ext = ExternalProduct.objects.get(external_id="P1")
    assert ext.canonical_product is not None
    assert ext.pipeline_status in ("TRANSLATION_PENDING", "PRICING_PENDING", "READY_TO_PUBLISH"), ext.pipeline_status
