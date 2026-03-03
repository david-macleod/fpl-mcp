#!/usr/bin/env python3
"""
FPL Data Extractor

Downloads and exports Fantasy Premier League data to CSV files.
Uses synchronous requests to avoid async DNS resolution issues.
"""

import asyncio
import csv
import json
import os
import subprocess
from typing import Dict, Optional
from constants import TEAM_ID, API_URLS


def curl_get(url: str) -> dict:
    """Fetch JSON data using curl subprocess (avoids async DNS issues)."""
    result = subprocess.run(
        ['curl', '-s', '--max-time', '30', url],
        capture_output=True, text=True, timeout=35
    )
    if result.returncode != 0:
        raise Exception(f"curl failed for {url}: {result.stderr}")
    return json.loads(result.stdout)


def write_csv(data: list, filepath: str, fieldnames=None) -> str:
    """Write a list of dicts to a CSV file."""
    os.makedirs(os.path.dirname(filepath) if os.path.dirname(filepath) else ".", exist_ok=True)
    if not data:
        with open(filepath, 'w', newline='', encoding='utf-8') as f:
            if fieldnames:
                csv.DictWriter(f, fieldnames=fieldnames).writeheader()
        return filepath
    with open(filepath, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames or data[0].keys())
        writer.writeheader()
        writer.writerows(data)
    return filepath


class FPLDataExtractor:
    """Extracts FPL data and exports to CSV files."""

    def __init__(self):
        self.team_id = TEAM_ID
        self.static_data: Optional[dict] = None

    async def __aenter__(self):
        self.static_data = curl_get(API_URLS["static"])
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass

    def _static(self) -> dict:
        if not self.static_data:
            self.static_data = curl_get(API_URLS["static"])
        return self.static_data

    async def export_players(self, filepath: str) -> str:
        return write_csv(self._static()["elements"], filepath)

    async def export_teams(self, filepath: str) -> str:
        return write_csv(self._static()["teams"], filepath)

    async def export_gameweeks(self, filepath: str) -> str:
        return write_csv(self._static()["events"], filepath)

    async def export_fixtures(self, filepath: str) -> str:
        return write_csv(curl_get(API_URLS["fixtures"]), filepath)

    async def export_team_info(self, filepath: str) -> str:
        data = curl_get(API_URLS["user"].format(self.team_id))
        return write_csv([data], filepath)

    async def export_team_history(self, filepath: str) -> str:
        history = curl_get(API_URLS["user_history"].format(self.team_id))
        return write_csv(history.get("current", []), filepath)

    async def export_team_chips(self, filepath: str) -> str:
        history = curl_get(API_URLS["user_history"].format(self.team_id))
        chips = history.get("chips", [])
        return write_csv(chips, filepath, fieldnames=["name", "event", "time"])

    async def export_team_picks(self, filepath: str) -> str:
        all_picks = []
        for gw in self._static()["events"]:
            if gw["finished"] or gw["is_current"]:
                try:
                    picks_data = curl_get(API_URLS["user_picks"].format(self.team_id, gw["id"]))
                    for pick in picks_data.get("picks", []):
                        pick["gameweek"] = gw["id"]
                        pick["team_id"] = self.team_id
                        all_picks.append(pick)
                except Exception:
                    continue
        return write_csv(all_picks, filepath)

    async def export_player_gameweek_stats(self, filepath: str) -> str:
        players = self._static()["elements"]
        active = [p for p in players if p.get("minutes", 0) > 0]
        all_stats = []
        for i, player in enumerate(active):
            if i % 50 == 0:
                print(f"  Player stats: {i}/{len(active)}")
            try:
                summary = curl_get(API_URLS["player"].format(player["id"]))
                for stat in summary.get("history", []):
                    stat["player_id"] = player["id"]
                    all_stats.append(stat)
            except Exception:
                continue
        return write_csv(all_stats, filepath)

    async def export_all(self, data_dir: str = "data") -> Dict[str, str]:
        """Export all FPL data to CSV files."""
        results = {}
        print("  Fetching players...")
        results["players"] = await self.export_players(f"{data_dir}/players.csv")
        print("  Fetching teams...")
        results["teams"] = await self.export_teams(f"{data_dir}/teams.csv")
        print("  Fetching gameweeks...")
        results["gameweeks"] = await self.export_gameweeks(f"{data_dir}/gameweeks.csv")
        print("  Fetching fixtures...")
        results["fixtures"] = await self.export_fixtures(f"{data_dir}/fixtures.csv")
        print("  Fetching team info...")
        results["team_info"] = await self.export_team_info(f"{data_dir}/team_info.csv")
        print("  Fetching team history...")
        results["team_history"] = await self.export_team_history(f"{data_dir}/team_history.csv")
        print("  Fetching team chips...")
        results["team_chips"] = await self.export_team_chips(f"{data_dir}/team_chips.csv")
        print("  Fetching team picks...")
        results["team_picks"] = await self.export_team_picks(f"{data_dir}/team_picks.csv")
        print("  Fetching player gameweek stats (slow)...")
        results["player_gameweek_stats"] = await self.export_player_gameweek_stats(
            f"{data_dir}/player_gameweek_stats.csv"
        )
        return results


async def main():
    print("Downloading latest FPL data...\n")
    async with FPLDataExtractor() as extractor:
        results = await extractor.export_all("data")
    print("\nDone! Files created:")
    for key, filepath in results.items():
        size = os.path.getsize(filepath)
        print(f"  {key}: {filepath} ({size:,} bytes)")


if __name__ == "__main__":
    asyncio.run(main())
