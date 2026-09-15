-- v1 40acres_venft_events
WITH sigs AS (
  SELECT
    keccak(to_utf8('Transfer(address,address,uint256)')) AS t_transfer,
    keccak(to_utf8('Deposit(address,uint256,uint8,uint256,uint256,uint256)')) AS t_deposit,
    keccak(to_utf8('Withdraw(address,uint256,uint256,uint256)')) AS t_withdraw,
    keccak(to_utf8('Merge(address,uint256,uint256,uint256,uint256,uint256,uint256,uint256)')) AS t_merge,
    keccak(to_utf8('Split(uint256,uint256,uint256,address,uint256,uint256,uint256,uint256)')) AS t_split,
    keccak(to_utf8('DepositManaged(address,uint256,uint256,uint256,uint256)')) AS t_dm,
    keccak(to_utf8('WithdrawManaged(address,uint256,uint256,uint256,uint256)')) AS t_wm
),
loans AS (
  SELECT * FROM (VALUES
    ('optimism', 0xf132bd888897254521d13e2c401e109caaba06a7),
    ('base', 0x87f18b377e625b62c708d5f6ea96ec193558efd0)
  ) AS v (blockchain, loan)
),
esc AS (
  SELECT
    'optimism' AS blockchain, l.block_number, CAST(l."index" AS BIGINT) AS log_index,
    DATE(l.block_time - INTERVAL '6' HOUR) AS local_date,
    l.topic0, l.topic1, l.topic2, l.topic3, l.data
  FROM optimism.logs l
  CROSS JOIN sigs s
  WHERE l.contract_address = 0xfaf8fd17d9840595845582fcb047df13f006787d
    AND l.block_date BETWEEN DATE '2023-06-22' AND DATE '2026-02-27'
    AND l.block_time <= from_unixtime(1772171999)
    AND l.topic0 IN (s.t_transfer, s.t_deposit, s.t_withdraw, s.t_merge, s.t_split, s.t_dm, s.t_wm)
  UNION ALL
  SELECT
    'base' AS blockchain, l.block_number, CAST(l."index" AS BIGINT) AS log_index,
    DATE(l.block_time - INTERVAL '6' HOUR) AS local_date,
    l.topic0, l.topic1, l.topic2, l.topic3, l.data
  FROM base.logs l
  CROSS JOIN sigs s
  WHERE l.contract_address = 0xebf418fe2512e7e6bd9b87a8f0f294acdc67e6b4
    AND l.block_date BETWEEN DATE '2023-08-28' AND DATE '2026-02-27'
    AND l.block_time <= from_unixtime(1772171999)
    AND l.topic0 IN (s.t_transfer, s.t_deposit, s.t_withdraw, s.t_merge, s.t_split, s.t_dm, s.t_wm)
),
xfers AS (
  SELECT
    e.blockchain, e.topic3 AS token_topic, e.block_number, e.log_index, e.local_date,
    CAST(CASE WHEN bytearray_substring(e.topic2, 13, 20) = lo.loan THEN 'in' ELSE 'out' END AS VARCHAR) AS kind,
    CAST(NULL AS VARCHAR) AS amount
  FROM esc e
  JOIN loans lo ON lo.blockchain = e.blockchain
  CROSS JOIN sigs s
  WHERE e.topic0 = s.t_transfer
    AND e.topic3 IS NOT NULL
    AND (bytearray_substring(e.topic1, 13, 20) = lo.loan OR bytearray_substring(e.topic2, 13, 20) = lo.loan)
    AND NOT (bytearray_substring(e.topic1, 13, 20) = lo.loan AND bytearray_substring(e.topic2, 13, 20) = lo.loan)
),
ids AS (
  SELECT DISTINCT blockchain, token_topic FROM xfers
),
locks AS (
  SELECT e.blockchain, e.topic2 AS token_topic, e.block_number, e.log_index, e.local_date,
         CAST('add' AS VARCHAR) AS kind, CAST(bytearray_to_uint256(bytearray_substring(e.data, 1, 32)) AS VARCHAR) AS amount
  FROM esc e CROSS JOIN sigs s WHERE e.topic0 = s.t_deposit
  UNION ALL
  SELECT e.blockchain, e.topic2 AS token_topic, e.block_number, e.log_index, e.local_date,
         CAST('zero' AS VARCHAR) AS kind, CAST(NULL AS VARCHAR) AS amount
  FROM esc e CROSS JOIN sigs s WHERE e.topic0 = s.t_withdraw
  UNION ALL
  SELECT e.blockchain, e.topic2 AS token_topic, e.block_number, e.log_index, e.local_date,
         CAST('zero' AS VARCHAR) AS kind, CAST(NULL AS VARCHAR) AS amount
  FROM esc e CROSS JOIN sigs s WHERE e.topic0 = s.t_merge
  UNION ALL
  SELECT e.blockchain, e.topic3 AS token_topic, e.block_number, e.log_index, e.local_date,
         CAST('set' AS VARCHAR) AS kind, CAST(bytearray_to_uint256(bytearray_substring(e.data, 65, 32)) AS VARCHAR) AS amount
  FROM esc e CROSS JOIN sigs s WHERE e.topic0 = s.t_merge
  UNION ALL
  SELECT e.blockchain, e.topic1 AS token_topic, e.block_number, e.log_index, e.local_date,
         CAST('zero' AS VARCHAR) AS kind, CAST(NULL AS VARCHAR) AS amount
  FROM esc e CROSS JOIN sigs s WHERE e.topic0 = s.t_split
  UNION ALL
  SELECT e.blockchain, e.topic2 AS token_topic, e.block_number, e.log_index, e.local_date,
         CAST('set' AS VARCHAR) AS kind, CAST(bytearray_to_uint256(bytearray_substring(e.data, 33, 32)) AS VARCHAR) AS amount
  FROM esc e CROSS JOIN sigs s WHERE e.topic0 = s.t_split
  UNION ALL
  SELECT e.blockchain, e.topic3 AS token_topic, e.block_number, e.log_index, e.local_date,
         CAST('set' AS VARCHAR) AS kind, CAST(bytearray_to_uint256(bytearray_substring(e.data, 65, 32)) AS VARCHAR) AS amount
  FROM esc e CROSS JOIN sigs s WHERE e.topic0 = s.t_split
  UNION ALL
  SELECT e.blockchain, e.topic2 AS token_topic, e.block_number, e.log_index, e.local_date,
         CAST('zero' AS VARCHAR) AS kind, CAST(NULL AS VARCHAR) AS amount
  FROM esc e CROSS JOIN sigs s WHERE e.topic0 = s.t_dm
  UNION ALL
  SELECT e.blockchain, e.topic2 AS token_topic, e.block_number, e.log_index, e.local_date,
         CAST('set' AS VARCHAR) AS kind, CAST(bytearray_to_uint256(bytearray_substring(e.data, 1, 32)) AS VARCHAR) AS amount
  FROM esc e CROSS JOIN sigs s WHERE e.topic0 = s.t_wm
),
rows_out AS (
  SELECT * FROM xfers
  UNION ALL
  SELECT k.* FROM locks k
  JOIN ids i ON i.blockchain = k.blockchain AND i.token_topic = k.token_topic
)
SELECT
  blockchain,
  CAST(bytearray_to_uint256(token_topic) AS VARCHAR) AS token_id,
  block_number,
  log_index,
  local_date,
  kind,
  amount
FROM rows_out
ORDER BY blockchain, block_number, log_index, kind
