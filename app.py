"""
FastAPI Web Application & REST Endpoints for Greyhound Betting Engine.
Provides interactive controls to fetch data, trigger predictions, inspect races, and build multis.
"""

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone, timedelta
import sqlite3
import re
from bs4 import BeautifulSoup

from contextlib import asynccontextmanager
from db import get_connection, init_db
from thedogs_scraper import TheDogsScraper
from fasttrack_scraper import FastTrackScraper
from weather_collector import WeatherCollector
from analytics import GreyhoundAnalytics
from multi_optimizer import MultiBetOptimizer
from poller_daemon import daemon_instance
from results_daemon import results_daemon_instance
from dog_hydration_daemon import DogHydrationDaemon, DogHydrationWorker
from daily_learning_engine import DailyLearningEngine

hydration_daemon_instance = DogHydrationDaemon(interval_secs=300, batch_size=40)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Start automated background polling daemon, results auto-reconciliation daemon, and dog hydration daemon
    daemon_instance.start()
    results_daemon_instance.start()
    hydration_daemon_instance.start()
    yield
    # Stop background daemons on shutdown
    daemon_instance.stop()
    results_daemon_instance.stop()
    hydration_daemon_instance.stop()


app = FastAPI(title="Greyhound Precision Betting Engine", lifespan=lifespan)

BASE_DIR = Path(__file__).parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

from fastapi.responses import FileResponse

@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return FileResponse(STATIC_DIR / "greyhound_logo.png")


# TheDogs publishes all start times in Eastern Daylight Time (AEDT, UTC+11) during Daylight Saving (October-April)
AEDT_TZ = timezone(timedelta(hours=11))

# Global background status tracking
TASKS_STATUS = {
    "thedogs": {"running": False, "message": "Idle"},
    "sportsbet": {"running": False, "message": "Idle"},
    "fasttrack": {"running": False, "message": "Idle"},
    "weather": {"running": False, "message": "Idle"}
}


@app.get("/", response_class=HTMLResponse)
def index_view():
    index_file = TEMPLATES_DIR / "index.html"
    if not index_file.exists():
        return HTMLResponse("<h1>Templates missing</h1>", status_code=500)
    return HTMLResponse(index_file.read_text(encoding="utf-8"))


@app.get("/api/status")
def get_system_status():
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT count(*) FROM meetings")
    meetings_count = c.fetchone()[0]
    c.execute("SELECT count(*) FROM races")
    races_count = c.fetchone()[0]
    c.execute("SELECT count(*) FROM race_runners")
    runners_count = c.fetchone()[0]
    c.execute("SELECT count(*) FROM past_performances")
    pp_count = c.fetchone()[0]
    c.execute("SELECT count(*) FROM weather_records")
    weather_count = c.fetchone()[0]
    conn.close()

    return {
        "stats": {
            "meetings": meetings_count,
            "races": races_count,
            "runners": runners_count,
            "past_performances": pp_count,
            "weather_records": weather_count
        },
        "tasks": TASKS_STATUS
    }


@app.get("/api/races")
def list_races(filter_status: str = "all"):
    conn = get_connection()
    c = conn.cursor()
    query = """
    SELECT r.id, r.race_number, r.race_name, r.distance, r.grade, r.start_time,
           m.track_name, m.meeting_date,
           sum(case when rr.is_scratched = 0 or rr.is_scratched is null then 1 else 0 end) as runners_count,
           sum(case when rr.finish_position is not null then 1 else 0 end) as finished_count,
           (SELECT condition_desc FROM weather_records w WHERE w.track_name LIKE ('%' || m.track_name || '%') ORDER BY id DESC LIMIT 1) as weather_desc,
           (SELECT temperature FROM weather_records w WHERE w.track_name LIKE ('%' || m.track_name || '%') ORDER BY id DESC LIMIT 1) as weather_temp
    FROM races r
    JOIN meetings m ON r.meeting_id = m.id
    LEFT JOIN race_runners rr ON rr.race_id = r.id
    WHERE m.track_name != 'Form'
    GROUP BY r.id
    """
    c.execute(query)
    rows = c.fetchall()
    conn.close()

    now_aedt = datetime.now(AEDT_TZ)

    items = []
    for row in rows:
        is_finished = (row["finished_count"] or 0) > 0
        start_t = row["start_time"]
        m_date = row["meeting_date"]
        diff_mins = None

        if start_t and ":" in str(start_t):
            try:
                h, m = map(int, str(start_t).split(":"))
                d_parts = list(map(int, str(m_date).split("-")))
                dt_aedt = datetime(d_parts[0], d_parts[1], d_parts[2], h, m, tzinfo=AEDT_TZ)
                # Midnight rollover: races starting in early morning (00:00 to 07:59 AEDT)
                # with race number >= 4 belong to the evening/night session of that meeting date,
                # meaning their actual jump time is on the following calendar day in AEDT.
                if h < 8 and (row["race_number"] or 1) >= 4:
                    dt_aedt += timedelta(days=1)
                diff_mins = (dt_aedt - now_aedt).total_seconds() / 60.0
            except Exception:
                diff_mins = None

        is_upcoming = not is_finished and (diff_mins is None or diff_mins >= -25)

        countdown_label = None
        if diff_mins is not None:
            if -3 <= diff_mins <= 1:
                countdown_label = "Jumping Now"
            elif 1 < diff_mins < 60:
                countdown_label = f"in {int(diff_mins)}m"
            elif diff_mins >= 60:
                h_left = int(diff_mins // 60)
                m_left = int(diff_mins % 60)
                countdown_label = f"in {h_left}h {m_left}m"
            elif diff_mins < -3 and is_upcoming:
                countdown_label = "In Run"
            else:
                countdown_label = "Completed"

        if filter_status == "upcoming" and not is_upcoming:
            continue
        if filter_status == "completed" and is_upcoming:
            continue

        items.append({
            "id": row["id"],
            "track": row["track_name"],
            "date": row["meeting_date"],
            "race_number": row["race_number"],
            "race_name": row["race_name"],
            "distance": row["distance"],
            "grade": row["grade"],
            "start_time": row["start_time"] or "TBD",
            "runners_count": row["runners_count"],
            "weather_desc": row["weather_desc"] or "",
            "weather_temp": row["weather_temp"] or "",
            "status": "Upcoming" if is_upcoming else "Completed",
            "diff_mins": round(diff_mins, 1) if diff_mins is not None else None,
            "countdown": countdown_label
        })

    # Sort logic: Next to jump first!
    if filter_status == "upcoming":
        # 1. Races jumping soonest (diff_mins >= -5) ascending: 1m, 3m, 7m, 12m...
        # 2. Followed by races that just jumped (-25 <= diff_mins < -5)
        # 3. Followed by TBD start times
        items.sort(key=lambda x: (
            0 if (x["diff_mins"] is not None and x["diff_mins"] >= -5) else (1 if x["diff_mins"] is not None else 2),
            x["diff_mins"] if (x["diff_mins"] is not None and x["diff_mins"] >= -5) else (
                -x["diff_mins"] if x["diff_mins"] is not None else 9999
            ),
            x["id"]
        ))
    elif filter_status == "completed":
        # Most recently completed first
        items.sort(key=lambda x: (
            0 if x["diff_mins"] is not None else 1,
            -(x["diff_mins"] or 0),
            -x["id"]
        ))
    else:  # 'all'
        # Group upcoming races first (sorted by next to jump), then completed
        items.sort(key=lambda x: (
            0 if x["status"] == "Upcoming" else 1,
            x["diff_mins"] if (x["status"] == "Upcoming" and x["diff_mins"] is not None and x["diff_mins"] >= -5) else (
                9999 if x["status"] == "Upcoming" else (-(x["diff_mins"] or 0))
            ),
            -x["id"]
        ))

    return items


@app.get("/api/races/{race_id}")
def get_race_analysis(race_id: int):
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
    SELECT r.id, r.race_number, r.race_name, r.distance, r.grade, r.start_time, r.expert_tips,
           m.track_name, m.meeting_date
    FROM races r
    JOIN meetings m ON r.meeting_id = m.id
    WHERE r.id = ?
    """, (race_id,))
    race = c.fetchone()
    if not race:
        conn.close()
        raise HTTPException(status_code=404, detail="Race not found")

    # Ensure fresh weather observation for this track (fetches if missing or > 25 mins old)
    try:
        from weather_collector import WeatherCollector
        wc = WeatherCollector()
        wc.ensure_track_weather(race["track_name"], max_age_minutes=25)
    except Exception:
        pass

    c.execute("""
    SELECT temperature, humidity, precipitation, wind_speed, condition_desc, recorded_at
    FROM weather_records
    WHERE track_name LIKE ? OR ? LIKE ('%' || track_name || '%')
    ORDER BY id DESC LIMIT 1
    """, (f"%{race['track_name']}%", race["track_name"]))
    weather = c.fetchone()

    c.execute("""
    SELECT rr.box_number, rr.rug_number, g.dog_name
    FROM race_runners rr
    LEFT JOIN greyhounds g ON rr.dog_id = g.id
    WHERE rr.race_id = ? AND rr.is_scratched = 1
    ORDER BY COALESCE(rr.box_number, rr.rug_number, 99) ASC
    """, (race_id,))
    scratched_rows = c.fetchall()
    scratched_runners = [{"box": r["box_number"] or r["rug_number"], "name": r["dog_name"]} for r in scratched_rows]

    conn.close()

    analytics = GreyhoundAnalytics()
    predictions = analytics.predict_race_probabilities(race_id)

    # Extract 5th dog / Value Roughie
    value_roughie = next((p for p in predictions if p.get("is_roughie")), None)
    top4_boxes = [p["box"] for p in predictions[:4]]
    top5_boxes = list(top4_boxes)
    if value_roughie and value_roughie["box"] not in top4_boxes:
        top5_boxes.append(value_roughie["box"])
    elif len(predictions) >= 5:
        top5_boxes.append(predictions[4]["box"])

    raw_tips = race["expert_tips"] if race and "expert_tips" in race.keys() else None
    parsed_tips = [int(x.strip()) for x in raw_tips.split(",") if x.strip().isdigit()] if raw_tips else None

    # Dynamic track calibration profile
    from track_calibration_engine import TrackCalibrationEngine
    track_calib = None
    try:
        t_engine = TrackCalibrationEngine()
        track_calib = t_engine.get_track_calibration(race["track_name"], race["distance"] or 400)
    except Exception as e:
        logger.warning(f"Failed to fetch track calibration for {race.get('track_name')}: {e}")

    # Estimate 5-Dog Boxed First 4 & 4-Dog Boxed Trifecta Odds & Staking Payouts (Default 5% Flexi)
    from exotic_bet_optimizer import ExoticBetOptimizer
    f4_odds_estimate = None
    tri_odds_estimate = None
    try:
        opt = ExoticBetOptimizer()
        f4_odds_estimate = opt.estimate_boxed_first4_odds(race_id, selected_boxes=top5_boxes, custom_stake=6.0)
    except Exception as e:
        logger.warning(f"Failed to estimate First 4 odds for race {race_id}: {e}")

    try:
        opt = ExoticBetOptimizer()
        tri_odds_estimate = opt.estimate_boxed_trifecta_odds(race_id, selected_boxes=top4_boxes, custom_stake=1.20)
    except Exception as e:
        logger.warning(f"Failed to estimate Trifecta odds for race {race_id}: {e}")

    return {
        "race": {
            "id": race["id"],
            "track": race["track_name"],
            "date": race["meeting_date"],
            "race_number": race["race_number"],
            "race_name": race["race_name"],
            "distance": race["distance"],
            "grade": race["grade"],
            "start_time": race["start_time"] or "TBD",
            "expert_tips": parsed_tips
        },
        "weather": dict(weather) if weather else None,
        "scratched": scratched_runners,
        "predictions": predictions,
        "value_roughie": value_roughie,
        "top4_boxes": top4_boxes,
        "top5_boxed": top5_boxes,
        "active_runners_count": len(predictions),
        "places_paid": 3 if len(predictions) >= 8 else (2 if len(predictions) >= 5 else 1),
        "no_third_place_dividend": len(predictions) < 8,
        "place_terms_label": "Paying 2 Places (No 3rd Place Dividend)" if (5 <= len(predictions) <= 7) else ("Win Only (No Place Dividends)" if len(predictions) < 5 else "Paying 3 Places (Standard 8-Runner Field)"),
        "boxed_first4_estimate": f4_odds_estimate,
        "boxed_trifecta_estimate": tri_odds_estimate,
        "track_calibration": track_calib
    }


@app.get("/api/races/{race_id}/estimate-first4")
def estimate_first4_odds_api(
    race_id: int,
    stake: float = 6.0,
    boxes: Optional[str] = None,
    structure: str = "boxed",
    anchor: Optional[int] = None,
    secondary_anchor: Optional[int] = None
):
    """
    Returns live projected tote dividends, payouts, break-even targets,
    and expected value for a First 4 ticket with Combinatorial Selective Boxing.
    """
    from exotic_bet_optimizer import ExoticBetOptimizer
    try:
        optimizer = ExoticBetOptimizer()
        selected_boxes = [int(b.strip()) for b in boxes.split(",") if b.strip().isdigit()] if boxes else None
        return optimizer.estimate_boxed_first4_odds(
            race_id,
            selected_boxes=selected_boxes,
            custom_stake=stake,
            ticket_structure=structure,
            anchor_box=anchor,
            secondary_anchor=secondary_anchor
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/races/{race_id}/estimate-trifecta")
def estimate_trifecta_odds_api(
    race_id: int,
    stake: float = 1.20,
    boxes: Optional[str] = None,
    structure: str = "boxed",
    anchor: Optional[int] = None,
    secondary_anchor: Optional[int] = None
):
    """
    Returns live projected tote dividends, payouts, break-even targets,
    and expected value for a Trifecta ticket with Combinatorial Selective Boxing.
    """
    from exotic_bet_optimizer import ExoticBetOptimizer
    try:
        optimizer = ExoticBetOptimizer()
        selected_boxes = [int(b.strip()) for b in boxes.split(",") if b.strip().isdigit()] if boxes else None
        return optimizer.estimate_boxed_trifecta_odds(
            race_id,
            selected_boxes=selected_boxes,
            custom_stake=stake,
            ticket_structure=structure,
            anchor_box=anchor,
            secondary_anchor=secondary_anchor
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/tracks/calibration-report")
def get_track_calibration_report(track: Optional[str] = None, distance: Optional[int] = None):
    """
    Returns empirical autonomous track calibration profiles, rail biases,
    box multipliers, and lead dominance metrics.
    """
    from track_calibration_engine import TrackCalibrationEngine
    engine = TrackCalibrationEngine()
    if track:
        return engine.get_track_calibration(track, distance or 400)
    return {"calibrations": engine.get_calibration_summary()}


@app.post("/api/tracks/recalibrate")
def trigger_track_recalibration():
    """
    Triggers on-demand autonomous empirical recalculation of all track geometries,
    box strike rates, and split lead dominance factors across historical race data.
    """
    from track_calibration_engine import TrackCalibrationEngine
    engine = TrackCalibrationEngine()
    reports = engine.calibrate_all_tracks()
    return {
        "status": "success",
        "calibrated_count": len(reports),
        "message": f"Successfully recalibrated {len(reports)} track and distance profiles."
    }


@app.get("/api/races/{race_id}/exotics")
def get_race_exotics_api(race_id: int, stake: Optional[float] = None):
    """
    Returns automated First 4, Trifecta, and Dutching tickets for the specified race.
    """
    from exotic_bet_optimizer import ExoticBetOptimizer
    try:
        optimizer = ExoticBetOptimizer()
        return optimizer.generate_race_exotics(race_id, custom_stake=stake)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to generate exotics: {e}")


@app.post("/api/exotics/calculate")
def calculate_custom_exotic(payload: Dict[str, Any]):
    """
    Custom interactive exotic ticket calculator.
    Payload:
    {
        "race_id": int,
        "exotic_type": "first_4" | "trifecta",
        "ticket_style": "boxed" | "standout" | "roving" | "dutching",
        "selected_boxes": [1, 2, 4, 7],
        "banker_box": 1 (for standout/roving),
        "stake": 20.0
    }
    """
    import itertools
    from exotic_bet_optimizer import ExoticBetOptimizer
    from analytics import GreyhoundAnalytics

    race_id = payload.get("race_id")
    exotic_type = payload.get("exotic_type", "first_4").lower()
    ticket_style = payload.get("ticket_style", "boxed").lower()
    selected_boxes = payload.get("selected_boxes", [])
    banker_box = payload.get("banker_box")
    stake = float(payload.get("stake", 20.0))

    if not race_id:
        raise HTTPException(status_code=400, detail="race_id is required")

    analytics = GreyhoundAnalytics()
    preds = analytics.predict_race_probabilities(race_id)
    if not preds:
        raise HTTPException(status_code=404, detail="No predictions found for race")

    win_probs = {r["box"]: r["model_win_prob"] for r in preds}
    runner_names = {r["box"]: r["dog_name"] for r in preds}
    optimizer = ExoticBetOptimizer()

    try:
        if ticket_style == "boxed":
            return optimizer.build_boxed_ticket(
                exotic_type=exotic_type,
                selected_boxes=selected_boxes,
                win_probs=win_probs,
                stake=stake,
                runner_names=runner_names
            )
        elif ticket_style in ("standout", "roving"):
            if not banker_box:
                raise HTTPException(status_code=400, detail="banker_box required for standout/roving ticket")
            contenders = [b for b in selected_boxes if b != banker_box]
            return optimizer.build_standout_ticket(
                exotic_type=exotic_type,
                banker_box=banker_box,
                contender_boxes=contenders,
                win_probs=win_probs,
                stake=stake,
                runner_names=runner_names,
                roving=(ticket_style == "roving")
            )
        elif ticket_style == "key_standout":
            banker_boxes = payload.get("banker_boxes", [])
            if not banker_boxes and banker_box:
                banker_boxes = [banker_box]
            if len(banker_boxes) < 2 and len(selected_boxes) >= 2:
                banker_boxes = selected_boxes[:2]
            contenders = [b for b in selected_boxes if b not in banker_boxes]
            return optimizer.build_key_standout_ticket(
                exotic_type=exotic_type,
                banker_boxes=banker_boxes,
                contender_boxes=contenders,
                win_probs=win_probs,
                stake=stake,
                runner_names=runner_names
            )
        elif ticket_style == "dutching":
            req_len = 3 if exotic_type == "trifecta" else 4
            perms = payload.get("permutations")
            if not perms:
                all_perms = list(itertools.permutations(selected_boxes, req_len))
                perms = all_perms[:8]
            else:
                perms = [tuple(p) for p in perms]
            return optimizer.build_dutching_ticket(
                exotic_type=exotic_type,
                target_permutations=perms,
                win_probs=win_probs,
                total_stake=stake,
                runner_names=runner_names
            )
        else:
            raise HTTPException(status_code=400, detail=f"Unsupported ticket_style: {ticket_style}")
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/races/{race_id}/srm/simulate")
def simulate_race_srm_api(race_id: int, iterations: int = 10000):
    """
    Runs a 10,000-iteration Monte Carlo race simulation for the specified race,
    evaluating runner podium frequencies and discovering top +EV Same Race Multis.
    """
    from monte_carlo_srm import MonteCarloSRMSimulator
    try:
        sim = MonteCarloSRMSimulator(n_iterations=iterations)
        sim_data = sim.simulate_race(race_id)
        optimal_srms = sim.scan_optimal_srms(race_id)
        return {
            "race_id": race_id,
            "track_name": sim_data["track_name"],
            "distance": sim_data["distance"],
            "iterations": sim_data["n_iterations"],
            "runners": sim_data["runners"],
            "optimal_srms": optimal_srms
        }
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Simulation error: {e}")


@app.post("/api/srm/evaluate")
def evaluate_custom_srm_api(payload: Dict[str, Any]):
    """
    Evaluates a custom user-defined Same Race Multi slip using a 10,000-iteration Monte Carlo simulation.
    Payload:
    {
        "race_id": int,
        "conditions": { "1": "win", "4": "top_2", "7": "top_3" },
        "offered_srm_odds": 18.50,
        "iterations": 10000
    }
    """
    from monte_carlo_srm import MonteCarloSRMSimulator
    race_id = payload.get("race_id")
    raw_conds = payload.get("conditions", {})
    offered_odds = payload.get("offered_srm_odds")
    iterations = int(payload.get("iterations", 10000))

    if not race_id or not raw_conds:
        raise HTTPException(status_code=400, detail="race_id and conditions are required")

    try:
        # Convert string keys to int boxes
        conditions = {int(k): str(v) for k, v in raw_conds.items()}
        sim = MonteCarloSRMSimulator(n_iterations=iterations)
        sim_data = sim.simulate_race(race_id)
        result = sim.evaluate_srm_slip(sim_data, conditions, offered_srm_odds=offered_odds)
        return {
            "race_id": race_id,
            "track_name": sim_data["track_name"],
            "distance": sim_data["distance"],
            "iterations": iterations,
            **result
        }
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Evaluation error: {e}")


# --- Live Results Auto-Reconciliation Endpoints ---
@app.get("/api/results/status")
def get_results_daemon_status():
    """Returns the current state and telemetry of the Results Auto-Reconciliation Daemon."""
    from results_daemon import results_daemon_instance
    return results_daemon_instance.get_status()


@app.post("/api/results/reconcile-now")
def trigger_results_reconciliation(target_date: Optional[str] = None):
    """Triggers an immediate results scraping, database update, and model reconciliation cycle."""
    from results_daemon import results_daemon_instance
    return results_daemon_instance.reconcile_now(target_date=target_date)


@app.get("/api/results/pnl")
def get_results_pnl(target_date: Optional[str] = None):
    """Returns real-time P&L, win/place strike rates, and ROI metrics from reconciled results."""
    from results_daemon import results_daemon_instance
    return results_daemon_instance.get_pnl_summary(target_date=target_date)


@app.get("/api/dogs/{dog_id}/dossier")
def get_dog_dossier(dog_id: int):
    from dog_history_collector import DogHistoryCollector
    collector = DogHistoryCollector()
    dossier = collector.get_dog_dossier_from_db(dog_id)
    if not dossier:
        raise HTTPException(status_code=404, detail="Dog profile not found")
    return dossier


@app.get("/api/multis")
def generate_multis(
    race_id: Optional[int] = None,
    min_edge: float = 0.01,
    min_total_odds: float = 5.0,
    legs_count: int = 3,
    odds_source: str = "sportsbet"
):
    # If a specific race is selected, generate Same Race Multis (SRM) for that race!
    if race_id is not None:
        from srm_optimizer import SameRaceMultiOptimizer
        srm_opt = SameRaceMultiOptimizer()
        srm_combos = srm_opt.generate_srm_combinations(
            race_id=race_id,
            min_total_odds=min_total_odds,
            max_total_odds=500.0,
            odds_source=odds_source
        )
        return {
            "is_srm": True,
            "race_id": race_id,
            "value_legs_found": len(srm_combos),
            "multis": srm_combos[:20]
        }

    # Otherwise generate Cross-Meeting multis
    opt = MultiBetOptimizer()
    v_legs = opt.find_value_legs(min_edge=min_edge, odds_source=odds_source)
    multis = opt.generate_optimal_multis(
        v_legs,
        leg_count=legs_count,
        min_total_odds=min_total_odds,
        max_total_odds=500.0
    )
    return {
        "is_srm": False,
        "odds_source": odds_source,
        "value_legs_found": len(v_legs),
        "value_legs": v_legs,
        "multis": multis[:15]
    }


# Background task runners
def _task_collect_thedogs(limit: int = 150):
    TASKS_STATUS["thedogs"] = {"running": True, "message": "Scraping TheDogs cards & live odds..."}
    try:
        scraper = TheDogsScraper()
        links = scraper.fetch_today_race_links()
        
        # Sort links to prioritize upcoming evening meetings first: Mandurah (WA), Sandown, Angle Park, Launceston, Northam
        def priority_key(url: str):
            u = url.lower()
            if "mandurah" in u: return 0
            if "sandown" in u: return 1
            if "angle-park" in u: return 2
            if "launceston" in u: return 3
            if "northam" in u: return 4
            return 10

        sorted_links = sorted(links, key=priority_key)
        total_to_scrape = min(limit, len(sorted_links))
        count = 0
        for idx, link in enumerate(sorted_links[:limit], 1):
            track_hint = link.split("/")[4].replace("-", " ").title() if len(link.split("/")) > 4 else "Track"
            TASKS_STATUS["thedogs"] = {"running": True, "message": f"Scraping {track_hint} ({idx}/{total_to_scrape})..."}
            data = scraper.parse_race(link)
            if data and data["runners"]:
                scraper.save_race_to_db(data)
                count += 1
        TASKS_STATUS["thedogs"] = {"running": False, "message": f"Successfully scraped {count} races"}
    except Exception as e:
        TASKS_STATUS["thedogs"] = {"running": False, "message": f"Error: {e}"}


def _task_collect_fasttrack(limit: int = 6):
    TASKS_STATUS["fasttrack"] = {"running": True, "message": "Scraping FastTrack sectional history..."}
    try:
        scraper = FastTrackScraper()
        links = scraper.fetch_meeting_links()
        count = 0
        for link in links[:limit]:
            data = scraper.parse_meeting(link)
            if data and data["races"]:
                scraper.save_to_db(data)
                count += 1
        TASKS_STATUS["fasttrack"] = {"running": False, "message": f"Successfully saved {count} meetings"}
    except Exception as e:
        TASKS_STATUS["fasttrack"] = {"running": False, "message": f"Error: {e}"}


def _task_collect_weather():
    TASKS_STATUS["weather"] = {"running": True, "message": "Fetching live track weather..."}
    try:
        wc = WeatherCollector()
        saved = wc.sync_all_today_tracks()
        TASKS_STATUS["weather"] = {"running": False, "message": f"Updated live weather for {saved} tracks"}
    except Exception as e:
        TASKS_STATUS["weather"] = {"running": False, "message": f"Error: {e}"}


def _task_collect_live_odds():
    TASKS_STATUS["live_odds"] = {"running": True, "message": "Polling live fixed market odds..."}
    try:
        from live_odds_poller import LiveOddsPoller
        poller = LiveOddsPoller()
        conn = get_connection()
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("""
        SELECT DISTINCT r.id, m.track_name, r.race_number, m.meeting_date
        FROM races r
        JOIN meetings m ON r.meeting_id = m.id
        JOIN race_runners rr ON rr.race_id = r.id
        WHERE rr.finish_position IS NULL AND m.source = 'thedogs'
        """)
        races = c.fetchall()
        total_updated = 0
        for r in races:
            track = r["track_name"].lower().replace(" ", "-")
            date_str = r["meeting_date"]
            r_num = r["race_number"]
            meeting_url = f"https://www.thedogs.com.au/racing/{track}/{date_str}"
            r_resp = poller.client.get(meeting_url)
            if r_resp.status_code == 200:
                try:
                    soup = BeautifulSoup(r_resp.text, "lxml")
                except Exception:
                    soup = BeautifulSoup(r_resp.text, "html.parser")
                pattern = f"/racing/{track}/{date_str}/{r_num}/"
                match_a = soup.find("a", href=re.compile(pattern))
                if match_a:
                    exact_odds_url = "https://www.thedogs.com.au" + match_a["href"].split("?")[0] + "/odds"
                    odds_map = poller.fetch_race_live_odds(exact_odds_url)
                    if odds_map:
                        for b_num, price in odds_map.items():
                            c.execute("UPDATE race_runners SET sp_odds = ? WHERE race_id = ? AND box_number = ?", (price, r["id"], b_num))
                            total_updated += c.rowcount
        conn.commit()
        conn.close()
        TASKS_STATUS["live_odds"] = {"running": False, "message": f"Live odds updated for {total_updated} runners"}
    except Exception as e:
        TASKS_STATUS["live_odds"] = {"running": False, "message": f"Error: {e}"}


def _task_sync_scratchings():
    TASKS_STATUS["scratchings"] = {"running": True, "message": "Synchronizing official scratchings & reserve reallocations..."}
    try:
        from scratchings_manager import ScratchingsManager
        sm = ScratchingsManager()
        res = sm.sync_daily_scratchings()
        if isinstance(res, dict):
            msg = f"Marked {res.get('scratched_marked', 0)} scratched, reallocated {res.get('reserves_reallocated', 0)} reserves"
        else:
            msg = f"Purged {res} scratched runners"
        TASKS_STATUS["scratchings"] = {"running": False, "message": msg}
    except Exception as e:
        TASKS_STATUS["scratchings"] = {"running": False, "message": f"Error: {e}"}


@app.post("/api/actions/fetch-scratchings")
def trigger_fetch_scratchings(background_tasks: BackgroundTasks):
    if TASKS_STATUS.get("scratchings", {}).get("running"):
        return {"status": "already_running"}
    background_tasks.add_task(_task_sync_scratchings)
    return {"status": "started", "message": "Synchronizing official scratchings"}


@app.post("/api/actions/fetch-thedogs")
def trigger_fetch_thedogs(background_tasks: BackgroundTasks):
    if TASKS_STATUS["thedogs"]["running"]:
        return {"status": "already_running"}
    background_tasks.add_task(_task_collect_thedogs)
    return {"status": "started", "message": "Started fetching races from TheDogs"}


@app.post("/api/actions/fetch-live-odds")
def trigger_fetch_live_odds(background_tasks: BackgroundTasks):
    if TASKS_STATUS.get("live_odds", {}).get("running"):
        return {"status": "already_running"}
    background_tasks.add_task(_task_collect_live_odds)
    return {"status": "started", "message": "Started polling live market odds"}


@app.post("/api/actions/fetch-fasttrack")
def trigger_fetch_fasttrack(background_tasks: BackgroundTasks):
    if TASKS_STATUS["fasttrack"]["running"]:
        return {"status": "already_running"}
    background_tasks.add_task(_task_collect_fasttrack)
    return {"status": "started", "message": "Started fetching FastTrack form & history"}


def _task_collect_sportsbet():
    TASKS_STATUS["sportsbet"] = {"running": True, "message": "Synchronizing Sportsbet live fixed odds..."}
    try:
        from sportsbet_scraper import SportsbetScraper
        sb = SportsbetScraper()
        res = sb.sync_sportsbet_odds()
        runners = res.get("runners_updated", 0)
        matched = res.get("races_matched", 0)
        TASKS_STATUS["sportsbet"] = {"running": False, "message": f"Updated {runners} runners across {matched} races"}
    except Exception as e:
        TASKS_STATUS["sportsbet"] = {"running": False, "message": f"Error: {e}"}


@app.post("/api/actions/fetch-sportsbet")
def trigger_fetch_sportsbet(background_tasks: BackgroundTasks):
    if TASKS_STATUS.get("sportsbet", {}).get("running"):
        return {"status": "already_running"}
    background_tasks.add_task(_task_collect_sportsbet)
    return {"status": "started", "message": "Synchronizing live Sportsbet odds"}


@app.post("/api/actions/fetch-weather")
def trigger_fetch_weather(background_tasks: BackgroundTasks):
    if TASKS_STATUS.get("weather", {}).get("running"):
        return {"status": "already_running"}
    background_tasks.add_task(_task_collect_weather)
    return {"status": "started", "message": "Fetching live track weather observations"}


@app.post("/api/actions/import-history")
def api_import_history(payload: Dict[str, Any]):
    """
    Imports historical race records via JSON payload or from a local file/directory path.
    """
    from history_importer import HistoryImporter
    importer = HistoryImporter()

    file_path = payload.get("file_path")
    records = payload.get("records")

    if file_path:
        p = Path(file_path)
        if p.is_dir():
            return importer.import_directory(str(p))
        return importer.import_file(str(p))
    elif records and isinstance(records, list):
        return importer.import_records(records)
    else:
        raise HTTPException(status_code=400, detail="Provide 'file_path' or 'records' in request body")


@app.get("/api/actions/import-template")
def api_get_import_template(format: str = "csv"):
    from history_importer import HistoryImporter
    c, j = HistoryImporter.generate_template()
    if format.lower() == "json":
        return FileResponse(j, media_type="application/json", filename="sample_race_history.json")
    return FileResponse(c, media_type="text/csv", filename="sample_race_history.csv")


# --- Daemon Management Endpoints ---
@app.get("/api/daemon/status")
def get_daemon_status():
    return daemon_instance.get_status()


@app.post("/api/daemon/toggle")
def toggle_daemon(enable: Optional[bool] = None):
    curr = daemon_instance.enabled
    new_val = (not curr) if enable is None else enable
    daemon_instance.set_enabled(new_val)
    return {
        "status": "success",
        "enabled": daemon_instance.enabled,
        "daemon": daemon_instance.get_status()
    }


@app.post("/api/daemon/poll-now")
def trigger_daemon_poll_now():
    res = daemon_instance.poll_now()
    return {"status": "success", "result": res}


# --- Historical Backtesting Endpoints ---
@app.get("/api/backtest/run")
def run_backtest_api(
    min_edge: float = 0.03,
    min_odds: float = 1.50,
    max_odds: float = 20.0,
    staking_method: str = "flat",
    flat_stake: float = 10.0,
    initial_bankroll: float = 1000.0,
    kelly_fraction: float = 0.25,
    odds_source: str = "best",
    limit: Optional[int] = None
):
    from backtest import HistoricalBacktester
    tester = HistoricalBacktester()
    return tester.run_backtest(
        min_edge=min_edge,
        min_odds=min_odds,
        max_odds=max_odds,
        staking_method=staking_method,
        flat_stake=flat_stake,
        initial_bankroll=initial_bankroll,
        kelly_fraction=kelly_fraction,
        odds_source=odds_source,
        limit=limit
    )


@app.get("/api/backtest/optimize")
def run_backtest_optimize():
    from backtest import HistoricalBacktester
    tester = HistoricalBacktester()
    return tester.optimize_hyperparameters()


# --- Daily Learning & Continuous Audit Endpoints ---
@app.get("/api/learning/audit")
def run_daily_learning_audit(
    date: Optional[str] = None,
    scrape_missing: bool = False,
    current_only: bool = False
):
    from daily_learning_engine import DailyLearningEngine
    import datetime
    target_date = date or datetime.date.today().isoformat()
    engine = DailyLearningEngine()
    if scrape_missing:
        engine.scrape_missing_results(target_date)
    return engine.run_daily_audit(target_date, current_only=current_only)

@app.get("/api/reports/first4-audit")
def get_first4_audit_report(
    date: Optional[str] = None,
    track: Optional[str] = None,
    distance: Optional[int] = None,
    current_only: Optional[bool] = False
):
    from first4_audit_report import generate_first4_audit
    return generate_first4_audit(
        date_str=date,
        track_filter=track,
        distance_filter=distance,
        current_only=current_only
    )

@app.get("/api/export/predictions")
def export_historical_predictions():
    from fastapi.responses import PlainTextResponse
    import sqlite3
    from datetime import datetime
    from analytics import GreyhoundAnalytics
    from export_predictions import get_actual_top4

    today_iso = datetime.now().strftime('%Y-%m-%d')
    conn = sqlite3.connect('greyhound_racing.db')
    c = conn.cursor()

    # 1. Auto-sync any finished races today into historical_predictions
    try:
        c.execute('''
            SELECT DISTINCT r.id
            FROM races r
            JOIN meetings m ON r.meeting_id = m.id
            JOIN race_runners rr ON rr.race_id = r.id
            WHERE m.meeting_date = ? AND rr.finish_position IS NOT NULL
        ''', (today_iso,))
        finished_race_ids = [row[0] for row in c.fetchall()]

        if finished_race_ids:
            analytics = GreyhoundAnalytics()
            for r_id in finished_race_ids:
                actual_str = get_actual_top4(conn, r_id) or ""
                c.execute('SELECT id, actual_top4 FROM historical_predictions WHERE race_id = ?', (r_id,))
                existing = c.fetchone()
                if not existing:
                    preds = analytics.predict_race_probabilities(r_id)
                    if preds:
                        pred_str = ",".join(str(p["box"]) for p in preds[:4])
                        c.execute('''
                            INSERT INTO historical_predictions (race_id, predicted_top4, actual_top4)
                            VALUES (?, ?, ?)
                        ''', (r_id, pred_str, actual_str))
                elif (not existing[1] or existing[1] == "Pending") and actual_str:
                    c.execute('UPDATE historical_predictions SET actual_top4 = ? WHERE race_id = ?', (actual_str, r_id))
            conn.commit()
    except Exception as e:
        print(f"[Export] Warning auto-syncing today predictions: {e}")

    # 2. Fetch and return formatted predictions for today
    c.execute('''
        SELECT m.track_name, r.race_number, hp.predicted_top4, hp.actual_top4
        FROM historical_predictions hp
        JOIN races r ON hp.race_id = r.id
        JOIN meetings m ON r.meeting_id = m.id
        WHERE m.meeting_date = ?
        ORDER BY m.track_name, r.race_number
    ''', (today_iso,))
    records = c.fetchall()
    conn.close()

    lines = ["--- HISTORICAL PREDICTIONS EXPORT ---\n"]
    if not records:
        lines.append("No historical predictions found for today.")
    else:
        for row in records:
            track = row[0]
            race_num = row[1]
            pred = row[2]
            act = row[3] or "Pending"
            lines.append(f"{track} R{race_num} pred: {pred} act: {act}")

    content = "\n".join(lines)
    return PlainTextResponse(
        content=content,
        headers={"Content-Disposition": "attachment; filename=historical_predictions.txt"}
    )


# -------------------------------------------------------------
# DOG DOSSIER HYDRATION & SECTIONAL INGESTION ENDPOINTS
# -------------------------------------------------------------

@app.get("/api/hydration/status")
def get_hydration_status():
    """Returns background dog hydration daemon telemetry."""
    return hydration_daemon_instance.get_status()


@app.post("/api/hydration/run")
def trigger_hydration_run(background_tasks: BackgroundTasks, max_dogs: int = 40):
    """Triggers an immediate background batch pass to hydrate upcoming runners."""
    def run_worker():
        worker = DogHydrationWorker()
        worker.run_batch_hydration(max_dogs=max_dogs, verbose=False)

    background_tasks.add_task(run_worker)
    return {"status": "queued", "message": f"Queued hydration pass for up to {max_dogs} runners."}


@app.post("/api/hydration/race/{race_id}")
def hydrate_specific_race(race_id: int):
    """Synchronously hydrates all active runners in a specific race."""
    worker = DogHydrationWorker()
    result = worker.hydrate_race_runners(race_id)
    return result


# -------------------------------------------------------------
# CONTINUOUS BRIER SCORE & LOG-LOSS CALIBRATION ENDPOINTS
# -------------------------------------------------------------

@app.get("/api/calibration/metrics")
def get_calibration_metrics(date: Optional[str] = None, track: Optional[str] = None):
    """
    Computes and returns Multi-class Brier Score, Cross-Entropy Log-Loss,
    Expected Calibration Error (ECE), and decile reliability diagram.
    """
    engine = DailyLearningEngine()
    result = engine.compute_brier_and_log_loss(target_date=date, track_filter=track)
    return result


@app.get("/api/calibration/status")
def get_track_calibrations_status():
    """Returns all empirically calibrated track temperature multipliers from SQLite."""
    engine = DailyLearningEngine()
    return engine.get_calibration_status()


@app.post("/api/calibration/auto-tune")
def trigger_track_autotune(background_tasks: BackgroundTasks, min_races: int = 4):
    """Triggers background temperature optimization across all qualifying tracks."""
    def run_tuner():
        engine = DailyLearningEngine()
        engine.auto_tune_track_temperatures(min_races=min_races, verbose=False)

    background_tasks.add_task(run_tuner)
    return {"status": "queued", "message": f"Queued track temperature auto-tuning pass (min {min_races} races)."}


if __name__ == "__main__":
    import uvicorn
    init_db()
    uvicorn.run("app:app", host="127.0.0.1", port=8090, reload=True)
