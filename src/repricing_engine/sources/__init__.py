"""Source fetching: discover competitor offers via free SERP / comparison providers.

This package is opt-in (CLI ``--fetch``). The default CSV-only flow never imports
it. :class:`~repricing_engine.sources.orchestrator.SourceFetcher` runs the enabled
providers concurrently and deduplicates their results into competitor products.
"""
