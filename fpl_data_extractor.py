#!/usr/bin/env python3
"""
FPL Data Extractor

Downloads and exports Fantasy Premier League data to CSV files.
"""

import asyncio
import aiohttp
import csv
import os
from typing import Dict, List, Any
from constants import TEAM_ID, API_URLS
from utils import fetch, ssl_context, headers


class FPLDataExtractor:
    """Extracts FPL data and exports to CSV files."""

    def __init__(self):
        self.session = None
        self.static_data = None
        self.team_id = TEAM_ID

    async def __aenter__(self):
        """Async context manager entry."""
        self.session = aiohttp.ClientSession()
        # Load static data on initialization
        self.static_data = await fetch(self.session, API_URLS["static"])
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        if self.session:
            await self.session.close()

    async def export_players(self, filepath: str) -> str:
        """Export all players to CSV."""
        if not self.static_data:
            self.static_data = await fetch(self.session, API_URLS["static"])

        players = self.static_data["elements"]

        # Ensure directory exists
        os.makedirs(os.path.dirname(filepath) if os.path.dirname(filepath) else ".", exist_ok=True)

        if not players:
            raise ValueError("No player data available")

        # Write to CSV
        with open(filepath, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=players[0].keys())
            writer.writeheader()
            writer.writerows(players)

        return filepath

    async def export_teams(self, filepath: str) -> str:
        """Export all teams to CSV."""
        if not self.static_data:
            self.static_data = await fetch(self.session, API_URLS["static"])

        teams = self.static_data["teams"]

        os.makedirs(os.path.dirname(filepath) if os.path.dirname(filepath) else ".", exist_ok=True)

        with open(filepath, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=teams[0].keys())
            writer.writeheader()
            writer.writerows(teams)

        return filepath

    async def export_gameweeks(self, filepath: str) -> str:
        """Export gameweeks to CSV."""
        if not self.static_data:
            self.static_data = await fetch(self.session, API_URLS["static"])

        gameweeks = self.static_data["events"]

        os.makedirs(os.path.dirname(filepath) if os.path.dirname(filepath) else ".", exist_ok=True)

        with open(filepath, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=gameweeks[0].keys())
            writer.writeheader()
            writer.writerows(gameweeks)

        return filepath

    async def export_fixtures(self, filepath: str) -> str:
        """Export fixtures to CSV."""
        fixtures = await fetch(self.session, API_URLS["fixtures"])

        os.makedirs(os.path.dirname(filepath) if os.path.dirname(filepath) else ".", exist_ok=True)

        with open(filepath, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=fixtures[0].keys())
            writer.writeheader()
            writer.writerows(fixtures)

        return filepath

    async def export_player_gameweek_stats(self, filepath: str) -> str:
        """Export detailed player gameweek statistics."""
        if not self.static_data:
            self.static_data = await fetch(self.session, API_URLS["static"])

        players = self.static_data["elements"]

        os.makedirs(os.path.dirname(filepath) if os.path.dirname(filepath) else ".", exist_ok=True)

        all_stats = []

        # Fetch each player's history
        for player in players:
            player_id = player["id"]
            try:
                player_summary = await fetch(
                    self.session,
                    API_URLS["player"].format(player_id)
                )

                # Get history from player summary
                if "history" in player_summary:
                    for stat in player_summary["history"]:
                        stat["player_id"] = player_id
                        all_stats.append(stat)

            except Exception as e:
                # Skip players with no data
                continue

        if all_stats:
            with open(filepath, 'w', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=all_stats[0].keys())
                writer.writeheader()
                writer.writerows(all_stats)

        return filepath

    async def export_team_info(self, filepath: str) -> str:
        """Export team info to CSV."""
        team_data = await fetch(
            self.session,
            API_URLS["user"].format(self.team_id)
        )

        os.makedirs(os.path.dirname(filepath) if os.path.dirname(filepath) else ".", exist_ok=True)

        with open(filepath, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=team_data.keys())
            writer.writeheader()
            writer.writerow(team_data)

        return filepath

    async def export_team_history(self, filepath: str) -> str:
        """Export team history to CSV."""
        history_data = await fetch(
            self.session,
            API_URLS["user_history"].format(self.team_id)
        )

        # Get current gameweek history
        current_history = history_data.get("current", [])

        os.makedirs(os.path.dirname(filepath) if os.path.dirname(filepath) else ".", exist_ok=True)

        if current_history:
            with open(filepath, 'w', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=current_history[0].keys())
                writer.writeheader()
                writer.writerows(current_history)

        return filepath

    async def export_team_picks(self, filepath: str) -> str:
        """Export team picks for all gameweeks to CSV."""
        if not self.static_data:
            self.static_data = await fetch(self.session, API_URLS["static"])

        gameweeks = self.static_data["events"]

        all_picks = []

        # Get picks for each finished or current gameweek
        for gw in gameweeks:
            if gw["finished"] or gw["is_current"]:
                try:
                    picks_data = await fetch(
                        self.session,
                        API_URLS["user_picks"].format(self.team_id, gw["id"])
                    )

                    if "picks" in picks_data:
                        for pick in picks_data["picks"]:
                            pick["gameweek"] = gw["id"]
                            pick["team_id"] = self.team_id
                            all_picks.append(pick)

                except Exception as e:
                    # Skip gameweeks with no data
                    continue

        os.makedirs(os.path.dirname(filepath) if os.path.dirname(filepath) else ".", exist_ok=True)

        if all_picks:
            with open(filepath, 'w', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=all_picks[0].keys())
                writer.writeheader()
                writer.writerows(all_picks)

        return filepath

    async def export_team_chips(self, filepath: str) -> str:
        """Export team chips usage to CSV."""
        history_data = await fetch(
            self.session,
            API_URLS["user_history"].format(self.team_id)
        )

        chips = history_data.get("chips", [])

        os.makedirs(os.path.dirname(filepath) if os.path.dirname(filepath) else ".", exist_ok=True)

        # Write chips data (may be empty)
        with open(filepath, 'w', newline='', encoding='utf-8') as f:
            if chips:
                writer = csv.DictWriter(f, fieldnames=chips[0].keys())
                writer.writeheader()
                writer.writerows(chips)
            else:
                # Write empty file with standard headers
                writer = csv.DictWriter(f, fieldnames=["name", "event", "time"])
                writer.writeheader()

        return filepath

    async def export_all(self, data_dir: str = "data") -> Dict[str, str]:
        """Export all FPL data to CSV files."""
        results = {}

        # Export general FPL data
        results["players"] = await self.export_players(f"{data_dir}/players.csv")
        results["teams"] = await self.export_teams(f"{data_dir}/teams.csv")
        results["gameweeks"] = await self.export_gameweeks(f"{data_dir}/gameweeks.csv")
        results["fixtures"] = await self.export_fixtures(f"{data_dir}/fixtures.csv")

        # Export team-specific data
        results["team_info"] = await self.export_team_info(f"{data_dir}/team_info.csv")
        results["team_history"] = await self.export_team_history(f"{data_dir}/team_history.csv")
        results["team_picks"] = await self.export_team_picks(f"{data_dir}/team_picks.csv")
        results["team_chips"] = await self.export_team_chips(f"{data_dir}/team_chips.csv")

        # Export detailed player stats (this is the slowest part)
        results["player_gameweek_stats"] = await self.export_player_gameweek_stats(
            f"{data_dir}/player_gameweek_stats.csv"
        )

        return results


async def main():
    """Test the data extractor."""
    async with FPLDataExtractor() as extractor:
        print("Exporting all FPL data...")
        results = await extractor.export_all("data")
        print("\nExported files:")
        for key, filepath in results.items():
            print(f"  - {key}: {filepath}")


if __name__ == "__main__":
    asyncio.run(main())
