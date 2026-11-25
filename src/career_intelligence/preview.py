"""Collect one offline fixture or run the configured live sources sequentially."""

from pathlib import Path

import httpx

from career_intelligence.collection import CollectionPreview, CollectionSnapshot, Connector
from career_intelligence.collection_http import HttpPolicy
from career_intelligence.config import Company, ConfigurationError, LoadedConfiguration
from career_intelligence.greenhouse import GreenhouseConnector


async def preview(
    configuration: LoadedConfiguration,
    *,
    fixture: Path | None = None,
    source_key: str | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> CollectionPreview:
    limits = configuration.settings.collection
    if fixture is not None:
        company = Company.model_validate(
            {
                "key": "fixture-company",
                "name": "Example Fixture Company",
                "category": "big-tech-ai",
                "disabled_reason": "Offline fixture",
                "sources": [
                    {
                        "key": "fixture-board",
                        "provider": "greenhouse",
                        "board_token": "fixture-board",
                        "disabled_reason": "Offline fixture",
                    }
                ],
            }
        )
        adapter = GreenhouseConnector(limits, fixture=fixture)
        return CollectionPreview(
            mode="fixture", snapshots=[await adapter.collect(company, company.sources[0])]
        )

    sources = [
        (company, source)
        for company in configuration.watchlist.companies
        for source in company.sources
        if source.enabled and (source_key is None or source.key == source_key)
    ]
    if not sources:
        raise ConfigurationError("No matching enabled Greenhouse source in the watchlist")
    snapshots: list[CollectionSnapshot] = []
    async with httpx.AsyncClient(transport=transport, trust_env=False) as client:
        connector: Connector = GreenhouseConnector(limits, http=HttpPolicy(client, limits))
        for company, source in sources:
            snapshots.append(await connector.collect(company, source))
    return CollectionPreview(mode="live", snapshots=snapshots)
