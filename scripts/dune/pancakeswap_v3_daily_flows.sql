-- v1 pancakeswap_v3_daily_flows
WITH legs AS (
  SELECT * FROM (VALUES
    (0x1ca42c7219f0cb1b67927e26502320cb98f725bd, 0x60a3e35cc302bfa44cb288bc5a4f316fdb1adb42),
    (0x1ca42c7219f0cb1b67927e26502320cb98f725bd, 0x833589fcd6edb6e08f4c7c32d4f71b54bda02913),
    (0x257fcbae4ac6b26a02e4fc5e1a11e4174b5ce395, 0x2ae3f1ec7f1f5012cfeab0185bfc7aa3cf0dec22),
    (0x257fcbae4ac6b26a02e4fc5e1a11e4174b5ce395, 0x4200000000000000000000000000000000000006),
    (0x302976a386fbb375033be3ac1e4112f76cf42ef7, 0x04c0599ae5a44757c0af6f9ec3b93da8976c150a),
    (0x302976a386fbb375033be3ac1e4112f76cf42ef7, 0x4200000000000000000000000000000000000006),
    (0x345825a980bd94e1480bc4f20fe4e3dae2f23dd3, 0x50c5725949a6f0c72e6c4a641f24049a917db0cb),
    (0x345825a980bd94e1480bc4f20fe4e3dae2f23dd3, 0x833589fcd6edb6e08f4c7c32d4f71b54bda02913),
    (0x5f07bb9fee6062e9d09a52e6d587c64bad6ba706, 0x833589fcd6edb6e08f4c7c32d4f71b54bda02913),
    (0x5f07bb9fee6062e9d09a52e6d587c64bad6ba706, 0xfde4c96c8593536e31f229ea8f37b2ada2699bb2),
    (0x5f433f47db8ac2a90d50e3cafb29a7039d5828d4, 0xd2a530170d71a9cfe1651fb468e2b98f7ed7456b),
    (0x5f433f47db8ac2a90d50e3cafb29a7039d5828d4, 0x833589fcd6edb6e08f4c7c32d4f71b54bda02913),
    (0x64cfc444f30056302d4bfd20fd79783aa4b3fda3, 0x000000000d564d5be76f7f0d28fe52605afc7cf8),
    (0x64cfc444f30056302d4bfd20fd79783aa4b3fda3, 0x4200000000000000000000000000000000000006),
    (0xa0eed82e20cab62a5698e898e9fd4c4db412ee7e, 0xdd468a1ddc392dcdbef6db6e34e89aa338f9f186),
    (0xa0eed82e20cab62a5698e898e9fd4c4db412ee7e, 0x833589fcd6edb6e08f4c7c32d4f71b54bda02913),
    (0xb367b4c74f5316a04cf7b12f2020d00c84779acd, 0x1217bfe6c773eec6cc4a38b5dc45b92292b6e189),
    (0xb367b4c74f5316a04cf7b12f2020d00c84779acd, 0x833589fcd6edb6e08f4c7c32d4f71b54bda02913),
    (0xbd59a718e60bd868123c6e949c9fd97185efbdb7, 0x4200000000000000000000000000000000000006),
    (0xbd59a718e60bd868123c6e949c9fd97185efbdb7, 0xc1cba3fcea344f92d9239c08c0568f6f2f0ee452),
    (0xf0c559af52bce48b3f3710604a59b4feaefd5555, 0x60a3e35cc302bfa44cb288bc5a4f316fdb1adb42),
    (0xf0c559af52bce48b3f3710604a59b4feaefd5555, 0xfde4c96c8593536e31f229ea8f37b2ada2699bb2)
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
    AND t.block_date BETWEEN DATE '2025-11-27' AND DATE '2026-07-25'
    AND t.block_time > from_unixtime(1764223199)
    AND t.block_time <= from_unixtime(1784959199)
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
