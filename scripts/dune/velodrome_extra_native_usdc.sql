-- v1 velodrome_extra_native_usdc
WITH legs AS (
  SELECT * FROM (VALUES
    ('optimism', 0x2fa71491f8070fa644d97b4782db5734854c0f6f, 0x0b2c639c533813f4aa9d7837caf62653d097ff85)
  ) AS v (chain, pool, token)
),
flows AS (
  SELECT
    l.chain,
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
    ON t.blockchain = l.chain
   AND t.contract_address = l.token
   AND (t."to" = l.pool OR t."from" = l.pool)
  WHERE t.blockchain IN ('optimism')
    AND t.block_date BETWEEN DATE '2026-02-11' AND DATE '2026-09-01'
    AND t.block_time > from_unixtime(1770789599)
    AND t.block_time <= from_unixtime(1788242399)
),
daily AS (
  SELECT chain, pool, token, local_date, SUM(signed_raw) AS net, COUNT(*) AS transfers
  FROM flows
  GROUP BY chain, pool, token, local_date
)
SELECT
  d.chain AS blockchain,
  d.pool,
  d.token,
  e.symbol,
  e.decimals,
  d.local_date,
  CAST(d.net AS VARCHAR) AS net_raw,
  d.transfers
FROM daily d
LEFT JOIN tokens.erc20 e
  ON e.blockchain = d.chain AND e.contract_address = d.token
ORDER BY d.chain, d.pool, d.token, d.local_date
