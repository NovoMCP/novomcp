# Tree-Guided Retrieval: How We Made 122 Million Molecules Navigable by AI

**Date:** May 2026
**Author:** NovoMCP Engineering

---

## The Problem With Flat Vector Search at Scale

Vector databases are the backbone of retrieval-augmented generation. They work well at millions of rows, embed your documents, index them, query with a vector, get the top-K results. Pinecone, Weaviate, Qdrant, and others have made this pattern accessible.

But something breaks at 100 million rows.

Not latency, modern ANN algorithms like DiskANN handle 100M+ vectors with sub-100ms queries. What breaks is **relevance**. When an AI asks "find kinase inhibitors with low toxicity and good oral bioavailability" and gets back 10 of 122 million molecules, it has no way to know:

- Are these 10 representative of the chemical space, or are they all from the same narrow region?
- What did the search miss? Are there entire chemical families the AI never saw?
- How should the AI refine its search? It has no map, just 10 needles pulled from a haystack it can't see.

This is the fundamental limitation of flat retrieval at scale. The AI gets answers but not context. It finds molecules but can't navigate chemistry.

## What We Built

We built a two-layer system that gives AI agents both a map and a search engine for 122 million molecules.

### Layer 1: DiskANN Vector Index

Every molecule gets a 512-dimensional Morgan fingerprint embedding, a structural representation that captures what functional groups are present, how atoms are connected, and what the molecule looks like to a chemist.

```
SMILES → RDKit Morgan FP (radius 2, 2048 bits) → Random Projection (512 dims) → L2 normalize
```

These embeddings are stored directly in our DB with a DiskANN index. No separate vector database, no sync jobs, no cold starts. The same document that holds a molecule's properties also holds its embedding. When an AI searches for structurally similar molecules, it gets sub-100ms results across 122 million compounds.

This is the Pinecone-equivalent layer. It answers: "What molecules look like this one?"

### Layer 2: Cluster Hierarchy

This is what flat vector search doesn't give you.

We clustered the 122 million molecules into a navigable tree using MiniBatchKMeans on the Morgan fingerprint embeddings:

- **Level 1: 93 chemical regions**, each containing ~1.3 million molecules
- **Level 2: 9,097 chemical zones**, each containing ~13,000 molecules

Every cluster node is a self-describing document with a natural-language summary:

> "1,174,086 molecules, MW 95-8348, avg QED 0.61, avg toxicity 0.51, avg CYP inhibition risk 0.32, 78% orally bioavailable, 12% BBB-penetrant, 2% alert-free, 97% zero-PAINS, 100% FAVES-clean"

The AI reads these summaries, in plain English, and decides which branch to explore. It doesn't need to understand embeddings, fingerprints, or vector math. It reads text like a chemist would read a catalog.

### How the AI Navigates

```
User: "Find drug candidates for EGFR with MW 300-500 and low cardiotoxicity"

Step 1: explore_chemical_space("EGFR inhibitors, MW 300-500, low cardiotoxicity")
  → AI reads 5 region summaries
  → Picks Region B: "1.2M molecules, MW 290-520, avg QED 0.61,
     avg toxicity 0.18, 78% orally bioavailable, 97% zero-PAINS"

Step 2: drill_into_cluster("L1_C000023")
  → AI reads 5 zone summaries within Region B
  → Picks Zone: "12K molecules, MW 380-450, avg QED 0.71,
     91% GI High, 15% BBB-penetrant"

Step 3: compare_candidates([CID list from zone])
  → Head-to-head comparison of top molecules

Step 4: vector_search(seed SMILES)
  → DiskANN similarity search for structural analogs

Total: 4 API calls, <500ms, narrowing from 122M → 1.
```

The AI made an informed decision at every step. It saw the map before searching.

## Why This Matters for RAG

The standard RAG pattern, embed, index, retrieve, treats the knowledge base as a flat pool. This works when you have 10,000 documents and the user's query maps cleanly to a few relevant ones.

At 122 million molecules, flat retrieval fails silently. The AI gets results that look reasonable but are drawn from a tiny, potentially unrepresentative slice of the space. It has no way to detect this because it never sees the full landscape.

The hierarchy changes the failure mode. Instead of "here are 10 molecules that matched your query," the AI sees:

- 93 distinct chemical regions, each with property distributions
- Which regions match its criteria and which don't
- How many molecules exist in each region (is this a well-explored space or a niche?)
- What the tradeoffs look like (this region has high oral bioavailability but more PAINS alerts)

The AI makes a navigation decision, "I want the region with low toxicity and high oral bioavailability", before it ever does a vector search. The search happens within a context the AI already understands.

## The Data Layer

The hierarchy doesn't exist in isolation. Each cluster summary draws from three enrichment layers:

**109 data fields per molecule** across 122 million compounds:

- **Physicochemical properties**, MW, logP, TPSA, QED (refit to ChEMBL 35), drug-likeness, synthetic accessibility
- **26 ADMET predictions**, absorption, distribution, metabolism, excretion, toxicity from TDC benchmark-validated models (MapLight CatBoost, Chemprop v2 MPNN)
- **Structural alert screening**, 1,585 SMARTS patterns across 5 catalogs (PAINS, Brenk, NIH, ZINC, ChEMBL) with per-catalog breakdown
- **Pharmacokinetic classification**, GI absorption and BBB permeancy from BOILED-Egg
- **Regulatory compliance**, DEA, FDA, EPA, CWC, EU REACH, controlled substance detection
- **CYP450 metabolism**, 6 CYP inhibitor + 3 substrate models, risk scores
- **Nuclear receptor activity**, 7 NR agonist/inhibitor models
- **Stress response**, 5 pathway activation models

When the AI reads a cluster summary that says "78% orally bioavailable, avg toxicity 0.18, 97% zero-PAINS," those numbers are computed from real per-molecule predictions, not approximations.

## Engineering: Streaming at 122M Scale

A naive implementation would load all 122M embeddings (250GB) plus all alert data (35GB) into memory. We couldn't do that.

Instead, we built a streaming pipeline:

1. **Phase 1**, Stream 122M molecules from our DB, feed each batch to `MiniBatchKMeans.partial_fit()`. Memory usage: O(n_clusters × 512 dims), not O(122M × 512 dims). The centroids converge incrementally. ~35 hours.

2. **Phase 2**, Re-stream all 122M molecules, predict cluster assignments, accumulate per-cluster statistics (MW ranges, QED means, toxicity distributions, alert percentages) using running accumulators. No molecule data is retained between batches. ~35 hours.

3. **Phase 3**, Link Level 2 zones to their nearest Level 1 region via centroid cosine similarity, upload 9,190 cluster documents to our DB. Minutes.

Total: ~3 days on 8 GiB of RAM. The same data that required 285GB in a naive implementation.

## What Flat Vector Search Can't Do

The following query is trivial with the hierarchy and impossible with flat ANN:

> "Show me the chemical regions where more than 90% of molecules are orally bioavailable AND less than 5% have PAINS alerts AND the average toxicity score is below 0.3"

This is a property-filtered navigation query. It doesn't have a vector, there's no SMILES to embed. The AI needs to scan cluster summaries, apply filters, and present options. Flat vector search can't express property constraints. The hierarchy can.

## Cost

| Component | One-time | Monthly |
|-----------|----------|---------|
| DiskANN embeddings (122M × 512 dims) | ~$200 in Cosmos RUs | ~$50 (storage) |
| Cluster hierarchy (9,190 docs) | ~$5 in RUs | ~$5 |
| Structural alerts (122M docs) | ~$150 in RUs | ~$50 (storage) |
| TDC ADMET merge (26 columns × 122M) | ~$100 in RUs | $0 (same docs) |
| **Total** | **~$455** | **~$105/mo** |

No GPU required for any part of the pipeline. All fingerprint computation, clustering, and enrichment runs on CPU.

## What's Next

- **Level 3 leaf clusters**, ~1M clusters of ~100 molecules each, for fine-grained navigation
- **Semantic cluster naming**, LLM-generated names for each region ("pyrimidine kinase inhibitors" instead of "L1_C000023")
- **Telemetry**, tracking which branches LLMs explore to optimize cluster descriptions
- **Incremental updates**, when new molecules or model updates arrive, rebuild only affected clusters instead of the full tree

---

*The engine behind tree-guided retrieval was built in 47 days (March 10 – April 27, 2026) across structural alerts reprocessing, DiskANN embedding generation, TDC ADMET enrichment, FAVES V4 integration, and streaming cluster hierarchy construction. The full execution record is in `docs/NovoMCP/TREE-SEARCH-ARCHITECTURE.md`.*
