-- ============================================================
-- Sentient Trader: AI Behavior Master Report
-- Adjust only these dates.
-- ============================================================
drop view if exists pg_temp.ai_behavior_base;

create temp view ai_behavior_base as
select
  t.id,
  t.ticker,
  t.created_at,
  t.created_at at time zone 'America/New_York' as created_at_et,
  case
    when (t.created_at at time zone 'America/New_York')::time < time '09:30' then 'premarket'
    when (t.created_at at time zone 'America/New_York')::time <= time '16:00' then 'regular_market'
    else 'after_hours'
  end as market_session,
  coalesce(t.decision_path, 'unknown') as decision_path,
  coalesce(t.pm_recommendation, t.trade_action) as pm_recommendation,
  t.trade_action,
  t.sentiment_score,
  t.confidence_score as raw_confidence,
  t.calibrated_confidence,
  t.confidence_cap,
  t.risk_should_trade,
  t.executed_action,
  t.order_id,
  t.gate_reason,
  coalesce(so.label_status, 'MISSING') as label_status,
  so.return_15m,
  so.return_1h,
  so.return_eod,
  so.label_error,
  case
    when coalesce(t.pm_recommendation, t.trade_action) = 'BUY' then so.return_15m
    when coalesce(t.pm_recommendation, t.trade_action) = 'SELL' then - so.return_15m
  end as directional_return_15m,
  case
    when coalesce(t.pm_recommendation, t.trade_action) = 'BUY' then so.return_1h
    when coalesce(t.pm_recommendation, t.trade_action) = 'SELL' then - so.return_1h
  end as directional_return_1h,
  case
    when coalesce(t.pm_recommendation, t.trade_action) = 'BUY' then so.return_eod
    when coalesce(t.pm_recommendation, t.trade_action) = 'SELL' then - so.return_eod
  end as directional_return_eod
from
  sentient_trader.trades t
  left join sentient_trader.signal_outcomes so on so.trade_id = t.id
where
  t.created_at >= timestamp with time zone '2026-05-26 00:00:00+00'
  and t.created_at < timestamp with time zone '2026-05-27 00:00:00+00';

-- ============================================================
-- 1. System Shape: how much AI vs pre-screen vs execution?
-- ============================================================
select
  count(*) as total_signals,
  count(*) filter (
    where
      decision_path = 'pre_screen'
  ) as pre_screened,
  count(*) filter (
    where
      decision_path = 'full_debate'
  ) as full_debates,
  count(*) filter (
    where
      decision_path = 'expired'
  ) as expired,
  count(*) filter (
    where
      pm_recommendation = 'BUY'
  ) as buy_recommendations,
  count(*) filter (
    where
      pm_recommendation = 'SELL'
  ) as sell_recommendations,
  count(*) filter (
    where
      pm_recommendation = 'HOLD'
  ) as hold_recommendations,
  count(*) filter (
    where
      executed_action is not null
  ) as executed_orders,
  count(*) filter (
    where
      pm_recommendation in ('BUY', 'SELL')
      and executed_action is null
  ) as blocked_directional_recommendations
from
  ai_behavior_base;

-- ============================================================
-- 2. Recommendation Mix by Path
-- ============================================================
select
  decision_path,
  pm_recommendation,
  count(*) as count,
  round(
    100.0 * count(*) / nullif(
      sum(count(*)) over (
        partition by
          decision_path
      ),
      0
    ),
    2
  ) as pct_within_path
from
  ai_behavior_base
group by
  decision_path,
  pm_recommendation
order by
  decision_path,
  count desc;

-- ============================================================
-- 3. Label Coverage
-- ============================================================
select
  label_status,
  market_session,
  count(*) as count
from
  ai_behavior_base
group by
  label_status,
  market_session
order by
  label_status,
  market_session;

-- ============================================================
-- 4. Blocked BUY/SELL Performance
-- directional_return means:
-- BUY: stock went up = good
-- SELL: stock went down = good
-- ============================================================
select
  pm_recommendation,
  count(*) as total_blocked_directional,
  count(*) filter (
    where
      label_status = 'LABELED'
  ) as labeled_count,
  avg(directional_return_15m) as avg_directional_return_15m,
  avg(
    case
      when directional_return_15m > 0 then 1.0
      when directional_return_15m is not null then 0.0
    end
  ) as win_rate_15m,
  avg(directional_return_1h) as avg_directional_return_1h,
  avg(
    case
      when directional_return_1h > 0 then 1.0
      when directional_return_1h is not null then 0.0
    end
  ) as win_rate_1h,
  avg(directional_return_eod) as avg_directional_return_eod,
  avg(
    case
      when directional_return_eod > 0 then 1.0
      when directional_return_eod is not null then 0.0
    end
  ) as win_rate_eod
from
  ai_behavior_base
where
  pm_recommendation in ('BUY', 'SELL')
  and executed_action is null
group by
  pm_recommendation
order by
  pm_recommendation;

-- ============================================================
-- 5. Confidence Bucket Performance
-- Shows whether higher calibrated confidence is actually better.
-- ============================================================
select
  case
    when calibrated_confidence is null then 'unknown'
    when calibrated_confidence < 0.40 then '0.00-0.39'
    when calibrated_confidence < 0.60 then '0.40-0.59'
    when calibrated_confidence < 0.80 then '0.60-0.79'
    else '0.80+'
  end as calibrated_confidence_bucket,
  pm_recommendation,
  count(*) as count,
  avg(directional_return_15m) as avg_directional_return_15m,
  avg(
    case
      when directional_return_15m > 0 then 1.0
      when directional_return_15m is not null then 0.0
    end
  ) as win_rate_15m,
  avg(directional_return_eod) as avg_directional_return_eod,
  avg(
    case
      when directional_return_eod > 0 then 1.0
      when directional_return_eod is not null then 0.0
    end
  ) as win_rate_eod
from
  ai_behavior_base
where
  pm_recommendation in ('BUY', 'SELL')
group by
  1,
  2
order by
  1,
  2;

-- ============================================================
-- 6. Why Trades Were Blocked
-- ============================================================
select
  block_reason,
  count(*) as count
from
  (
    select
      case
        when gate_reason ilike '%No long position%' then 'no_long_position_for_sell'
        when gate_reason ilike '%Calibrated confidence%' then 'calibrated_confidence_too_low'
        when gate_reason ilike '%Directional sentiment%' then 'directional_sentiment_too_weak'
        when gate_reason ilike '%Article quality%' then 'article_quality_too_weak'
        when gate_reason ilike '%Simulated%' then 'simulated_signal'
        when gate_reason is null then 'no_gate_reason'
        else 'other'
      end as block_reason
    from
      ai_behavior_base
    where
      pm_recommendation in ('BUY', 'SELL')
      and executed_action is null
  ) x
group by
  block_reason
order by
  count desc;

-- ============================================================
-- 7. Why Outcomes Are Missing / No Bars
-- ============================================================
select
  label_status,
  market_session,
  coalesce(label_error, 'none') as label_error,
  count(*) as count
from
  ai_behavior_base
where
  label_status in ('MISSING', 'NO_BARS', 'PARTIAL')
group by
  label_status,
  market_session,
  coalesce(label_error, 'none')
order by
  count desc;

-- ============================================================
-- 8. Actual Executed Orders
-- ============================================================
select
  ticker,
  created_at,
  pm_recommendation,
  calibrated_confidence,
  risk_should_trade,
  executed_action,
  order_id,
  return_15m,
  return_1h,
  return_eod,
  label_status
from
  ai_behavior_base
where
  executed_action is not null
order by
  created_at desc;

-- ============================================================
-- 9. Best/Worst Blocked Directional Calls
-- Positive directional_return = AI direction was right.
-- ============================================================
select
  ticker,
  created_at,
  market_session,
  pm_recommendation,
  calibrated_confidence,
  gate_reason,
  label_status,
  directional_return_15m,
  directional_return_1h,
  directional_return_eod,
  label_error
from
  ai_behavior_base
where
  pm_recommendation in ('BUY', 'SELL')
  and executed_action is null
order by
  directional_return_eod desc nulls last
limit
  50;

-- ============================================================
-- 10. Scheduler Health
-- ============================================================
select
  scheduler_name,
  status,
  count(*) as runs,
  max(started_at) as last_started_at,
  max(finished_at) as last_finished_at,
  avg(duration_ms) as avg_duration_ms,
  sum(rows_processed) as total_rows_processed,
  max(error_message) filter (
    where
      status = 'ERROR'
  ) as latest_error
from
  sentient_trader.scheduler_runs
where
  started_at >= timestamp with time zone '2026-05-26 00:00:00+00'
  and started_at < timestamp with time zone '2026-05-27 00:00:00+00'
group by
  scheduler_name,
  status
order by
  scheduler_name,
  status;