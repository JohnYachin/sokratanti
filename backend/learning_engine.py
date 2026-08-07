"""
CAIOS Learning Engine — Architecture Section 7
Evaluates past signals at 1h/4h/24h/72h/168h windows.
Updates agent weights based on historical accuracy.
Runs hourly via systemd timer.
"""
import asyncio, json, logging
from datetime import datetime, timezone, timedelta

import httpx
from supabase import create_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("caios.learning")

SB_URL = "https://zrvsuwdlhnnfvqxxohex.supabase.co"
SB_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InpydnN1d2RsaG5uZnZxeHhvaGV4Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc4NTUxMTQwMCwiZXhwIjoyMTAxMDg3NDAwfQ.19YNUSRWeJknVytkfQjvnzsjT0LmvqkWUX0eRRDSGJY"

EVAL_WINDOWS = [1, 4, 24, 72, 168]   # hours: 1h 4h 1d 3d 7d

# Agent weight tiers based on 24h accuracy
WEIGHT_TIERS = [
    (0.75, 2.0),   # >= 75% → weight 2.0
    (0.60, 1.5),   # >= 60% → weight 1.5
    (0.45, 1.0),   # >= 45% → weight 1.0 (baseline)
    (0.00, 0.5),   # <  45% → weight 0.5
]


async def fetch_current_prices(coin_ids: list[str]) -> dict:
    """Fetch current prices from CoinGecko for given coingecko_ids."""
    if not coin_ids:
        return {}
    ids_str = ",".join(coin_ids)
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.get(
                "https://api.coingecko.com/api/v3/coins/markets",
                params={"vs_currency": "usd", "ids": ids_str, "order": "market_cap_desc"}
            )
        return {d["id"]: d["current_price"] for d in r.json()}
    except Exception as e:
        log.error(f"Price fetch error: {e}")
        return {}


def get_signal_direction(signal: str) -> str:
    """Normalize signal to BUY / SELL / HOLD."""
    if signal in ("BUY", "STRONG_BUY"):
        return "BUY"
    if signal in ("SELL", "STRONG_SELL"):
        return "SELL"
    return "HOLD"


def is_correct(direction: str, price_at_signal: float, price_now: float) -> bool | None:
    """Return True/False for BUY/SELL, None for HOLD."""
    if direction == "BUY":
        return price_now > price_at_signal
    if direction == "SELL":
        return price_now < price_at_signal
    return None


async def evaluate_signals(sb) -> int:
    """
    Find signals due for evaluation and record results.
    Returns number of evaluations performed.
    """
    now = datetime.now(timezone.utc)
    total_evals = 0

    # Load all signals that have a price_at_signal
    signals = sb.table("signals").select(
        "id,coin_id,signal,price_at_signal,created_at,coins(coingecko_id,symbol)"
    ).not_.is_("price_at_signal", "null").execute().data

    if not signals:
        log.info("No signals to evaluate")
        return 0

    # Find already-evaluated (signal_id, window) pairs
    existing = sb.table("prediction_evaluations").select(
        "signal_id,window_hours"
    ).execute().data
    evaluated_pairs = {(e["signal_id"], e["window_hours"]) for e in existing}

    # Collect coins we need prices for
    coin_cgids = list({s["coins"]["coingecko_id"] for s in signals if s.get("coins")})
    prices = await fetch_current_prices(coin_cgids)

    for sig in signals:
        if not sig.get("coins"):
            continue
        cg_id      = sig["coins"]["coingecko_id"]
        sym        = sig["coins"]["symbol"]
        created_at = datetime.fromisoformat(sig["created_at"].replace("Z", "+00:00"))
        entry_price = float(sig["price_at_signal"])
        current_price = prices.get(cg_id)
        direction   = get_signal_direction(sig["signal"])

        if not current_price:
            continue

        for window_h in EVAL_WINDOWS:
            key = (sig["id"], window_h)
            if key in evaluated_pairs:
                continue  # Already done

            due_at = created_at + timedelta(hours=window_h)
            if now < due_at:
                continue  # Not due yet

            pct_change = (current_price - entry_price) / entry_price * 100
            correct    = is_correct(direction, entry_price, current_price)

            try:
                sb.table("prediction_evaluations").insert({
                    "signal_id":        sig["id"],
                    "coin_id":          sig["coin_id"],
                    "window_hours":     window_h,
                    "price_at_signal":  entry_price,
                    "price_at_eval":    current_price,
                    "price_change_pct": round(pct_change, 4),
                    "signal_direction": direction,
                    "is_correct":       correct,
                }).execute()
                icon = "✅" if correct else ("⬜" if correct is None else "❌")
                log.info(f"{icon} {sym} {window_h}h | {direction} @ ${entry_price:,.2f} → ${current_price:,.2f} ({pct_change:+.2f}%)")
                total_evals += 1
            except Exception as e:
                if "duplicate" in str(e).lower() or "unique" in str(e).lower():
                    pass  # Race condition - ok
                else:
                    log.error(f"Eval insert error: {e}")

    return total_evals


async def update_agent_weights(sb) -> int:
    """
    Recalculate per-agent accuracy and update weights.
    Uses 24h window as primary metric.
    Returns number of agents updated.
    """
    agents = sb.table("agents").select("id,name,weight").execute().data
    if not agents:
        return 0

    updated = 0
    for agent in agents:
        agent_id = agent["id"]
        agent_name = agent["name"]

        for window_h in EVAL_WINDOWS:
            # Find agent_executions joined with prediction_evaluations
            try:
                execs = sb.table("agent_executions").select(
                    "id,signal,confidence,cycle_id,coin_id"
                ).eq("agent_id", agent_id).eq("status", "success").execute().data

                if not execs:
                    continue

                # Get cycle → signal mapping
                cycle_ids = list({e["cycle_id"] for e in execs if e.get("cycle_id")})
                if not cycle_ids:
                    continue

                # For each execution, find the matching prediction_evaluation
                total = correct = 0
                total_conf = 0.0

                for ex in execs:
                    coin_id    = ex.get("coin_id")
                    cycle_id   = ex.get("cycle_id")
                    direction  = get_signal_direction(ex["signal"])
                    conf       = float(ex.get("confidence") or 0)

                    if direction == "HOLD":
                        continue

                    # Find evaluation for this coin/cycle
                    evals = sb.table("prediction_evaluations").select(
                        "is_correct,signal_id"
                    ).eq("coin_id", coin_id).eq("window_hours", window_h).execute().data

                    if not evals:
                        continue

                    for ev in evals:
                        if ev["is_correct"] is None:
                            continue
                        total += 1
                        total_conf += conf
                        if ev["is_correct"]:
                            correct += 1

                if total < 3:
                    continue  # Not enough data

                accuracy = correct / total
                avg_conf = total_conf / total

                # Upsert agent_accuracy
                sb.table("agent_accuracy").upsert({
                    "agent_id":        agent_id,
                    "window_hours":    window_h,
                    "total_signals":   total,
                    "correct_signals": correct,
                    "accuracy_pct":    round(accuracy * 100, 2),
                    "avg_confidence":  round(avg_conf, 3),
                    "updated_at":      datetime.now(timezone.utc).isoformat(),
                }, on_conflict="agent_id,window_hours").execute()

                # Update agent weight based on 24h accuracy
                if window_h == 24:
                    new_weight = 1.0  # default
                    for threshold, weight in WEIGHT_TIERS:
                        if accuracy >= threshold:
                            new_weight = weight
                            break

                    if abs(new_weight - float(agent.get("weight") or 1.0)) > 0.05:
                        sb.table("agents").update({"weight": new_weight}).eq("id", agent_id).execute()
                        log.info(f"⚖️  {agent_name}: accuracy={accuracy*100:.1f}% → weight {agent.get('weight',1.0):.1f} → {new_weight:.1f}")
                        updated += 1

            except Exception as e:
                log.error(f"Agent {agent_name} accuracy error: {e}")

    return updated


async def print_summary(sb):
    """Print current learning engine summary."""
    try:
        total_evals = sb.table("prediction_evaluations").select("id", count="exact").execute().count
        acc_rows = sb.table("agent_accuracy").select(
            "accuracy_pct,agents(name)"
        ).eq("window_hours", 24).order("accuracy_pct", desc=True).execute().data

        print(f"\n{'='*55}")
        print(f"📊 CAIOS Learning Engine — Summary")
        print(f"{'='*55}")
        print(f"   Total evaluations stored: {total_evals}")
        if acc_rows:
            print(f"\n   🎯 Agent Accuracy (24h window):")
            for r in acc_rows[:10]:
                name = r.get("agents", {}).get("name", "?")[:30] if r.get("agents") else "?"
                acc  = r["accuracy_pct"]
                icon = "🟢" if acc >= 60 else "🟡" if acc >= 45 else "🔴"
                print(f"   {icon} {name:<30} {acc:.1f}%")
        print(f"{'='*55}")
    except Exception as e:
        log.error(f"Summary error: {e}")


async def main():
    sb = create_client(SB_URL, SB_KEY)
    log.info("✅ Learning Engine started")

    # 1. Evaluate pending signals
    log.info("📊 Evaluating signals...")
    n_evals = await evaluate_signals(sb)
    log.info(f"✅ {n_evals} evaluations recorded")

    # 2. Update agent weights
    log.info("⚖️  Updating agent weights...")
    n_updated = await update_agent_weights(sb)
    log.info(f"✅ {n_updated} agents updated")

    # 3. Print summary
    await print_summary(sb)
    log.info("Learning Engine run complete.")


if __name__ == "__main__":
    asyncio.run(main())
