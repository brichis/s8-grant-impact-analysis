-- v3 hydrex_daily_flows
WITH legs AS (
  SELECT * FROM (VALUES
    (0x034196aee7968d4c85b00b40c4e88ef706577cfa, 0x4200000000000000000000000000000000000006),
    (0x034196aee7968d4c85b00b40c4e88ef706577cfa, 0xcbada732173e39521cdbe8bf59a6dc85a9fc7b8c),
    (0x8772c46a7d10d7189d21eb635a9d271c83f66263, 0x4200000000000000000000000000000000000006),
    (0x8772c46a7d10d7189d21eb635a9d271c83f66263, 0xcbd06e5a2b0c65597161de254aa074e489deb510),
    (0x8c8063a449eb9f020449fec4d9f0bca845188929, 0x4200000000000000000000000000000000000006),
    (0x8c8063a449eb9f020449fec4d9f0bca845188929, 0xcb17c9db87b595717c857a08468793f5bab6445f),
    (0xd604cf300a4ae4345426df42ffb296aa35b4bef2, 0x50c5725949a6f0c72e6c4a641f24049a917db0cb),
    (0xd604cf300a4ae4345426df42ffb296aa35b4bef2, 0x833589fcd6edb6e08f4c7c32d4f71b54bda02913),
    (0xee58348059c9ad6ac345be79c399da0c200627ed, 0x4200000000000000000000000000000000000006),
    (0xee58348059c9ad6ac345be79c399da0c200627ed, 0xcb585250f852c6c6bf90434ab21a00f02833a4af)
  ) AS v (pool, token)
),
flows AS (
  SELECT
    l.pool,
    l.token,
    DATE(t.block_time - INTERVAL '6' HOUR) AS local_date,
    CASE
      WHEN t."to" = l.pool AND t."from" = l.pool THEN CAST(0 AS INT256)
      WHEN t."to" = l.pool THEN CAST(t.amount_raw AS INT256)
      ELSE CAST(0 AS INT256) - CAST(t.amount_raw AS INT256)
    END AS signed_raw
  FROM tokens.transfers t
  JOIN legs l
    ON t.contract_address = l.token
   AND (t."to" = l.pool OR t."from" = l.pool)
  WHERE t.blockchain = 'base'
    AND t.block_date BETWEEN DATE '2025-12-20' AND DATE '2026-03-26'
    AND t.block_number > 39710526
    AND t.block_number <= 43857726
),
daily AS (
  SELECT pool, token, local_date, SUM(signed_raw) AS net, COUNT(*) AS transfers
  FROM flows
  GROUP BY pool, token, local_date
)
SELECT
  d.pool,
  d.token,
  e.symbol,
  e.decimals,
  d.local_date,
  CAST(d.net AS VARCHAR) AS net_raw,
  d.transfers
FROM daily d
LEFT JOIN tokens.erc20 e
  ON e.blockchain = 'base' AND e.contract_address = d.token
ORDER BY d.pool, d.token, d.local_date
