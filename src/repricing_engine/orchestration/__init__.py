"""Async orchestration around the synchronous matching pipeline.

:class:`~repricing_engine.orchestration.enhanced_pipeline.EnhancedPipeline` bridges
the (synchronous) :class:`~repricing_engine.matching.pipeline.MatchingPipeline`
with the async source-fetching and PDP-verification stages. It is only used when
the CLI ``--fetch`` / ``--verify-pdp`` flags are set; the default flow is untouched.
"""
