-- v1 pancakeswap_infinity_events
WITH pools AS (
  SELECT * FROM (VALUES
    (0x02a4a0db44682be4d5c4c7809c0c96c4f0464d9b565d9fcdf536b23738277d9f),
    (0x1fe14350e72ed606c8850a16d91d5500c18274b7a19852dacf7c4164f78d820b),
    (0x6763ac78b18c40e59dce6a8acf7c9f9c046555fa2ebc76cf2634afdc5363bd9a),
    (0x9dc5b572d9618d1d82e0f6a6c7c7e9da3ff85b9c86b6308abe14440e0e01061e),
    (0x9ed2b133457a9debb64997f932b15a0a81f61718e8a267a4b36fab7b960d788a),
    (0xffd8807511b4657aaf2777232b53a095f8ba6d24a368f4c1768b1cd1de1944f6)
  ) AS v (pool_id)
),
sigs AS (
  SELECT
    keccak(to_utf8('Initialize(bytes32,address,address,address,uint24,bytes32,uint160,int24)')) AS t_init,
    keccak(to_utf8('ModifyLiquidity(bytes32,address,int24,int24,int256,bytes32)')) AS t_modify,
    keccak(to_utf8('Swap(bytes32,address,int128,int128,uint160,uint128,int24,uint24,uint16)')) AS t_swap
),
ev AS (
  SELECT
    l.topic0,
    l.topic1 AS pool_id,
    l.topic2,
    l.topic3,
    l.data,
    l.block_number,
    CAST(l."index" AS BIGINT) AS log_index,
    DATE(l.block_time - INTERVAL '6' HOUR) AS local_date
  FROM base.logs l
  CROSS JOIN sigs s
  WHERE l.contract_address = 0xa0ffb9c1ce1fe56963b0321b32e7a0302114058b
    AND l.block_date BETWEEN DATE '2025-05-22' AND DATE '2026-07-25'
    AND l.block_number >= 30544106
    AND l.block_time <= from_unixtime(1784959199)
    AND l.topic0 IN (s.t_init, s.t_modify, s.t_swap)
    AND l.topic1 IN (SELECT pool_id FROM pools)
),
inits AS (
  SELECT
    CAST('init' AS VARCHAR) AS event, e.pool_id, e.block_number, e.log_index, e.local_date,
    bytearray_substring(e.topic2, 13, 20) AS currency0,
    bytearray_substring(e.topic3, 13, 20) AS currency1,
    CAST(NULL AS VARCHAR) AS tick_lower,
    CAST(NULL AS VARCHAR) AS tick_upper,
    CAST(NULL AS VARCHAR) AS liquidity_delta,
    CAST(bytearray_to_uint256(bytearray_substring(e.data, 97, 32)) AS VARCHAR) AS sqrt_price_x96,
    CAST(bytearray_to_int256(bytearray_substring(e.data, 129, 32)) AS VARCHAR) AS tick,
    CAST(NULL AS VARCHAR) AS liquidity,
    CAST(1 AS BIGINT) AS n_events
  FROM ev e CROSS JOIN sigs s
  WHERE e.topic0 = s.t_init
),
modifies AS (
  SELECT
    CAST('modify' AS VARCHAR) AS event, e.pool_id, e.block_number, e.log_index, e.local_date,
    CAST(NULL AS VARBINARY) AS currency0,
    CAST(NULL AS VARBINARY) AS currency1,
    CAST(bytearray_to_int256(bytearray_substring(e.data, 1, 32)) AS VARCHAR) AS tick_lower,
    CAST(bytearray_to_int256(bytearray_substring(e.data, 33, 32)) AS VARCHAR) AS tick_upper,
    CAST(bytearray_to_int256(bytearray_substring(e.data, 65, 32)) AS VARCHAR) AS liquidity_delta,
    CAST(NULL AS VARCHAR) AS sqrt_price_x96,
    CAST(NULL AS VARCHAR) AS tick,
    CAST(NULL AS VARCHAR) AS liquidity,
    CAST(1 AS BIGINT) AS n_events
  FROM ev e CROSS JOIN sigs s
  WHERE e.topic0 = s.t_modify
),
swaps AS (
  SELECT e.pool_id, e.local_date, e.block_number, e.log_index, e.data,
         e.block_number * 100000 + e.log_index AS k
  FROM ev e CROSS JOIN sigs s
  WHERE e.topic0 = s.t_swap
),
last_swaps AS (
  SELECT
    pool_id,
    local_date,
    max_by(block_number, k) AS block_number,
    max_by(log_index, k) AS log_index,
    max_by(data, k) AS data,
    COUNT(*) AS n_events
  FROM swaps
  GROUP BY pool_id, local_date
),
prices AS (
  SELECT
    CAST('price' AS VARCHAR) AS event, pool_id, block_number, log_index, local_date,
    CAST(NULL AS VARBINARY) AS currency0,
    CAST(NULL AS VARBINARY) AS currency1,
    CAST(NULL AS VARCHAR) AS tick_lower,
    CAST(NULL AS VARCHAR) AS tick_upper,
    CAST(NULL AS VARCHAR) AS liquidity_delta,
    CAST(bytearray_to_uint256(bytearray_substring(data, 65, 32)) AS VARCHAR) AS sqrt_price_x96,
    CAST(bytearray_to_int256(bytearray_substring(data, 129, 32)) AS VARCHAR) AS tick,
    CAST(bytearray_to_uint256(bytearray_substring(data, 97, 32)) AS VARCHAR) AS liquidity,
    n_events
  FROM last_swaps
)
SELECT * FROM inits
UNION ALL
SELECT * FROM modifies
UNION ALL
SELECT * FROM prices
ORDER BY pool_id, block_number, log_index
