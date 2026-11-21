from collections.abc import Callable, Iterable
from datetime import UTC, datetime

import pytest

from cvestream.domain.model import (
    AffectedProduct,
    CvssScore,
    KevCatalog,
    KevEntry,
    Sourced,
    Vulnerability,
)

OBSERVED_AT = datetime(2026, 10, 26, tzinfo=UTC)

# (product, vendor, version_text); vendor and version_text may be omitted
type ProductSpec = (
    tuple[str, str | None, str | None] | tuple[str, str | None] | tuple[str]
)
type VulnerabilityFactory = Callable[..., Vulnerability]


def _affected(spec: ProductSpec) -> Sourced[AffectedProduct]:
    product = spec[0]
    vendor = spec[1] if len(spec) > 1 else None
    version_text = spec[2] if len(spec) > 2 else None
    return Sourced(
        AffectedProduct(product=product, vendor=vendor, version_text=version_text),
        source="euvd",
        observed_at=OBSERVED_AT,
    )


@pytest.fixture
def make_vulnerability() -> VulnerabilityFactory:
    """Factory building a `Vulnerability`; each test states only what matters."""

    def factory(
        *,
        primary_id: str = "CVE-2026-0001",
        aliases: Iterable[str] = (),
        title: str | None = None,
        description: str | None = None,
        products: Iterable[ProductSpec] = (),
        scores: Iterable[float | None] = (),
        kev: bool = False,
        kev_catalog: KevCatalog = KevCatalog.CISA_KEV,
    ) -> Vulnerability:
        return Vulnerability(
            primary_id=primary_id,
            aliases=frozenset({primary_id, *aliases}),
            titles=(
                (Sourced(title, source="euvd", observed_at=OBSERVED_AT),)
                if title is not None
                else ()
            ),
            descriptions=(
                (Sourced(description, source="euvd", observed_at=OBSERVED_AT),)
                if description is not None
                else ()
            ),
            scores=tuple(
                Sourced(
                    CvssScore(score=s) if s is not None else None,
                    source="euvd",
                    observed_at=OBSERVED_AT,
                )
                for s in scores
            ),
            affected=tuple(_affected(p) for p in products),
            kev=(
                (
                    Sourced(
                        KevEntry(catalog=kev_catalog),
                        source=(
                            "cisa_kev"
                            if kev_catalog == KevCatalog.CISA_KEV
                            else "eu_kev"
                        ),
                        observed_at=OBSERVED_AT,
                    ),
                )
                if kev
                else ()
            ),
        )

    return factory
