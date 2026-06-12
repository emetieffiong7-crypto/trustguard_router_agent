# """
# TrustGuard Composite Scoring Engine

# Computes trust scores for all agents using multiple independent signals.
# Stores results locally and posts onchain only for agents that merit it.

# Score components (max 100 total):
#     Registration age        max 15 pts   weight 0.15
#     Card URI quality        max 20 pts   weight 0.20
#     ERC-8004 reputation     max 35 pts   weight 0.35  (Bayesian average)
#     Self verification       max 20 pts   weight 0.20
#     TrustGuard probe        max 10 pts   weight 0.10

# Zero score = all signals are absent simultaneously = genuinely dormant.
# """

# import logging
# import asyncio
# from datetime import datetime, timezone
# from typing import Optional
# from sqlalchemy.ext.asyncio import AsyncSession
# from sqlalchemy import select, func

# from config import settings
# from db.models import Agent
# from db.base import AsyncSessionFactory

# logger = logging.getLogger("trustguard.scoring")

# # -------------------------------------------------------------------------
# # Bayesian prior for reputation signal
# # Pulls unknown agents toward neutral rather than letting a single
# # high score dominate or leaving zero-feedback agents at 0.
# # -------------------------------------------------------------------------
# BAYESIAN_PRIOR_COUNT = 5    # virtual prior feedback count
# BAYESIAN_PRIOR_MEAN  = 50   # neutral prior mean (out of 100)

# # Minimum composite score threshold to post onchain
# ONCHAIN_POST_THRESHOLD = 20

# # How many points qualify as a significant score change worth posting
# ONCHAIN_CHANGE_THRESHOLD = 10


# def compute_registration_age_score(registered_at: Optional[datetime]) -> int:
#     """
#     Score based on how long the agent has been registered.
#     Rewards longevity without penalising new agents too harshly.

#     0    pts — no registration timestamp
#     0    pts — registered less than 7 days ago
#     5    pts — 7 to 30 days
#     10   pts — 30 to 90 days
#     15   pts — more than 90 days
#     """
#     if not registered_at:
#         return 0

#     now      = datetime.now(timezone.utc)
#     reg_time = registered_at.replace(tzinfo=timezone.utc) \
#                if registered_at.tzinfo is None else registered_at
#     age_days = (now - reg_time).days

#     if age_days < 7:
#         return 0
#     if age_days < 30:
#         return 5
#     if age_days < 90:
#         return 10
#     return 15


# def compute_card_quality_score(
#     card_uri:     Optional[str],
#     a2a_endpoint: Optional[str],
#     supports_x402: bool,
#     name:         Optional[str],
# ) -> int:
#     """
#     Score based on how complete and valid the agent's card is.

#     0  pts — no card URI
#     5  pts — card URI exists but no endpoints parsed
#     12 pts — card URI and at least a name parsed
#     17 pts — card URI and A2A endpoint present
#     20 pts — card URI, A2A endpoint, and x402 support
#     """
#     if not card_uri:
#         return 0

#     score = 5  # has card URI

#     if name:
#         score += 7  # card was successfully parsed

#     if a2a_endpoint:
#         score += 5  # has A2A endpoint

#     if supports_x402:
#         score += 3  # supports x402 payments

#     return min(score, 20)


# def compute_reputation_score(
#     total_feedback: int,
#     positive_count: int,
#     cumulative_score: int,
# ) -> int:
#     """
#     Bayesian average of ERC-8004 reputation registry feedback.
#     Smooths out single-feedback outliers and pulls unknown agents
#     toward a neutral 50 rather than 0.

#     Returns 0 to 35 pts mapped from a 0-100 Bayesian mean.
#     """
#     if total_feedback == 0:
#         # No feedback at all — neutral prior gives 0 contribution
#         # We do not penalise for unknown, but we do not reward either
#         return 0

#     # Raw mean from actual feedback
#     raw_mean = cumulative_score / total_feedback if total_feedback > 0 else 0

#     # Bayesian smoothed mean
#     bayesian_mean = (
#         (BAYESIAN_PRIOR_COUNT * BAYESIAN_PRIOR_MEAN) +
#         (total_feedback * raw_mean)
#     ) / (BAYESIAN_PRIOR_COUNT + total_feedback)

#     # Scale to 0-35 range
#     return int((bayesian_mean / 100) * 35)


# def compute_self_verification_score(
#     self_verified:    bool,
#     self_proof_fresh: bool,
# ) -> int:
#     """
#     Score based on Self Agent ID proof-of-human verification.

#     0  pts — not verified
#     10 pts — verified but proof expired
#     20 pts — verified with fresh proof
#     """
#     if not self_verified:
#         return 0
#     if not self_proof_fresh:
#         return 10
#     return 20


# def compute_probe_score(
#     last_probed_at:      Optional[datetime],
#     consecutive_failures: int,
#     a2a_endpoint:        Optional[str],
# ) -> int:
#     """
#     Score based on TrustGuard's direct endpoint probes.
#     Only contributes if the agent has actually been probed.
#     No penalty for never being probed — that is not the agent's fault.

#     0  pts — never probed or no endpoint to probe
#     10 pts — last probe passed
#     5  pts — last probe failed but agent has endpoint
#     0  pts — 3+ consecutive failures (dormant signal)
#     """
#     if not last_probed_at or not a2a_endpoint:
#         return 0

#     if consecutive_failures >= 3:
#         return 0

#     if consecutive_failures > 0:
#         return 5

#     return 10


# def compute_composite_score(
#     registered_at:        Optional[datetime],
#     card_uri:             Optional[str],
#     a2a_endpoint:         Optional[str],
#     supports_x402:        bool,
#     name:                 Optional[str],
#     total_feedback:       int,
#     positive_count:       int,
#     cumulative_score:     int,
#     self_verified:        bool,
#     self_proof_fresh:     bool,
#     last_probed_at:       Optional[datetime],
#     consecutive_failures: int,
# ) -> dict:
#     """
#     Compute the full composite trust score for an agent.
#     Returns a dict with the total score and each component.
#     """
#     age_score        = compute_registration_age_score(registered_at)
#     card_score       = compute_card_quality_score(
#         card_uri, a2a_endpoint, supports_x402, name
#     )
#     rep_score        = compute_reputation_score(
#         total_feedback, positive_count, cumulative_score
#     )
#     self_score       = compute_self_verification_score(
#         self_verified, self_proof_fresh
#     )
#     probe_score      = compute_probe_score(
#         last_probed_at, consecutive_failures, a2a_endpoint
#     )

#     total = age_score + card_score + rep_score + self_score + probe_score
#     total = min(100, max(0, total))

#     return {
#         "total":           total,
#         "age_score":       age_score,
#         "card_score":      card_score,
#         "rep_score":       rep_score,
#         "self_score":      self_score,
#         "probe_score":     probe_score,
#     }


# async def fetch_reputation_data(agent_ids: list[int]) -> dict:
#     """
#     Fetch reputation summaries from subgraph for a list of agent IDs.
#     Returns dict keyed by agent_id (int).
#     """
#     from subgraph.client import subgraph_client
#     from subgraph.queries import GET_ALL_REPUTATION_SUMMARIES

#     try:
#         data = await subgraph_client.query(
#             GET_ALL_REPUTATION_SUMMARIES,
#             variables={"limit": 1000}
#         )
#         summaries = data.get("agentReputationSummaries", [])
#         return {
#             int(s["agentId"]): {
#                 "total_feedback":    int(s.get("totalFeedback",   0)),
#                 "positive_count":    int(s.get("positiveCount",   0)),
#                 "negative_count":    int(s.get("negativeCount",   0)),
#                 "cumulative_score":  int(s.get("cumulativeScore", 0)),
#             }
#             for s in summaries
#         }
#     except Exception as e:
#         logger.warning(f"Could not fetch reputation from subgraph: {e}")
#         return {}


# async def fetch_self_verified_owners() -> set:
#     """
#     Fetch Self-verified agent owner addresses from subgraph.
#     Returns a set of lowercase owner addresses.
#     """
#     from subgraph.client import subgraph_client
#     from subgraph.queries import GET_SELF_VERIFIED_AGENTS

#     try:
#         data = await subgraph_client.query(
#             GET_SELF_VERIFIED_AGENTS,
#             variables={"limit": 500}
#         )
#         return {
#             s["owner"].lower()
#             for s in data.get("selfVerifiedAgents", [])
#         }
#     except Exception as e:
#         logger.warning(f"Could not fetch Self verified agents: {e}")
#         return set()


# async def should_post_onchain(
#     agent:     Agent,
#     new_score: int,
#     rep_data:  dict,
# ) -> bool:
#     """
#     Decide whether this agent's score update should be posted onchain.
#     Conservative — only posts for agents with real positive signals.

#     Returns True only when ALL of:
#     - New score exceeds ONCHAIN_POST_THRESHOLD (20)
#     - At least one strong positive signal exists
#     - Score has changed meaningfully from last known value
#     """
#     if new_score < ONCHAIN_POST_THRESHOLD:
#         return False

#     # Must have at least one strong signal
#     has_reputation   = rep_data.get("total_feedback", 0) > 0
#     is_self_verified = agent.self_verified
#     passed_probe     = (
#         agent.last_probed_at is not None and
#         agent.consecutive_failures == 0
#     )

#     if not any([has_reputation, is_self_verified, passed_probe]):
#         return False

#     # Score must have changed meaningfully
#     score_change = abs(new_score - (agent.trust_score or 0))
#     if score_change < ONCHAIN_CHANGE_THRESHOLD:
#         return False

#     return True


# async def post_score_onchain(
#     agent:    Agent,
#     score:    int,
#     evidence: str,
# ) -> Optional[str]:
#     """
#     Post a TrustScoreUpdated event for an agent via recordVerificationProbe.
#     Returns tx hash or None on failure.
#     """
#     from onchain.contracts import record_verification_probe

#     try:
#         tx_hash = await record_verification_probe(
#             agent_address = agent.owner_address,
#             agent_id      = agent.agent_id or 0,
#             passed        = score >= 50,
#             evidence      = evidence[:500]
#         )
#         logger.info(
#             f"Posted score onchain: agent={agent.agent_id} "
#             f"score={score} tx={tx_hash}"
#         )
#         return tx_hash
#     except Exception as e:
#         logger.warning(f"Failed to post score onchain for agent {agent.agent_id}: {e}")
#         return None


# async def score_agents_batch(
#     agents:            list[Agent],
#     rep_data:          dict,
#     self_verified_set: set,
#     post_onchain:      bool = True,
#     db:                Optional[AsyncSession] = None,
# ) -> dict:
#     """
#     Score a batch of agents and update the database.
#     Optionally posts qualifying scores onchain.

#     Returns summary statistics.
#     """
#     updated       = 0
#     posted_onchain = 0
#     total_score   = 0

#     for agent in agents:
#         # Get registration timestamp from first_seen_at as proxy
#         # (subgraph registeredAt would be more accurate when available)
#         registered_at = agent.first_seen_at

#         # Get reputation data
#         rep = rep_data.get(agent.agent_id or 0, {})

#         # Update Self verification from subgraph
#         is_self_verified = (
#             agent.owner_address.lower() in self_verified_set
#             or agent.self_verified
#         )
#         if is_self_verified and not agent.self_verified:
#             agent.self_verified    = True
#             agent.self_proof_fresh = True

#         # Compute composite score
#         result = compute_composite_score(
#             registered_at        = registered_at,
#             card_uri             = agent.card_uri,
#             a2a_endpoint         = agent.a2a_endpoint,
#             supports_x402        = agent.supports_x402 or False,
#             name                 = agent.name,
#             total_feedback       = rep.get("total_feedback",   0),
#             positive_count       = rep.get("positive_count",   0),
#             cumulative_score     = rep.get("cumulative_score", 0),
#             self_verified        = is_self_verified,
#             self_proof_fresh     = agent.self_proof_fresh or False,
#             last_probed_at       = agent.last_probed_at,
#             consecutive_failures = agent.consecutive_failures or 0,
#         )

#         new_score = result["total"]
#         total_score += new_score

#         # Build evidence string for onchain posting
#         evidence = (
#             f"composite_score:{new_score} "
#             f"age:{result['age_score']} "
#             f"card:{result['card_score']} "
#             f"rep:{result['rep_score']} "
#             f"self:{result['self_score']} "
#             f"probe:{result['probe_score']}"
#         )

#         # Decide whether to post onchain
#         if post_onchain and db and await should_post_onchain(agent, new_score, rep):
#             tx_hash = await post_score_onchain(agent, new_score, evidence)
#             if tx_hash:
#                 posted_onchain += 1
#             # Small delay to avoid overwhelming the RPC
#             await asyncio.sleep(0.5)

#         # Update the agent record
#         agent.trust_score   = new_score
#         agent.last_updated_at = datetime.utcnow()
#         updated += 1

#     if db:
#         await db.commit()

#     avg_score = int(total_score / len(agents)) if agents else 0

#     return {
#         "scored":          updated,
#         "posted_onchain":  posted_onchain,
#         "avg_score":       avg_score,
#     }


# async def run_full_scoring_pass(
#     batch_size:    int  = 100,
#     post_onchain:  bool = True,
#     min_agent_id:  int  = 0,
# ) -> dict:
#     """
#     Score all agents in the database in batches.
#     This is the main entry point called on startup and by the admin API.

#     Returns summary of the full pass.
#     """
#     logger.info("=" * 50)
#     logger.info("Starting full scoring pass")
#     logger.info(f"  post_onchain: {post_onchain}")
#     logger.info(f"  batch_size:   {batch_size}")
#     logger.info("=" * 50)

#     # Fetch all reputation and Self data upfront
#     # Better to make 2 subgraph calls than N calls per agent
#     logger.info("Fetching reputation data from subgraph...")
#     rep_data = await fetch_reputation_data([])

#     logger.info("Fetching Self verified agents from subgraph...")
#     self_verified_set = await fetch_self_verified_owners()

#     logger.info(
#         f"Subgraph data: {len(rep_data)} reputation records, "
#         f"{len(self_verified_set)} Self verified owners"
#     )

#     total_scored   = 0
#     total_onchain  = 0
#     offset         = 0

#     async with AsyncSessionFactory() as db:
#         while True:
#             # Fetch a batch of agents
#             result = await db.execute(
#                 select(Agent)
#                 .where(Agent.agent_id >= min_agent_id)
#                 .order_by(Agent.agent_id.asc())
#                 .limit(batch_size)
#                 .offset(offset)
#             )
#             agents = result.scalars().all()

#             if not agents:
#                 break

#             logger.info(
#                 f"Scoring agents {offset} to {offset + len(agents)} "
#                 f"(total so far: {total_scored})"
#             )

#             stats = await score_agents_batch(
#                 agents            = agents,
#                 rep_data          = rep_data,
#                 self_verified_set = self_verified_set,
#                 post_onchain      = post_onchain,
#                 db                = db,
#             )

#             total_scored  += stats["scored"]
#             total_onchain += stats["posted_onchain"]
#             offset        += batch_size

#             # Small delay between batches
#             await asyncio.sleep(0.1)

#     logger.info("=" * 50)
#     logger.info("Scoring pass complete")
#     logger.info(f"  Total scored:    {total_scored}")
#     logger.info(f"  Posted onchain:  {total_onchain}")
#     logger.info("=" * 50)

#     return {
#         "total_scored":   total_scored,
#         "posted_onchain": total_onchain,
#     }

"""
TrustGuard Composite Scoring Engine

Score components (max 100 total):
    Registration age        max 15 pts
    Card URI quality        max 20 pts
    ERC-8004 reputation     max 35 pts  (Bayesian smoothed)
    Self verification       max 20 pts
    TrustGuard probe        max 10 pts

Zero score means all signals absent simultaneously — genuinely dormant.
Agents with only a registration and card get 5-20 pts (low but non-zero).
"""

import logging
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from config import settings
from db.models import Agent
from db.base import AsyncSessionFactory

logger = logging.getLogger("trustguard.scoring")

BAYESIAN_PRIOR_COUNT  = 5
BAYESIAN_PRIOR_MEAN   = 50
ONCHAIN_POST_THRESHOLD  = 25
ONCHAIN_CHANGE_THRESHOLD = 10


def compute_registration_age_score(registered_at: Optional[datetime]) -> int:
    """
    Score based on actual onchain registration age.
    Uses registered_at (from subgraph or estimated) not first_seen_at.

    0  pts — no timestamp available
    0  pts — less than 7 days
    5  pts — 7 to 30 days
    10 pts — 30 to 90 days
    15 pts — more than 90 days
    """
    if not registered_at:
        return 0

    now = datetime.now(timezone.utc)
    reg = registered_at.replace(tzinfo=timezone.utc) \
          if registered_at.tzinfo is None else registered_at

    age_days = (now - reg).days

    if age_days < 7:
        return 0
    if age_days < 30:
        return 5
    if age_days < 90:
        return 10
    return 15


def compute_card_quality_score(
    card_uri:      Optional[str],
    a2a_endpoint:  Optional[str],
    supports_x402: bool,
    name:          Optional[str],
    mcp_endpoint:  Optional[str] = None,
) -> int:
    """
    Score based on completeness of the agent's card.

    0  pts — no card URI
    5  pts — has card URI (even if not enriched yet)
    10 pts — card URI + name parsed successfully
    15 pts — card URI + name + A2A endpoint
    18 pts — card URI + name + A2A + MCP endpoint
    20 pts — card URI + name + A2A + x402 support
    """
    if not card_uri:
        return 0

    score = 5  # has card URI

    if name:
        score += 5  # card was parsed successfully

    if a2a_endpoint:
        score += 5  # has A2A endpoint

    if mcp_endpoint:
        score += 3  # has MCP endpoint

    if supports_x402:
        score += 2  # supports x402 payments

    return min(score, 20)


def compute_reputation_score(
    total_feedback:   int,
    positive_count:   int,
    cumulative_score: int,
) -> int:
    """
    Bayesian smoothed reputation from ERC-8004 registry feedback.
    Returns 0-35 pts.

    Agents with no feedback get 0 — neutral, not penalised.
    Agents with feedback are smoothed toward the prior (50/100)
    until they accumulate enough feedback for the signal to dominate.
    """
    if total_feedback == 0:
        return 0

    raw_mean = cumulative_score / total_feedback

    bayesian_mean = (
        (BAYESIAN_PRIOR_COUNT * BAYESIAN_PRIOR_MEAN) +
        (total_feedback * raw_mean)
    ) / (BAYESIAN_PRIOR_COUNT + total_feedback)

    return int((bayesian_mean / 100) * 35)


def compute_self_verification_score(
    self_verified:    bool,
    self_proof_fresh: bool,
) -> int:
    """
    0  pts — not verified
    10 pts — verified, proof expired
    20 pts — verified, proof fresh
    """
    if not self_verified:
        return 0
    return 20 if self_proof_fresh else 10


def compute_probe_score(
    last_probed_at:       Optional[datetime],
    consecutive_failures: int,
    a2a_endpoint:         Optional[str],
) -> int:
    """
    0  pts — never probed (not a penalty, just unknown)
    10 pts — last probe passed
    5  pts — last probe failed but has endpoint
    0  pts — 3+ consecutive failures
    """
    if not last_probed_at or not a2a_endpoint:
        return 0
    if consecutive_failures >= 3:
        return 0
    if consecutive_failures > 0:
        return 5
    return 10


def compute_composite_score(
    registered_at:        Optional[datetime],
    card_uri:             Optional[str],
    a2a_endpoint:         Optional[str],
    supports_x402:        bool,
    name:                 Optional[str],
    mcp_endpoint:         Optional[str],
    total_feedback:       int,
    positive_count:       int,
    cumulative_score:     int,
    self_verified:        bool,
    self_proof_fresh:     bool,
    last_probed_at:       Optional[datetime],
    consecutive_failures: int,
) -> dict:
    """Compute the full composite trust score. Returns score and components."""

    age_score   = compute_registration_age_score(registered_at)
    card_score  = compute_card_quality_score(
        card_uri, a2a_endpoint, supports_x402, name, mcp_endpoint
    )
    rep_score   = compute_reputation_score(
        total_feedback, positive_count, cumulative_score
    )
    self_score  = compute_self_verification_score(
        self_verified, self_proof_fresh
    )
    probe_score = compute_probe_score(
        last_probed_at, consecutive_failures, a2a_endpoint
    )

    total = min(100, max(0,
        age_score + card_score + rep_score + self_score + probe_score
    ))

    return {
        "total":       total,
        "age_score":   age_score,
        "card_score":  card_score,
        "rep_score":   rep_score,
        "self_score":  self_score,
        "probe_score": probe_score,
    }


async def fetch_all_reputation_data() -> dict:
    """Fetch all reputation summaries from subgraph. Returns dict by agentId."""
    from subgraph.client import subgraph_client

    GET_ALL_REP = """
    query GetAllRep($limit: Int!, $skip: Int!) {
      agentReputationSummaries(first: $limit, skip: $skip) {
        agentId
        totalFeedback
        positiveCount
        negativeCount
        cumulativeScore
      }
    }
    """

    all_data = {}
    skip     = 0

    while True:
        try:
            data = await subgraph_client.query(
                GET_ALL_REP,
                variables={"limit": 1000, "skip": skip}
            )
            summaries = data.get("agentReputationSummaries", [])
            if not summaries:
                break

            for s in summaries:
                all_data[int(s["agentId"])] = {
                    "total_feedback":   int(s.get("totalFeedback",   0)),
                    "positive_count":   int(s.get("positiveCount",   0)),
                    "negative_count":   int(s.get("negativeCount",   0)),
                    "cumulative_score": int(s.get("cumulativeScore", 0)),
                }

            skip += 1000
            if len(summaries) < 1000:
                break

        except Exception as e:
            logger.warning(f"Reputation fetch failed at skip={skip}: {e}")
            break

    logger.info(f"Fetched reputation data for {len(all_data)} agents")
    return all_data


# async def fetch_self_verified_owners() -> set:
#     """Fetch Self verified agent owner addresses from subgraph."""
#     from subgraph.client import subgraph_client
#     from subgraph.queries import GET_SELF_VERIFIED_AGENTS

#     try:
#         data = await subgraph_client.query(
#             GET_SELF_VERIFIED_AGENTS,
#             variables={"limit": 500}
#         )
#         owners = {
#             s["owner"].lower()
#             for s in data.get("selfVerifiedAgents", [])
#         }
#         logger.info(f"Fetched {len(owners)} Self verified owners")
#         return owners
#     except Exception as e:
#         logger.warning(f"Self verified fetch failed: {e}")
#         return set()

async def fetch_self_verified_owners() -> dict:
    """
    Fetch Self verification data from both subgraph and Self REST API.
    Returns dict keyed by owner_address (lowercase) with verification details.

    Strategy:
    1. Query subgraph for Transfer events (fast, indexed)
    2. For agents in database with no Self status, query Self REST API
       in batches (slower but complete)

    Returns combined set of verified owner addresses.
    """
    from subgraph.client import subgraph_client
    from subgraph.queries import GET_SELF_VERIFIED_AGENTS
    from self_id.client import self_id_client

    verified_map: dict = {}

    # Step 1 — subgraph (fast path)
    try:
        data = await subgraph_client.query(
            GET_SELF_VERIFIED_AGENTS,
            variables={"limit": 500}
        )
        for s in data.get("selfVerifiedAgents", []):
            owner = s["owner"].lower()
            verified_map[owner] = {
                "verified":    True,
                "proof_fresh": True,
                "source":      "subgraph",
            }
        logger.info(f"Subgraph Self data: {len(verified_map)} verified owners")
    except Exception as e:
        logger.warning(f"Subgraph Self fetch failed: {e}")

    # Step 2 — Self REST API for agents in database not yet marked verified
    # Only run this for agents that have agentIds (not just owner addresses)
    try:
        from db.base import AsyncSessionFactory
        from db.models import Agent
        from sqlalchemy import select

        async with AsyncSessionFactory() as db:
            # Get agents that are NOT already Self verified
            # and have been registered long enough to potentially have verification
            result = await db.execute(
                select(Agent.agent_id, Agent.owner_address)
                .where(
                    Agent.self_verified == False,
                    Agent.agent_id != None,
                    Agent.agent_id <= 500  # limit API calls — focus on active agents
                )
                .order_by(Agent.trust_score.desc())
                .limit(200)
            )
            candidates = result.all()

        if candidates:
            agent_ids = [row.agent_id for row in candidates]
            id_to_owner = {
                row.agent_id: row.owner_address.lower()
                for row in candidates
            }

            logger.info(
                f"Querying Self API for {len(agent_ids)} unverified agents..."
            )

            api_results = await self_id_client.get_verification_batch(
                agent_ids    = agent_ids,
                concurrency  = 5,
                delay_between = 0.2,
            )

            for agent_id, data in api_results.items():
                is_verified = (
                    data.get("isVerified") or
                    data.get("verified", False)
                )
                if is_verified:
                    owner = id_to_owner.get(agent_id, "")
                    if owner:
                        verified_map[owner] = {
                            "verified":              True,
                            "proof_fresh":           data.get("isProofFresh", False),
                            "verification_strength": data.get("verificationStrength"),
                            "source":                "self_api",
                        }

            logger.info(
                f"Self API added {len([v for v in api_results.values() if v.get('isVerified')])} "
                f"verified agents"
            )

    except Exception as e:
        logger.warning(f"Self API enrichment failed: {e}")

    logger.info(f"Total Self verified owners: {len(verified_map)}")
    return verified_map

async def should_post_onchain(
    agent:     Agent,
    new_score: int,
    rep:       dict,
) -> bool:
    """
    True only when the agent has real positive signals AND
    the score change is meaningful.
    Conservative to avoid burning gas on dormant agents.
    """
    if new_score < ONCHAIN_POST_THRESHOLD:
        return False

    has_reputation   = rep.get("total_feedback", 0) > 0
    is_self_verified = agent.self_verified
    passed_probe     = (
        agent.last_probed_at is not None and
        (agent.consecutive_failures or 0) == 0
    )

    if not any([has_reputation, is_self_verified, passed_probe]):
        return False

    score_change = abs(new_score - (agent.trust_score or 0))
    return score_change >= ONCHAIN_CHANGE_THRESHOLD


async def post_score_onchain(agent: Agent, score: int, evidence: str) -> Optional[str]:
    """Post score to contract. Returns tx hash or None."""
    from onchain.contracts import record_verification_probe
    try:
        tx_hash = await record_verification_probe(
            agent_address = agent.owner_address,
            agent_id      = agent.agent_id or 0,
            passed        = score >= 50,
            evidence      = evidence[:500]
        )
        logger.info(
            f"Onchain: agent={agent.agent_id} score={score} tx={tx_hash}"
        )
        return tx_hash
    except Exception as e:
        logger.warning(f"Onchain post failed for {agent.agent_id}: {e}")
        return None


# async def score_batch(
#     agents:            list[Agent],
#     rep_data:          dict,
#     self_verified_set: set,
#     post_onchain:      bool,
#     db:                AsyncSession,
# ) -> dict:
#     """Score a batch and update database. Returns stats."""

#     scored         = 0
#     posted_onchain = 0
#     total_score    = 0

#     for agent in agents:

#         rep = rep_data.get(agent.agent_id or 0, {})

#         is_self = (
#             agent.owner_address.lower() in self_verified_set
#             or (agent.self_verified or False)
#         )
#         if is_self and not agent.self_verified:
#             agent.self_verified    = True
#             agent.self_proof_fresh = True

#         result = compute_composite_score(
#             registered_at        = agent.registered_at,
#             card_uri             = agent.card_uri,
#             a2a_endpoint         = agent.a2a_endpoint,
#             supports_x402        = agent.supports_x402 or False,
#             name                 = agent.name,
#             mcp_endpoint         = agent.mcp_endpoint,
#             total_feedback       = rep.get("total_feedback",   0),
#             positive_count       = rep.get("positive_count",   0),
#             cumulative_score     = rep.get("cumulative_score", 0),
#             self_verified        = is_self,
#             self_proof_fresh     = agent.self_proof_fresh or False,
#             last_probed_at       = agent.last_probed_at,
#             consecutive_failures = agent.consecutive_failures or 0,
#         )

#         new_score    = result["total"]
#         total_score += new_score

#         evidence = (
#             f"score:{new_score} "
#             f"age:{result['age_score']} "
#             f"card:{result['card_score']} "
#             f"rep:{result['rep_score']} "
#             f"self:{result['self_score']} "
#             f"probe:{result['probe_score']}"
#         )

#         if post_onchain and await should_post_onchain(agent, new_score, rep):
#             tx = await post_score_onchain(agent, new_score, evidence)
#             if tx:
#                 posted_onchain += 1
#             await asyncio.sleep(0.5)

#         agent.trust_score            = new_score
#         agent.score_age_component    = result["age_score"]
#         agent.score_card_component   = result["card_score"]
#         agent.score_rep_component    = result["rep_score"]
#         agent.score_self_component   = result["self_score"]
#         agent.score_probe_component  = result["probe_score"]
#         agent.last_scored_at         = datetime.utcnow()
#         scored += 1

#     await db.commit()

#     return {
#         "scored":         scored,
#         "posted_onchain": posted_onchain,
#         "avg_score":      int(total_score / scored) if scored else 0,
#     }

async def score_batch(
    agents:            list[Agent],
    rep_data:          dict,
    self_verified_map: dict,   # changed from self_verified_set: set
    post_onchain:      bool,
    db:                AsyncSession,
) -> dict:
    """Score a batch and update database."""

    scored         = 0
    posted_onchain = 0
    total_score    = 0

    for agent in agents:
        rep = rep_data.get(agent.agent_id or 0, {})

        # Look up Self verification from the combined map
        owner_lower    = agent.owner_address.lower()
        self_data      = self_verified_map.get(owner_lower, {})
        is_self        = self_data.get("verified", False) or (agent.self_verified or False)
        is_self_fresh  = self_data.get("proof_fresh", False) or (agent.self_proof_fresh or False)

        if is_self and not agent.self_verified:
            agent.self_verified    = True
            agent.self_proof_fresh = is_self_fresh

        result = compute_composite_score(
            registered_at        = agent.registered_at,
            card_uri             = agent.card_uri,
            a2a_endpoint         = agent.a2a_endpoint,
            supports_x402        = agent.supports_x402 or False,
            name                 = agent.name,
            mcp_endpoint         = agent.mcp_endpoint,
            total_feedback       = rep.get("total_feedback",   0),
            positive_count       = rep.get("positive_count",   0),
            cumulative_score     = rep.get("cumulative_score", 0),
            self_verified        = is_self,
            self_proof_fresh     = is_self_fresh,
            last_probed_at       = agent.last_probed_at,
            consecutive_failures = agent.consecutive_failures or 0,
        )

        new_score    = result["total"]
        total_score += new_score

        evidence = (
            f"score:{new_score} "
            f"age:{result['age_score']} "
            f"card:{result['card_score']} "
            f"rep:{result['rep_score']} "
            f"self:{result['self_score']} "
            f"probe:{result['probe_score']}"
        )

        if post_onchain and await should_post_onchain(agent, new_score, rep):
            tx = await post_score_onchain(agent, new_score, evidence)
            if tx:
                posted_onchain += 1
            await asyncio.sleep(0.5)

        agent.trust_score           = new_score
        agent.score_age_component   = result["age_score"]
        agent.score_card_component  = result["card_score"]
        agent.score_rep_component   = result["rep_score"]
        agent.score_self_component  = result["self_score"]
        agent.score_probe_component = result["probe_score"]
        agent.last_scored_at        = datetime.utcnow()
        scored += 1

    await db.commit()

    return {
        "scored":         scored,
        "posted_onchain": posted_onchain,
        "avg_score":      int(total_score / scored) if scored else 0,
    }


async def run_full_scoring_pass(
    batch_size:   int  = 100,
    post_onchain: bool = False,
    min_agent_id: int  = 0,
) -> dict:

    logger.info("=" * 50)
    logger.info(f"Starting scoring pass | post_onchain={post_onchain}")
    logger.info("=" * 50)

    logger.info("Fetching reputation data...")
    rep_data = await fetch_all_reputation_data()

    logger.info("Fetching Self verified owners (subgraph + API)...")
    self_verified_map = await fetch_self_verified_owners()  # now returns dict

    total_scored  = 0
    total_onchain = 0
    offset        = 0

    async with AsyncSessionFactory() as db:
        while True:
            result = await db.execute(
                select(Agent)
                .where(Agent.agent_id >= min_agent_id)
                .order_by(Agent.agent_id.asc())
                .limit(batch_size)
                .offset(offset)
            )
            agents = result.scalars().all()

            if not agents:
                break

            logger.info(
                f"Scoring {offset} to {offset + len(agents)} "
                f"| total: {total_scored}"
            )

            stats = await score_batch(
                agents            = agents,
                rep_data          = rep_data,
                self_verified_map = self_verified_map,  # updated parameter name
                post_onchain      = post_onchain,
                db                = db,
            )

            total_scored  += stats["scored"]
            total_onchain += stats["posted_onchain"]
            offset        += batch_size

            await asyncio.sleep(0.05)

    logger.info("=" * 50)
    logger.info(f"Scoring complete | scored={total_scored} onchain={total_onchain}")
    logger.info("=" * 50)

    return {
        "total_scored":   total_scored,
        "posted_onchain": total_onchain,
    }