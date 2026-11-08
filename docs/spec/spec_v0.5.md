# REMY Financial — Technical specification v0.5

_Accepted-changes copy of REMY_Financial_Technical_Spec_v0.5.docx, for the repository (docs/spec/spec_v0.5.md). The .docx with tracked changes is the source of record._

**Version:** 0.5 (revised design baseline)  
**Date:** November 7, 2026  
**Status:** Consolidated provisional design; no demonstrated trading edge  
**Scope:** Research engine, probabilistic scenario simulation, options strategy evaluation, and capital-free prospective paper trading  
**Revision note:** v0.5 adds the policy, ranking, baseline, and evaluation revisions agreed November 7, 2026, and records R0–R1 implementation decisions in Section 15.

# Executive summary

REMY Financial is an experimental options-research engine that applies ideas from computer chess to financial decision-making. It is intended to discover potentially underpriced option payoffs by learning how economic developments propagate through companies and markets, simulating continuous distributions of possible outcomes, and jointly optimizing trade entry, partial liquidation, and exit.

Its competitive hypothesis is **better inference and search from modest, obtainable data**, not a proprietary data-acquisition advantage. It will compare a classical economic model, a fully self-learning neural model, and a hybrid under the same information, execution assumptions, and experimental tests. Recommendations will be tested through an immutable, prospective paper-trade registry, without risking real capital. Whether the project can produce a persistent tradable edge remains an empirical question.

# 1. Goals, boundaries, and success criteria

**Primary objective.** Identify and rank specific options strategies by expected **net percentage return on capital at risk**, while preserving the complete predicted distribution of results, especially adverse and favorable tails.

**Secondary objectives.** Discover reusable economic relationships; evaluate causal sequences and the timing of their effects; identify how changes in search depth alter trade value; compare classical, neural, and hybrid approaches under controlled conditions; and maintain an inspectable, reproducible record of real-time paper-trade recommendations.

**In scope.** Market-data ingestion; public company and event observations; historical point-in-time replay; candidate-trade generation; market-transition modeling; continuous stochastic simulation; entry/exit optimization; partial liquidation; MultiPV; paper-execution simulation; model tournaments; evaluation and diagnostics.

**Out of scope for the initial project.** Live brokerage order submission; automatic execution; building a comprehensive paid institutional data warehouse; claims of guaranteed profitability; treating simulated market paths as independent real-world evidence.

**Success condition.** Demonstrate, on untouched historical periods and later prospective paper trades, that the model's distributions are reasonably calibrated and that its recommended, realistically executable strategies outperform credible simple baselines, including the market-implied baseline (Section 9), after costs. Paper results are evidence about the model, not proof of achievable live fills.

**Pre-registered evaluation.** Before any R2 result is examined, the forecast scoring rules, tail-coverage tests, and trade-performance metrics described in Section 11 are fixed in a versioned evaluation document. A metric adopted after results are seen is reported as post hoc. The project also estimates in advance how many effectively independent paper-trade observations, and roughly how much calendar time, would be needed to detect an edge of a stated size after costs.

# 2. Core chess-engine analogy

| **Chess engine**    | **REMY Financial**                             | **Agreed interpretation**                                                     |
|---------------------|------------------------------------------------|-------------------------------------------------------------------------------|
| Board               | Point-in-time market information state         | Describes observed information; does not contain judgments of mispricing      |
| Move                | Candidate options strategy or null move        | Includes instrument, quantity, entry conditions, and eventual exit policy     |
| Evaluation          | Expected net return on capital at risk         | Derived from a predicted distribution of strategy outcomes                    |
| Search              | Simulated probabilistic economic continuations | Propagates learned or explicit dependencies through future states             |
| Depth (I)           | Causal depth                                   | Number of consequence layers explored, **not** a fixed interval of time       |
| Move ordering       | Causal event ordering                          | Different event sequences have different conditional effects                  |
| Time management     | Conditional event timing                       | Determines when subsequent economic developments could occur                  |
| Opening book        | Predefined economic hypothesis or scenario     | Reproducible assumptions to challenge and benchmark models                    |
| Transposition       | Economically overlapping successor states      | Reuses shared consequences while retaining different causal histories         |
| Modified hashing    | State fingerprints and similarity retrieval    | Finds candidate cross-company/state relationships; does not prove correlation |
| Principal variation | Joint entry/hold/partial-exit/exit policy      | Converts forecast distributions into testable strategies                      |
| MultiPV             | Alternative strategies and risk distributions  | Primary rank is return on capital at risk, not only dollar profit             |
| Endgame tablebase   | Option payoff and terminal decision solver     | Contractual payoff is exact; pre-expiry execution is uncertain                |
| Engine tournament   | Paired replay and prospective paper trading    | Compares versions on the same information and observed market outcomes        |
| UCI-style interface | Engine analysis protocol                       | Decouples engines from GUIs, research harnesses, and possible brokers         |

The analogy is deliberately not literal. A small options trade ordinarily does not cause the market to change; the engine controls the proposed trading policy, while market developments are stochastic. Financial observations are incomplete, and economic transition rules must be estimated rather than assumed exact.

# 3. Observable board and data contract

The board is an immutable, point-in-time **MarketState**. It records what the engine could actually know at the analysis instant, including market prices, options quotes and surfaces, rates, commodities, company information, public news and social observations, and relevant historical context. Deterministic derived measurements may be cached; forecasts, mispricing assessments, and trade recommendations belong to models, not the board.

Every observation should retain, when available, its source, observation/event time, public-availability time, ingestion/receipt time, quality flags, and applicable licensing or provenance. Historical replay must never expose later data revisions or subsequent reports as though known earlier.

The research data strategy is deliberately selective: use a tractable universe of liquid securities and accessible market/company sources. Data reliability matters, especially synchronized historical options quotes with bid/ask, size, underlying prices, and timestamps. Social-media reports are evidence to assess, not independent truth; repeated copies of one rumor must not be treated as independent observations. Only lawfully available public information is within scope. Universe membership is itself point-in-time: the liquid universe for date t is selected using only information available at t, and names that later delist, merge, or lose liquidity remain in the historical record.

# 4. Three competing model families

**REMY Classical.** Human-specified economic factors and relationships, with parameters calibrated from historical observations. Company-factor response functions replace simplistic one-number betas with conditional distributions depending on shock magnitude, underlying cause, market regime, and investment horizon. Explicit relationships model common shocks, residual dependencies, lags, and feedback.

**REMY Neural.** A genuinely self-learning probabilistic dynamics model. It discovers economic representations, intercompany relationships, conditional dependencies, ordering, timing, and outcome distributions from historical observations. It must **not** inherit handcrafted causal graphs, company-factor sensitivities, or opening-book conclusions as governing rules. Self-supervised forecasting and distributional training are possible methods, not fixed architectural commitments. Learning temporal association does not by itself establish causal identification.

**REMY Hybrid.** A separately tested combination of explicit economic structures and learned neural functions. The hybrid must not be silently treated as the pure neural engine.

All competitors may share the same immutable observations, instrument definitions, contractual options mathematics, simulated-execution standards, and evaluation harness. They should be independently versioned and evaluated against actual later market observations. Simulated trajectories may accelerate computation or train approximations, but cannot create independent evidence that the modeled economic assumptions are correct.

# 5. Continuous event-driven stochastic search

The transition model describes possible **joint** developments in macroeconomic factors, companies, market returns, volatility, and—when early exit matters—future executable option prices and liquidity. Distributions should be continuous or suitably granular, without artificial scenario cliffs. Genuine economic or contractual thresholds must still be represented.

A search state includes a hypothetical market/economic condition, relevant unresolved causal history, simulated elapsed time, probability weight, and where applicable the current strategy holdings. A transition samples or estimates event type, magnitude, consequences, and timing conditional on that state. Market noise and unrelated events continue to evolve alongside named causal chains.

**Causal depth (I) is not elapsed time.** Search at successive depths adds orders of economic consequences while evaluating the same trade horizon. An event may have several consequential effects almost immediately; others may take months. The model must learn or estimate this timing separately. The ordering of economic shocks matters: an oil supply shock followed by weaker demand is not equivalent to a demand contraction followed by an oil-price decline. Simultaneous events and feedback loops must not be forced into arbitrary single-file narratives.

**Broad iterative deepening.** Evaluate candidate trades across increasing causal depths rather than initially extending only attractive paths. At each depth, generate a coherent, normalized joint forecast distribution, not a simple sum of effects from independent chains. Store how a trade's estimated return changes with depth. Avoid double-counting the same shock across causal loops or overlapping explanations.

**Shared path simulation.** Prefer generating a common set of market trajectories and evaluating many candidate options against them. For rare, high-impact tails, importance sampling or related variance-reduction methods may be used, provided original probability weights are preserved. The number of simulated paths reduces sampling noise but not errors in model assumptions.

# 6. Economic transpositions and modified Zobrist-style fingerprints

Different initiating events can produce overlapping financial board states: for example, higher oil costs and higher financing costs can both weaken cash flow and debt coverage. These shared consequences are analytically useful even when the causes—and some future consequences—remain different.

Store both **causal state** (initiating developments, unresolved risks, timing, and history) and **economic-response state** (company cash flow, margins, leverage, return distributions, volatility exposures, and other modeled consequences). Support exact state caching, component reuse, and partial transpositions where only some downstream consequences can be shared.

Ordinary Zobrist XOR hashes are suitable for exact identity, not economic similarity. An experimental modified-fingerprint layer may use continuously derived feature vectors and similarity-preserving indexing (for example, projection-based hashing) to discover candidate related companies or states. Any identified relationship must be tested on the underlying continuous data, after accounting for common factors and multiple hypothesis testing. Similarity is not proof of causation or profitable predictability.

The practical value of caching and incremental neural updates remains unverified; measure it rather than assuming chess-like search bottlenecks.

# 7. Broad move generation and execution feasibility

Generate a broad universe of structurally permissible long options and, as supported, defined-risk multi-leg strategies. Include the **null move**: commit no new capital and reconsider after future information arrives. Do not eliminate rare-tail candidates merely because their immediate win probability looks small.

A candidate trading strategy specifies the underlying and contract identifiers, legs, direction, quantity, entry rule, price/size assumptions, exit policy, expiration, and capital at risk. Contract availability alone is insufficient: the engine needs a defensible assumption about entry and exit execution in the specified quantity.

Model liquidity as a state- and size-dependent cost/availability distribution: bid/ask, available depth, market-maker behavior, spread changes, delayed or failed fills, and fees. Do not impose arbitrary daily-volume cliffs as the sole eligibility criterion. Where a defensible executable entry price cannot be established, flag the candidate as **unverified/unpriceable**, not automatically unprofitable.

For simulated future entry after a null move, use the price and information hypothetically available **at that future node**, not today's quote. A historically observed quote must be distinguished from a model-generated future quote. Long options may be held to expiration where settlement mechanics are modeled; early exit is not a universal requirement.

# 8. PV, partial liquidation, and the universal options endgame solver

The financial **principal variation** is a conditional, executable trading policy, not primarily a narrative causal chain. Search and policy optimization jointly determine whether/when to enter, how many contracts to acquire, whether to wait, how much to sell at each subsequent decision, and the final exit or expiration treatment. A conditional policy may branch by observed future information; a displayed PV is its readable summary.

The strategy must be **nonanticipating**: decisions at a future node may use only the information observable at that node. It is invalid to choose the optimal historical exit after examining a completed market path. Partial liquidation is permitted in whole-contract quantities and may preserve exposure to a favorable right tail, but does not itself create expected profit. Its merit depends on liquidation prices, subsequent distributions, capital constraints, and fees.

**Policy-optimization baseline.** The reference method for optimizing nonanticipating entry, partial-liquidation, and exit decisions on simulated paths is least-squares Monte Carlo: a Longstaff–Schwartz-style regression of continuation value on state variables observable at each decision node. Any alternative policy optimizer must be benchmarked against it. A policy must be fitted on one set of simulated paths and valued on an independent set; valuing a policy on the paths used to fit it biases its estimated return upward and is not permitted in reported results.

Keep contractual terminal payoff and settlement mathematics independent of economic forecasting. For a standard long call, terminal intrinsic payoff per share is max(S_T - K, 0); for a long put, max(K - S_T, 0). Net profit also incorporates acquisition premium, contract multiplier, fees, and any relevant exercise/settlement costs. American exercise, short-leg assignment, deliverable differences, and broker procedures require explicit support before those structures are admitted to the tested universe.

**Early assignment.** Because the initial universe uses American-style equity and ETF options, structures with short legs are admitted only under a conservative assignment rule: a short leg is assumed assigned whenever early exercise would be rational for the holder—for example, an in-the-money short call before an ex-dividend date when the dividend exceeds the call’s remaining extrinsic value, or any in-the-money short leg whose extrinsic value falls below a fixed threshold. Assumed assignments are recorded as paper events with their cash and position consequences. The rule is versioned and applied identically to every engine.

At intermediate states, compare realistic liquidation proceeds with the modeled continuation value across future paths. Future executable prices and liquidity are predictions, not exact tablebase outputs. A single reusable solver should handle this mathematics across securities.

# 9. Evaluation scale and MultiPV

The primary per-strategy score is:

**Expected net return on capital at risk = E\[net strategy P&L\] / capital at risk.**

For a defined-risk option strategy, capital at risk ordinarily means the maximum committed capital subject to loss, including costs. For strategies with conditional entry, varying margin, or dynamically committed capital, the denominator convention must be specified explicitly and kept identical across competitors. Report expected return conditional on entry separately from the unconditional value of recommending the policy, including the possibility of no fill.

**MultiPV** should return several economically distinct candidate policies, not only the largest point estimates. In addition to the primary percentage score, preserve expected dollar profit, likelihood of loss, modeled return distribution, adverse percentile(s), expected shortfall, maximum contractual loss where bounded, holding-time distribution, entry/fill probability, and uncertainty in the probability model. Show return versus tail-risk trade-offs rather than compressing every preference into one arbitrary number. Null moves remain available.

**Uncertainty-adjusted ranking.** Selecting the highest-scoring candidates from a large universe systematically favors candidates whose scores are inflated by model error (the optimizer’s curse), most severely for inexpensive out-of-the-money structures whose value depends on tail probabilities. MultiPV therefore ranks on an estimate adjusted for model and sampling uncertainty—for example, shrinkage toward zero excess return or a lower confidence bound—and reports the unadjusted estimate alongside it. The adjustment method is versioned and identical across competitors, and the tournament reports predicted versus realized return separately for top-ranked selections.

Do **not** mechanically annualize short-duration option returns. Holding time and capital opportunity cost remain separate diagnostics at first; any combined scoring method must earn adoption through experiments. Likewise, a high predicted mean should not hide a material left tail.

An option's positive expected payoff under a real-world distribution must be compared with its actual executable premium and costs. Implied volatility and option prices reflect risk-neutral valuation, hedging demand, and risk premia; a high predicted realized volatility figure by itself does not establish a positive expected net return.

**Market-implied baseline.** The option surface defines a reference distribution: the risk-neutral density implied by observed prices, optionally adjusted by a documented, historically estimated volatility risk premium. Every engine must outperform this market-implied baseline, not merely naive statistical forecasts. A model that cannot beat the market’s own distribution after costs has not shown that its economic search adds value. A seeded random strategy selector serves as a second, floor-level baseline.

# 10. Opening books and structured scenario tests

An opening-book entry is a **versioned, reproducible economic hypothesis**: for example, condition on an oil supply disruption, define its assumed magnitude distribution and initial relationships, and explore a specified causal depth. The same book should run against different historical or live boards.

A book scenario asks, if this event occurs, what follows? Its conditional profit forecast must not be presented as an unconditional trade expectation without independently estimating event occurrence and complementary scenarios. Books can be fixtures for regression testing and classical-model baselines. They **must not** supply hidden economic rules or training labels to the independently self-learning neural engine.

# 11. Prospective paper-trade registry and engine tournaments

The **REMY Paper-Trade Registry** records **specific recommended trades**, not every macroeconomic forecast. Each candidate selected for prospective paper testing becomes a frozen recommendation linked to its engine version and underlying observation snapshot. A separate append-only journal records paper orders, modeled executions, partial fills, partial exits, final exit/settlement, and realized hypothetical P&L. An unfilled recommendation remains unfilled; a null decision can be audited separately without inventing a trade.

**Required recommendation fields:** unique trade ID; engine/model version and artifact hash; code commit; decision/receipt timestamps; market snapshot identifier and original quote provenance; complete contract/leg identifiers; recommended quantity; entry and cancellation conditions; expiry and strategy horizon; executable-price assumptions; maximum capital at risk; full predicted net-return distribution; expected percentage and dollar returns; tail-risk measures; probability of fill; jointly optimized conditional liquidation/exit policy; search configuration, causal depth, simulation seed, and opening-book ID where applicable.

**Required outcome fields:** paper-order submission time; quote and depth observations; actual or assumed fill/partial-fill decisions under a frozen fill rule; cash flows and commissions; partial sales; expiration/exercise/settlement events; final hypothetical net P&L and return on capital; deviation from the original forecast; annotations explaining data issues or apparent model/decision mistakes. Record the exact time an event happened (if known), became public, was ingested, and was used in the forecast where those differ.

**Frozen fill rule.** Paper executions cross the spread: each buy leg fills at the displayed ask and each sell leg at the displayed bid at the evaluation snapshot, and a leg fills only if the displayed size at that price covers the full quantity. A multi-leg order fills only if every leg fills; otherwise the order is recorded as unfilled. Exits follow the same rule. The rule is versioned; any later, less conservative rule creates a separate, linked set of records rather than revising past results.

**Immutability rule:** no editing a historical recommendation or retroactively changing a policy. Corrections and subsequent model reruns are separate, linked records. Maintain market snapshots or reproducible point-in-time references. Any post-hoc analysis must be labeled as such.

**Tournament protocol:** compare Classical, Neural, Hybrid, the market-implied and random baselines, and other simple baselines on identical point-in-time inputs. Test both (a) each engine's independently recommended portfolio of paper trades and (b) predictions for a common fixed set of contracts. Use paired replay where possible and prospective paper trades as the critical reality check. Measure calibration, tail reliability, expected-versus-realized net returns, fill rates, drawdowns, and selection performance. Account for overlapping trades, shared market shocks, regime changes, repeated experiments, and multiple testing. Simulated trades are **not** independent samples merely because they involve different contracts.

**Pre-registered metrics and statistical power.** Forecast quality is scored with proper scoring rules fixed in advance: continuous ranked probability score and log score for predicted underlying-return and strategy-return distributions, probability-integral-transform histograms for calibration, and coverage tests at fixed tail percentiles. Trade performance is reported as realized versus predicted net return on capital at risk, fill rates, drawdowns, and selection performance by rank. Before prospective paper trading begins, a recorded power estimate states how many effectively independent observations are expected to accumulate per month, given overlapping trades and shared shocks, and how long detecting an edge of a stated size would take.

# 12. Interfaces and boundaries

Define a versioned, machine-readable **REMY Analysis Protocol**, analogous to UCI but not tied to a broker. It should support capability discovery; loading a timestamped board; selecting a model and scenario book; requesting a stochastic analysis with causal-depth and sampling configuration; receiving MultiPV recommendations with full distributions and policies; recording decisions; and retrieving reproducibility metadata. A JSON-based request/response interface is a candidate design, not an agreed wire format.

A market-data or brokerage API may provide quotes and contract metadata to an adapter. **Broker execution is not part of the research scope.** Future integration must not require rewriting the models, search, evaluator, or registry.

# 13. Initial engineering sequence — R0–R1 approved; R2–R6 proposed

**R0 — Data and correctness.** Data-source verification; point-in-time universe of about 50 liquid underlyings; immutable market snapshots; point-in-time hygiene; exact options payoff/settlement math; deterministic candidate generation; basic transaction-cost assumptions; defined-risk structures with the conservative early-assignment rule.

**R1 — Paper-trade registry.** Freeze recommended trades and nonanticipating policies prospectively; implement order/fill/partial-liquidation journal and reconciliation; allow the simplest baseline to issue falsifiable recommendations immediately. Initial recommendation sources are the market-implied baseline and a seeded random baseline; the frozen fill rule applies from the first record.

**R2 — Classical stochastic baseline.** Fit an interpretable joint factor model; generate continuous shared economic paths; evaluate long options under a fixed simple exit rule; benchmark against transparent distribution and option-pricing baselines. Causal depth has no defined meaning for the R2 classical model; it is introduced in R3.

**R3 — Causal search and opening books.** Add explicit event ordering/timing, broad iterative deepening, scenario fixtures, partial economic transpositions, and convergence diagnostics without double-counting causal effects.

**R4 — Joint PV optimization.** Add null moves, conditional entries, prospective revaluation, partial liquidation, realistic future liquidity, and MultiPV return/tail-risk comparisons. Least-squares Monte Carlo, fitted and valued on separate path sets, is the reference policy optimizer.

**R5 — Independent pure neural competitor.** Train a neural dynamics model solely from admissible historical observations; do not seed it with classical causal rules. Compare distribution calibration, trade selection, and prospective paper P&L with identical data and execution assumptions.

**R6 — Hybrid and efficiency experiments.** Test hybrid structures, continuous similarity fingerprints, incremental inference, alternative sampling, and deeper causal search only where an incremental experiment demonstrates value.

Each stage should produce repeatable tests and a short result memo. Progression depends on correctness, forecast quality, and credible transaction economics, not code complexity or simulation count.

# 14. Settled decisions versus open research questions

**Established design decisions** (provisional until implemented): broad liquidity-aware move generation; a null move; causal rather than fixed-time plies; continuous joint scenario distributions; broad iterative deepening; event-order and timing dependence; classical and fully independent self-learning neural competitors; analytical partial transpositions; jointly optimized entry and partial exits; primary expected return on capital at risk; risk-transparent MultiPV; standardized scenario opening books; exact option terminal mechanics; prospective, immutable **trade-specific** paper registry; no live order execution; least-squares Monte Carlo policy baseline with separate fitting and valuation paths; uncertainty-adjusted MultiPV ranking; market-implied and random baselines; pre-registered scoring rules and power estimate; conservative early-assignment rule; frozen spread-crossing fill rule.

**Unresolved empirical and implementation questions:** whether the selected vendor’s synchronized historical options quotes meet the data contract (Section 15); which market events and features can be observed reliably; neural architecture and training loss; whether inferred relationships generalize out of sample; calibration of rare tails and future option liquidity; optimal causal depth and event timing resolution; mathematical treatment of interacting shocks and feedback; usefulness of approximate transpositions and incremental NNUE-like updates; practical strategy universe and capital conventions; opportunity-cost scoring; and the extent to which paper fills approximate possible real executions.

**Central falsifiable hypothesis:** Given the same modest, obtainable information, a neural or hybrid probabilistic causal model plus search can identify options strategies whose realistically executable, prospective return distributions are better than those selected by simpler baselines. The project must be prepared to reject this hypothesis if empirical results do not support it.

# 15. Implementation decisions for R0–R1

These decisions were made November 7, 2026. Each is recorded as a decision record in the repository and can be revisited only through a new, linked decision record.

**Language.** Python for all components. A component moves to Rust only where profiling demonstrates a material bottleneck.

**Repository.** Private repository github.com/jtsmith509/remy-finance, separate from the REMY chess engine.

**Host.** Mac mini, with scheduled snapshot jobs. A missed snapshot is recorded as a gap and is never backfilled into the prospective record.

**Data source.** ThetaData Options Standard (individual, personal-use license) is the provisional primary source. R0 begins by verifying bid/ask sizes, underlying prices, dividend and corporate-action coverage, time-of-day historical retrieval, and expired-contract coverage. A supplementary dividend and corporate-action source is added if needed. All vendor access sits behind an adapter interface so that the vendor can be replaced without changing models, search, evaluation, or the registry.

**Universe.** About 50 liquid US stocks and ETFs, selected point-in-time.

**Snapshot cadence.** Two frozen snapshots per trading day: one shortly after the open, once opening quotes have settled, and one shortly before the close. Exact times are fixed in a decision record during R0.

**Strategy universe.** All defined-risk structures: long calls and puts, vertical debit and credit spreads, iron condors, butterflies, and other structures whose maximum loss is bounded and computable at entry. Structures with undefined risk are excluded.

**Early assignment, fills, and baselines.** The conservative assignment rule (Section 8), the spread-crossing fill rule (Section 11), and the market-implied and seeded random baselines (Section 9) apply from R0–R1.

**Working process.** The same stage process as the REMY chess engine: a decision record for each material design choice, a result memo at the end of each stage, and explicit owner review and confirmation before the next stage begins.
