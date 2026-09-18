-- v2 curve_pools_daily_flows
WITH legs AS (
  SELECT * FROM (VALUES
    (0xd8dd9a8b2aca88e68c46af9008259d0ec04b7751, 0xc52d7f23a2e460248db6ee192cb23dd12bddcbf6),
    (0xd8dd9a8b2aca88e68c46af9008259d0ec04b7751, 0x4200000000000000000000000000000000000042),
    (0xd8dd9a8b2aca88e68c46af9008259d0ec04b7751, 0x0994206dfe8de6ec6920ff4d779b0d950605fb53),
    (0x4456d13fc6736e8e8330394c0c622103e06ea419, 0xc52d7f23a2e460248db6ee192cb23dd12bddcbf6),
    (0x4456d13fc6736e8e8330394c0c622103e06ea419, 0x68f180fcce6836688e9084f035309e29bf0a2095),
    (0x4456d13fc6736e8e8330394c0c622103e06ea419, 0x4200000000000000000000000000000000000006),
    (0x00d09cccda09bb97132293c571ead11630d57a71, 0xc52d7f23a2e460248db6ee192cb23dd12bddcbf6),
    (0x00d09cccda09bb97132293c571ead11630d57a71, 0x289f635106d5b822a505b39ac237a0ae9189335b)
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
  WHERE t.blockchain = 'optimism'
    AND t.block_date BETWEEN DATE '2026-04-17' AND DATE '2026-09-16'
    AND t.block_time > from_unixtime(1776491999)
    AND t.block_time <= from_unixtime(1789538399)
),
daily AS (
  SELECT pool, token, local_date, SUM(signed_raw) AS net, COUNT(*) AS transfers
  FROM flows
  GROUP BY pool, token, local_date
)
SELECT
  'optimism' AS blockchain,
  d.pool,
  d.token,
  e.symbol,
  e.decimals,
  d.local_date,
  CAST(d.net AS VARCHAR) AS net_raw,
  d.transfers
FROM daily d
LEFT JOIN tokens.erc20 e
  ON e.blockchain = 'optimism' AND e.contract_address = d.token
ORDER BY d.pool, d.token, d.local_date
