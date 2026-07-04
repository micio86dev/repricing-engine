"""PDP verification: visit candidate product pages to confirm identifiers and price.

This package is opt-in (CLI ``--verify-pdp``). The default flow never imports it.
:class:`~repricing_engine.pdp.verifier.PdpVerifier` runs a cheapest-first cascade
per candidate: fetch -> regex/meta/JSON-LD identifier scan (free) -> AI extraction
(last resort), turning the heuristic confirmation labels into facts.
"""
