"""
first4_audit_report.py
======================
Generates comprehensive First 4 & Exotic hit rate audit reports comparing
model predictions against actual official race results.

Supports filtering by:
  - Meeting date (default: today)
  - Track location (e.g., 'The Gardens', 'Meadows', 'Bendigo', 'Q1 Lakeside')
  - Race distance (e.g., 400, 500, 525, 600)

Usage via CLI:
  python first4_audit_report.py
  python first4_audit_report.py --track "The Gardens"
  python first4_audit_report.py --distance 525
  python first4_audit_report.py --track "Meadows" --distance 525
  python first4_audit_report.py --json
"""

import sys
import re
import json
import argparse
import sqlite3
import datetime
from collections import defaultdict
from typing import Dict, Any, List, Optional
from analytics import GreyhoundAnalytics


def clean_dog_name(name: Optional[str]) -> str:
    """Strip reserve indicators and time suffixes for clean display."""
    if not name:
        return ""
    cleaned = re.sub(r'\(from box \d+\)', '', name)
    cleaned = re.sub(r'\b\d+\.\d+\b', '', cleaned)
    return cleaned.strip()


def get_actual_effective_box(row: sqlite3.Row) -> int:
    """Resolve effective running box (accommodating reserve replacements)."""
    if "into_box" in row.keys() and row["into_box"]:
        return int(row["into_box"])
    match = re.search(r'\(from box (\d+)\)', row["dog_name"] or '')
    if match:
        return int(match.group(1))
    return int(row["box_number"])


def generate_first4_audit(
    date_str: Optional[str] = None,
    track_filter: Optional[str] = None,
    distance_filter: Optional[int] = None,
    current_only: Optional[bool] = False
) -> Dict[str, Any]:
    """
    Computes First 4 and exotic hit rates comparing model predictions to actual results.
    Returns structured summary, breakdowns by track and distance, and individual race items.
    """
    if not date_str:
        date_str = datetime.date.today().isoformat()

    analytics = GreyhoundAnalytics()
    conn = sqlite3.connect('greyhound_racing.db')
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    # Query all races with finished runners on target date
    query = """
        SELECT r.id, r.race_number, r.distance, r.grade, r.start_time, m.track_name
        FROM races r
        JOIN meetings m ON r.meeting_id = m.id
        WHERE m.meeting_date = ?
    """
    params: List[Any] = [date_str]

    if track_filter:
        clean_track = track_filter.strip().lower()
        query += " AND (LOWER(m.track_name) LIKE ? OR ? LIKE ('%' || LOWER(m.track_name) || '%'))"
        params.extend([f"%{clean_track}%", clean_track])

    if distance_filter:
        query += " AND r.distance = ?"
        params.append(int(distance_filter))

    query += " ORDER BY m.track_name ASC, r.race_number ASC, r.start_time ASC"
    c.execute(query, params)
    races = c.fetchall()

    # Discover all available tracks and distances today for easy UI dropdown population
    c.execute("""
        SELECT DISTINCT m.track_name
        FROM races r
        JOIN meetings m ON r.meeting_id = m.id
        JOIN race_runners rr ON rr.race_id = r.id
        WHERE m.meeting_date = ? AND rr.finish_position = 1
        ORDER BY m.track_name ASC
    """, (date_str,))
    available_tracks = [row["track_name"] for row in c.fetchall()]

    c.execute("""
        SELECT DISTINCT r.distance
        FROM races r
        JOIN meetings m ON r.meeting_id = m.id
        JOIN race_runners rr ON rr.race_id = r.id
        WHERE m.meeting_date = ? AND rr.finish_position = 1
        ORDER BY r.distance ASC
    """, (date_str,))
    available_distances = [row["distance"] for row in c.fetchall()]

    summary = {
        "total_completed": 0,
        "win_top1": 0,
        "win_top1_pct": 0.0,
        "win_top2": 0,
        "win_top2_pct": 0.0,
        "win_top4": 0,
        "win_top4_pct": 0.0,
        "exact_quinella": 0,
        "exact_quinella_pct": 0.0,
        "boxed_quinella": 0,
        "boxed_quinella_pct": 0.0,
        "trifecta_in_top4": 0,
        "trifecta_in_top4_pct": 0.0,
        "trifecta_in_top5": 0,
        "trifecta_in_top5_pct": 0.0,
        "exact_trifecta": 0,
        "exact_trifecta_pct": 0.0,
        "boxed_first4": 0,               # 4-dog box (legacy key)
        "boxed_first4_pct": 0.0,
        "boxed_first4_4dog": 0,
        "boxed_first4_4dog_pct": 0.0,
        "boxed_first4_5dog": 0,
        "boxed_first4_5dog_pct": 0.0,
        "boxed_first4_6dog": 0,
        "boxed_first4_6dog_pct": 0.0,
        "key_standout_f4": 0,
        "key_standout_f4_pct": 0.0,
        "exact_first4": 0,
        "exact_first4_pct": 0.0,
        "srm_3_of_4": 0,
        "srm_3_of_4_pct": 0.0,
        "overlap_counts": {4: 0, 3: 0, 2: 0, 1: 0, 0: 0}
    }

    track_stats: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
        "track": "", "races": 0, "win_top1": 0, "win_top4": 0,
        "srm_3plus": 0, "trifecta_in_4": 0, "boxed_first4": 0,
        "boxed_first4_4dog": 0, "boxed_first4_5dog": 0, "boxed_first4_6dog": 0,
        "key_standout_f4": 0, "exact_first4": 0
    })

    dist_stats: Dict[int, Dict[str, Any]] = defaultdict(lambda: {
        "distance": 0, "races": 0, "win_top1": 0, "win_top4": 0,
        "srm_3plus": 0, "trifecta_in_4": 0, "boxed_first4": 0,
        "boxed_first4_4dog": 0, "boxed_first4_5dog": 0, "boxed_first4_6dog": 0,
        "key_standout_f4": 0, "exact_first4": 0
    })

    td_stats: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
        "track": "", "distance": 0, "key": "", "races": 0, "win_top1": 0, "win_top4": 0,
        "srm_3plus": 0, "trifecta_in_4": 0, "boxed_first4": 0,
        "boxed_first4_4dog": 0, "boxed_first4_5dog": 0, "boxed_first4_6dog": 0,
        "key_standout_f4": 0, "exact_first4": 0
    })

    race_records = []

    for r in races:
        race_id = r["id"]
        # Fetch actual finishers ordered 1st to 4th
        c.execute("""
            SELECT rr.box_number, rr.finish_position, rr.into_box, g.dog_name
            FROM race_runners rr
            LEFT JOIN greyhounds g ON rr.dog_id = g.id
            WHERE rr.race_id = ? AND rr.finish_position IS NOT NULL
            ORDER BY rr.finish_position ASC
        """, (race_id,))
        actual_runners = c.fetchall()

        if not actual_runners or len(actual_runners) < 4:
            continue
        if actual_runners[0]["finish_position"] != 1:
            continue

        actual_top4_boxes = [get_actual_effective_box(row) for row in actual_runners[:4]]
        actual_top4_names = [clean_dog_name(row["dog_name"]) for row in actual_runners[:4]]
        actual_set = set(actual_top4_boxes)

        # Generate model predictions
        preds = analytics.predict_race_probabilities(race_id)
        if not preds or len(preds) < 4:
            continue

        pred_top4_boxes = [int(p["box"]) for p in preds[:4]]
        pred_top4_names = [clean_dog_name(p["dog_name"]) for p in preds[:4]]
        pred_top5_boxes = [int(p["box"]) for p in preds[:5]]
        pred_top6_boxes = [int(p["box"]) for p in preds[:6]]

        summary["total_completed"] += 1

        # Check exotic & position metrics
        win_hit = (pred_top4_boxes[0] == actual_top4_boxes[0])
        win_in_top2 = (actual_top4_boxes[0] in pred_top4_boxes[:2])
        win_in_top4 = (actual_top4_boxes[0] in pred_top4_boxes[:4])

        exact_q = (pred_top4_boxes[0] == actual_top4_boxes[0] and pred_top4_boxes[1] == actual_top4_boxes[1])
        boxed_q = set(actual_top4_boxes[:2]).issubset(set(pred_top4_boxes[:4]))

        trifecta_in_4 = set(actual_top4_boxes[:3]).issubset(set(pred_top4_boxes[:4]))
        trifecta_in_5 = set(actual_top4_boxes[:3]).issubset(set(pred_top5_boxes))
        exact_tri = (pred_top4_boxes[:3] == actual_top4_boxes[:3])

        exact_f4 = (pred_top4_boxes[:4] == actual_top4_boxes[:4])
        overlap_count = len(set(pred_top4_boxes[:4]).intersection(actual_set))

        # Multi-tiered Boxed First 4 hit checks:
        boxed_f4_4dog = (overlap_count == 4)
        boxed_f4_5dog = actual_set.issubset(set(pred_top5_boxes))
        boxed_f4_6dog = actual_set.issubset(set(pred_top6_boxes))

        # Key Standout First 4: Winner in top 2, 2nd in top 3, all 4 in top 6
        key_standout_hit = (
            actual_top4_boxes[0] in pred_top4_boxes[:2]
            and actual_top4_boxes[1] in pred_top4_boxes[:3]
            and actual_set.issubset(set(pred_top6_boxes))
        )

        srm_hit = (overlap_count >= 3)

        if win_hit: summary["win_top1"] += 1
        if win_in_top2: summary["win_top2"] += 1
        if win_in_top4: summary["win_top4"] += 1
        if exact_q: summary["exact_quinella"] += 1
        if boxed_q: summary["boxed_quinella"] += 1
        if trifecta_in_4: summary["trifecta_in_top4"] += 1
        if trifecta_in_5: summary["trifecta_in_top5"] += 1
        if exact_tri: summary["exact_trifecta"] += 1

        summary["boxed_first4"] += 1 if boxed_f4_4dog else 0
        summary["boxed_first4_4dog"] += 1 if boxed_f4_4dog else 0
        summary["boxed_first4_5dog"] += 1 if boxed_f4_5dog else 0
        summary["boxed_first4_6dog"] += 1 if boxed_f4_6dog else 0
        summary["key_standout_f4"] += 1 if key_standout_hit else 0
        summary["exact_first4"] += 1 if exact_f4 else 0
        summary["srm_3_of_4"] += 1 if srm_hit else 0

        summary["overlap_counts"][overlap_count] = summary["overlap_counts"].get(overlap_count, 0) + 1

        # Update breakdown buckets
        track_name = r["track_name"]
        distance = r["distance"]
        td_key = f"{track_name} {distance}m"

        t_bucket = track_stats[track_name]
        t_bucket["track"] = track_name
        t_bucket["races"] += 1
        if win_hit: t_bucket["win_top1"] += 1
        if win_in_top4: t_bucket["win_top4"] += 1
        if srm_hit: t_bucket["srm_3plus"] += 1
        if trifecta_in_4: t_bucket["trifecta_in_4"] += 1
        if boxed_f4_4dog: t_bucket["boxed_first4"] += 1
        if boxed_f4_4dog: t_bucket["boxed_first4_4dog"] += 1
        if boxed_f4_5dog: t_bucket["boxed_first4_5dog"] += 1
        if boxed_f4_6dog: t_bucket["boxed_first4_6dog"] += 1
        if key_standout_hit: t_bucket["key_standout_f4"] += 1
        if exact_f4: t_bucket["exact_first4"] += 1

        d_bucket = dist_stats[distance]
        d_bucket["distance"] = distance
        d_bucket["races"] += 1
        if win_hit: d_bucket["win_top1"] += 1
        if win_in_top4: d_bucket["win_top4"] += 1
        if srm_hit: d_bucket["srm_3plus"] += 1
        if trifecta_in_4: d_bucket["trifecta_in_4"] += 1
        if boxed_f4_4dog: d_bucket["boxed_first4"] += 1
        if boxed_f4_4dog: d_bucket["boxed_first4_4dog"] += 1
        if boxed_f4_5dog: d_bucket["boxed_first4_5dog"] += 1
        if boxed_f4_6dog: d_bucket["boxed_first4_6dog"] += 1
        if key_standout_hit: d_bucket["key_standout_f4"] += 1
        if exact_f4: d_bucket["exact_first4"] += 1

        td_bucket = td_stats[td_key]
        td_bucket["track"] = track_name
        td_bucket["distance"] = distance
        td_bucket["key"] = td_key
        td_bucket["races"] += 1
        if win_hit: td_bucket["win_top1"] += 1
        if win_in_top4: td_bucket["win_top4"] += 1
        if srm_hit: td_bucket["srm_3plus"] += 1
        if trifecta_in_4: td_bucket["trifecta_in_4"] += 1
        if boxed_f4_4dog: td_bucket["boxed_first4"] += 1
        if boxed_f4_4dog: td_bucket["boxed_first4_4dog"] += 1
        if boxed_f4_5dog: td_bucket["boxed_first4_5dog"] += 1
        if boxed_f4_6dog: td_bucket["boxed_first4_6dog"] += 1
        if key_standout_hit: td_bucket["key_standout_f4"] += 1
        if exact_f4: td_bucket["exact_first4"] += 1

        # Hit badges for UI
        badges = []
        if exact_f4:
            badges.append("EXACT F4")
        if boxed_f4_4dog:
            badges.append("4-DOG F4")
        elif boxed_f4_5dog:
            badges.append("5-DOG F4")
        elif boxed_f4_6dog:
            badges.append("6-DOG F4")

        if key_standout_hit:
            badges.append("STANDOUT F4")

        if exact_tri:
            badges.append("EXACT TRI")
        elif trifecta_in_4:
            badges.append("TRIFECTA")
        if srm_hit:
            badges.append(f"{overlap_count}/4 SRM")
        if win_hit:
            badges.append("TOP 1 WIN")
        elif win_in_top4:
            badges.append("WIN IN 4")

        race_records.append({
            "race_id": race_id,
            "track": track_name,
            "race_number": r["race_number"],
            "distance": distance,
            "grade": r["grade"] or "N/A",
            "start_time": r["start_time"] or "",
            "pred_boxes": pred_top4_boxes,
            "pred_names": pred_top4_names,
            "pred_boxes_5": pred_top5_boxes,
            "pred_boxes_6": pred_top6_boxes,
            "actual_boxes": actual_top4_boxes,
            "actual_names": actual_top4_names,
            "overlap_count": overlap_count,
            "win_hit": win_hit,
            "win_in_top4": win_in_top4,
            "srm_hit": srm_hit,
            "trifecta_in_4": trifecta_in_4,
            "trifecta_in_5": trifecta_in_5,
            "boxed_f4": boxed_f4_4dog,
            "boxed_f4_4dog": boxed_f4_4dog,
            "boxed_f4_5dog": boxed_f4_5dog,
            "boxed_f4_6dog": boxed_f4_6dog,
            "key_standout_hit": key_standout_hit,
            "exact_f4": exact_f4,
            "badges": badges
        })

    # Discover meeting totals and remaining races for all tracks today and on target date
    today_str = datetime.date.today().isoformat()
    c.execute("""
        SELECT m.track_name,
               COUNT(r.id) as total_races,
               SUM(CASE WHEN EXISTS (
                   SELECT 1 FROM race_runners rr
                   WHERE rr.race_id = r.id AND rr.finish_position = 1
               ) THEN 1 ELSE 0 END) as completed_races
        FROM meetings m
        JOIN races r ON r.meeting_id = m.id
        WHERE m.meeting_date = ?
        GROUP BY m.track_name
    """, (date_str,))
    date_track_meetings = {}
    for row in c.fetchall():
        tot = row["total_races"] or 0
        comp = row["completed_races"] or 0
        rem = max(0, tot - comp)
        date_track_meetings[row["track_name"]] = {
            "total_races": tot,
            "completed_races": comp,
            "remaining_races": rem,
        }

    today_active_tracks = {}
    if date_str != today_str:
        c.execute("""
            SELECT m.track_name,
                   COUNT(r.id) as total_races,
                   SUM(CASE WHEN EXISTS (
                       SELECT 1 FROM race_runners rr
                       WHERE rr.race_id = r.id AND rr.finish_position = 1
                   ) THEN 1 ELSE 0 END) as completed_races
            FROM meetings m
            JOIN races r ON r.meeting_id = m.id
            WHERE m.meeting_date = ?
            GROUP BY m.track_name
        """, (today_str,))
        for row in c.fetchall():
            tot = row["total_races"] or 0
            comp = row["completed_races"] or 0
            rem = max(0, tot - comp)
            today_active_tracks[row["track_name"]] = {
                "total_races": tot,
                "completed_races": comp,
                "remaining_races": rem,
            }

    conn.close()

    total = summary["total_completed"]
    if total > 0:
        summary["win_top1_pct"] = round(summary["win_top1"] / total * 100, 1)
        summary["win_top2_pct"] = round(summary["win_top2"] / total * 100, 1)
        summary["win_top4_pct"] = round(summary["win_top4"] / total * 100, 1)
        summary["exact_quinella_pct"] = round(summary["exact_quinella"] / total * 100, 1)
        summary["boxed_quinella_pct"] = round(summary["boxed_quinella"] / total * 100, 1)
        summary["trifecta_in_top4_pct"] = round(summary["trifecta_in_top4"] / total * 100, 1)
        summary["trifecta_in_top5_pct"] = round(summary["trifecta_in_top5"] / total * 100, 1)
        summary["exact_trifecta_pct"] = round(summary["exact_trifecta"] / total * 100, 1)
        summary["boxed_first4_pct"] = round(summary["boxed_first4"] / total * 100, 1)
        summary["boxed_first4_4dog_pct"] = round(summary["boxed_first4_4dog"] / total * 100, 1)
        summary["boxed_first4_5dog_pct"] = round(summary["boxed_first4_5dog"] / total * 100, 1)
        summary["boxed_first4_6dog_pct"] = round(summary["boxed_first4_6dog"] / total * 100, 1)
        summary["key_standout_f4_pct"] = round(summary["key_standout_f4"] / total * 100, 1)
        summary["exact_first4_pct"] = round(summary["exact_first4"] / total * 100, 1)
        summary["srm_3_of_4_pct"] = round(summary["srm_3_of_4"] / total * 100, 1)

    # Format breakdown tables with percentages
    def add_pcts(item: Dict[str, Any]) -> Dict[str, Any]:
        n = item["races"]
        return {
            **item,
            "win_top1_pct": round(item["win_top1"] / n * 100, 1) if n else 0.0,
            "win_top4_pct": round(item["win_top4"] / n * 100, 1) if n else 0.0,
            "srm_3plus_pct": round(item["srm_3plus"] / n * 100, 1) if n else 0.0,
            "trifecta_in_4_pct": round(item["trifecta_in_4"] / n * 100, 1) if n else 0.0,
            "boxed_first4_pct": round(item["boxed_first4"] / n * 100, 1) if n else 0.0,
            "boxed_first4_4dog_pct": round(item.get("boxed_first4_4dog", item["boxed_first4"]) / n * 100, 1) if n else 0.0,
            "boxed_first4_5dog_pct": round(item.get("boxed_first4_5dog", 0) / n * 100, 1) if n else 0.0,
            "boxed_first4_6dog_pct": round(item.get("boxed_first4_6dog", 0) / n * 100, 1) if n else 0.0,
            "key_standout_f4_pct": round(item.get("key_standout_f4", 0) / n * 100, 1) if n else 0.0,
            "exact_first4_pct": round(item["exact_first4"] / n * 100, 1) if n else 0.0,
        }

    all_formatted_tracks = [add_pcts(s) for s in sorted(track_stats.values(), key=lambda x: x["races"], reverse=True)]
    for item in all_formatted_tracks:
        t_name = item["track"]
        d_info = date_track_meetings.get(t_name, {})
        tot = d_info.get("total_races", item["races"])
        rem = d_info.get("remaining_races", 0)

        if date_str == today_str:
            is_current = (rem > 0)
            remaining_today = rem
        else:
            t_today_info = today_active_tracks.get(t_name)
            if t_today_info:
                remaining_today = t_today_info.get("remaining_races", 0)
                is_current = (remaining_today > 0)
            else:
                remaining_today = 0
                is_current = (rem > 0)

        item["total_meeting_races"] = tot
        item["completed_meeting_races"] = d_info.get("completed_races", item["races"])
        item["remaining_races"] = remaining_today if (date_str != today_str and t_name in today_active_tracks) else rem
        item["is_current_location"] = is_current

    filtered_tracks = [t for t in all_formatted_tracks if t.get("is_current_location", False)] if current_only else all_formatted_tracks
    formatted_dists = [add_pcts(s) for s in sorted(dist_stats.values(), key=lambda x: x["distance"])]
    formatted_tds = [add_pcts(s) for s in sorted(td_stats.values(), key=lambda x: (x["track"], x["distance"]))]

    return {
        "date": date_str,
        "track_filter": track_filter,
        "distance_filter": distance_filter,
        "current_only": bool(current_only),
        "current_tracks_count": len([t for t in all_formatted_tracks if t.get("is_current_location", False)]),
        "total_tracks_count": len(all_formatted_tracks),
        "summary": summary,
        "breakdown_by_track": filtered_tracks,
        "all_tracks_breakdown": all_formatted_tracks,
        "breakdown_by_distance": formatted_dists,
        "breakdown_by_track_distance": formatted_tds,
        "races": race_records,
        "available_tracks": available_tracks,
        "available_distances": available_distances
    }


def print_cli_report(audit_data: Dict[str, Any], show_races: bool = True):
    """Renders formatted CLI report to terminal."""
    # Ensure UTF-8 or safe encoding
    try:
        if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

    sum_data = audit_data["summary"]
    total = sum_data["total_completed"]
    date_str = audit_data["date"]
    t_filt = audit_data["track_filter"] or "All Tracks"
    d_filt = f"{audit_data['distance_filter']}m" if audit_data["distance_filter"] else "All Distances"
    c_filt = " | [Current Locations Only: Yes]" if audit_data.get("current_only") else ""

    print("\n" + "=" * 84)
    print(f"       GREYHOUND FIRST 4 & EXOTIC AUDIT REPORT -- {date_str}")
    print(f"       Filter: [Location: {t_filt}] | [Distance: {d_filt}]{c_filt}")
    print("=" * 84)

    if total == 0:
        print("\nNo completed races found matching the specified filters.\n")
        return

    print("\n" + "-" * 88)
    print(" 1. OVERALL HIT RATE & MULTI-TIER TICKET PERFORMANCE")
    print("-" * 88)
    print(f" Total Completed Races Audited   : {total}")
    print(f" Top 1 Win Rate (P1 = Win)      : {sum_data['win_top1']}/{total} ({sum_data['win_top1_pct']}%)")
    print(f" Winner in Top 2 (P1-P2)        : {sum_data['win_top2']}/{total} ({sum_data['win_top2_pct']}%)")
    print(f" Winner in Top 4 (P1-P4)        : {sum_data['win_top4']}/{total} ({sum_data['win_top4_pct']}%)")
    print(f" 3+ in Top 4 (SRM Hit Rate)     : {sum_data['srm_3_of_4']}/{total} ({sum_data['srm_3_of_4_pct']}%)")
    print(f" Trifecta in Top 4 (1-2-3 in 4)  : {sum_data['trifecta_in_top4']}/{total} ({sum_data['trifecta_in_top4_pct']}%)")
    print(f" Trifecta in Top 5 (1-2-3 in 5)  : {sum_data['trifecta_in_top5']}/{total} ({sum_data['trifecta_in_top5_pct']}%)")
    print(" " + "-" * 86)
    print(" MULTI-TIERED FIRST 4 TICKETS:")
    print(f" • Tier 1: 4-Dog Box ( 24 combos) : {sum_data['boxed_first4_4dog']}/{total} ({sum_data['boxed_first4_4dog_pct']}%)")
    print(f" • Tier 2: 5-Dog Box (120 combos) : {sum_data['boxed_first4_5dog']}/{total} ({sum_data['boxed_first4_5dog_pct']}%)")
    print(f" • Tier 3: 6-Dog Box (360 combos) : {sum_data['boxed_first4_6dog']}/{total} ({sum_data['boxed_first4_6dog_pct']}%) [High-Coverage Target]")
    print(f" • Tier 4: Key Standout (24 combos): {sum_data['key_standout_f4']}/{total} ({sum_data['key_standout_f4_pct']}%)")
    print(f" • Exact First 4 (1-2-3-4 Order) : {sum_data['exact_first4']}/{total} ({sum_data['exact_first4_pct']}%)")

    # Overlap Breakdown
    counts = sum_data["overlap_counts"]
    print("\n Top 4 Overlap Distribution:")
    for k in [4, 3, 2, 1, 0]:
        c = counts.get(k, 0)
        pct = round(c / total * 100, 1) if total else 0.0
        bar = "#" * int(pct // 4)
        print(f"   {k}/4 overlap : {c:>2}/{total} ({pct:>5.1f}%) {bar}")

    # Track Breakdown Table
    print("\n" + "-" * 98)
    print(" 2. BREAKDOWN BY TRACK LOCATION" + (" (Current Race Locations Only)" if audit_data.get("current_only") else ""))
    print("-" * 98)
    print(f" {'Track Name':<16} | {'Status':<12} | {'Races':<5} | {'Top 1 Win':<11} | {'3+ in 4':<10} | {'4-Dog F4':<10} | {'5-Dog F4':<10} | {'6-Dog F4':<10}")
    print(" " + "-" * 96)
    for t in audit_data["breakdown_by_track"]:
        r = t["races"]
        rem = t.get("remaining_races", 0)
        status = f"{rem} left" if t.get("is_current_location") else "Done"
        print(f" {t['track']:<16} | {status:<12} | {r:<5} | {t['win_top1']:>2}/{r} ({t['win_top1_pct']:>4.1f}%) | {t['srm_3plus']:>2}/{r} ({t['srm_3plus_pct']:>4.1f}%) | {t['boxed_first4_4dog']:>2}/{r} ({t['boxed_first4_4dog_pct']:>4.1f}%) | {t['boxed_first4_5dog']:>2}/{r} ({t['boxed_first4_5dog_pct']:>4.1f}%) | {t['boxed_first4_6dog']:>2}/{r} ({t['boxed_first4_6dog_pct']:>4.1f}%)")

    # Distance Breakdown Table
    print("\n" + "-" * 88)
    print(" 3. BREAKDOWN BY RACE DISTANCE")
    print("-" * 88)
    print(f" {'Distance':<16} | {'Races':<5} | {'Top 1 Win':<11} | {'3+ in 4':<10} | {'4-Dog F4':<10} | {'5-Dog F4':<10} | {'6-Dog F4':<10}")
    print(" " + "-" * 86)
    for d in audit_data["breakdown_by_distance"]:
        r = d["races"]
        dist_label = f"{d['distance']}m"
        print(f" {dist_label:<16} | {r:<5} | {d['win_top1']:>2}/{r} ({d['win_top1_pct']:>4.1f}%) | {d['srm_3plus']:>2}/{r} ({d['srm_3plus_pct']:>4.1f}%) | {d['boxed_first4_4dog']:>2}/{r} ({d['boxed_first4_4dog_pct']:>4.1f}%) | {d['boxed_first4_5dog']:>2}/{r} ({d['boxed_first4_5dog_pct']:>4.1f}%) | {d['boxed_first4_6dog']:>2}/{r} ({d['boxed_first4_6dog_pct']:>4.1f}%)")

    # Race-by-race table
    if show_races and audit_data["races"]:
        print("\n" + "-" * 88)
        print(" 4. RACE-BY-RACE AUDIT DETAIL")
        print("-" * 88)
        print(f" {'Race':<18} | {'Dist':<5} | {'Pred Top 4':<12} | {'Actual 1-4':<12} | {'Hit Badges'}")
        print(" " + "-" * 86)
        for rc in audit_data["races"]:
            lbl = f"{rc['track']} R{rc['race_number']}"
            pred_str = "-".join(str(b) for b in rc["pred_boxes"])
            act_str = "-".join(str(b) for b in rc["actual_boxes"])
            badge_str = " ".join(f"[{b}]" for b in rc["badges"]) if rc["badges"] else "[MISS]"
            print(f" {lbl:<18} | {rc['distance']}m  | {pred_str:<12} | {act_str:<12} | {badge_str}")

    print("\n" + "=" * 88 + "\n")


def main():
    parser = argparse.ArgumentParser(description="First 4 & Exotic Hit Rate Audit Report")
    parser.add_argument("--date", type=str, default=None, help="Race date (YYYY-MM-DD), defaults to today")
    parser.add_argument("-t", "--track", type=str, default=None, help="Filter by track location (e.g. 'Meadows', 'Bendigo')")
    parser.add_argument("-d", "--distance", type=int, default=None, help="Filter by race distance (e.g. 525, 400)")
    parser.add_argument("--current-only", action="store_true", help="Only show track performance from current race locations with remaining races today")
    parser.add_argument("--json", action="store_true", help="Output results in JSON format")
    parser.add_argument("--no-races", action="store_true", help="Omit race-by-race table from CLI output")

    args = parser.parse_args()

    audit = generate_first4_audit(
        date_str=args.date,
        track_filter=args.track,
        distance_filter=args.distance,
        current_only=args.current_only
    )

    if args.json:
        print(json.dumps(audit, indent=2))
    else:
        print_cli_report(audit, show_races=not args.no_races)


if __name__ == "__main__":
    main()
