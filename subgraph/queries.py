# All GraphQL query strings for The Graph subgraph.
# Your subgraph must index TrustGuardRouter events.
# See scripts/deploy_subgraph.md for setup instructions.


GET_AGENT_TRUST_SCORES = """
query GetAgentTrustScores($minScore: Int!, $limit: Int!) {
  trustScoreUpdateds(
    where: { newScore_gte: $minScore }
    orderBy: newScore
    orderDirection: desc
    first: $limit
  ) {
    agent
    success
    newScore
    blockTimestamp
  }
}
"""

GET_ESCROW_HISTORY = """
query GetEscrowHistory($payerAddress: String, $payeeAddress: String) {
  escrowCreateds(
    where: {
      payer: $payerAddress
      payee: $payeeAddress
    }
    orderBy: blockTimestamp
    orderDirection: desc
    first: 50
  ) {
    escrowId
    payer
    payee
    payeeAgentId
    amount
    token
    blockTimestamp
    transactionHash
  }
}
"""

GET_ESCROW_BY_ID = """
query GetEscrowById($escrowId: Bytes!) {
  escrowCreateds(where: { escrowId: $escrowId }) {
    escrowId
    payer
    payee
    payeeAgentId
    amount
    token
    blockTimestamp
    transactionHash
  }
  escrowReleaseds(where: { escrowId: $escrowId }) {
    escrowId
    payee
    payout
    fee
    blockTimestamp
    transactionHash
  }
  escrowRefundeds(where: { escrowId: $escrowId }) {
    escrowId
    payer
    amount
    blockTimestamp
    transactionHash
  }
}
"""

GET_VERIFICATION_PROBES = """
query GetVerificationProbes($agentAddress: String!) {
  verificationProbeRecordeds(
    where: { agent: $agentAddress }
    orderBy: blockTimestamp
    orderDirection: desc
    first: 20
  ) {
    agent
    agentId
    passed
    evidence
    blockTimestamp
    transactionHash
  }
}
"""

GET_FEES_COLLECTED = """
query GetFeesCollected($token: String) {
  feeCollecteds(
    where: { token: $token }
    orderBy: blockTimestamp
    orderDirection: desc
    first: 100
  ) {
    escrowId
    token
    amount
    blockTimestamp
    transactionHash
  }
}
"""

GET_BLACKLISTED_AGENTS = """
query GetBlacklistedAgents {
  agentBlacklisteds {
    agent
    reason
    blockTimestamp
  }
}
"""

GET_AGENT_SCORE_HISTORY = """
query GetAgentScoreHistory($agentAddress: String!) {
  trustScoreUpdateds(
    where: { agent: $agentAddress }
    orderBy: blockTimestamp
    orderDirection: desc
    first: 50
  ) {
    agent
    success
    newScore
    blockTimestamp
    transactionHash
  }
}
"""

# Existing queries stay as they are for sepolia/TrustGuard events

# -------------------------------------------------------------------------
# Mainnet queries — uses ERC-8004 registry and reputation data directly
# -------------------------------------------------------------------------

GET_REGISTERED_AGENTS = """
query GetRegisteredAgents($limit: Int!, $skip: Int!) {
  registeredAgents(
    first: $limit
    skip: $skip
    orderBy: registeredAt
    orderDirection: desc
  ) {
    id
    agentId
    owner
    cardURI
    registeredAt
    transactionHash
  }
}
"""

GET_AGENT_REPUTATION_SUMMARY = """
query GetAgentReputationSummary($agentId: String!) {
  agentReputationSummaries(where: { agentId: $agentId }) {
    agentId
    totalFeedback
    positiveCount
    negativeCount
    cumulativeScore
    lastUpdated
  }
}
"""

GET_ALL_REPUTATION_SUMMARIES = """
query GetAllReputationSummaries($limit: Int!) {
  agentReputationSummaries(
    first: $limit
    orderBy: totalFeedback
    orderDirection: desc
  ) {
    agentId
    totalFeedback
    positiveCount
    negativeCount
    cumulativeScore
    lastUpdated
  }
}
"""

GET_SELF_VERIFIED_AGENTS = """
query GetSelfVerifiedAgents($limit: Int!) {
  selfVerifiedAgents(
    first: $limit
    orderBy: verifiedAt
    orderDirection: desc
  ) {
    id
    agentId
    owner
    verifiedAt
    transactionHash
  }
}
"""

GET_RECENT_FEEDBACK_FOR_AGENT = """
query GetRecentFeedback($agentId: String!, $limit: Int!) {
  reputationFeedbacks(
    where: { agentId: $agentId }
    orderBy: blockTimestamp
    orderDirection: desc
    first: $limit
  ) {
    agentId
    clientAddress
    value
    tag1
    tag2
    endpoint
    blockTimestamp
    transactionHash
  }
}
"""

GET_REPUTATION_SUMMARIES_PAGE = """
query GetReputationSummariesPage($limit: Int!, $skip: Int!) {
  agentReputationSummaries(
    first: $limit
    skip: $skip
    orderBy: totalFeedback
    orderDirection: desc
  ) {
    agentId
    totalFeedback
    positiveCount
    negativeCount
    cumulativeScore
    lastUpdated
  }
}
"""