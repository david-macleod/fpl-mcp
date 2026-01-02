#!/usr/bin/env python3
"""
FPL Data Extractor

Downloads and exports Fantasy Premier League data to CSV files.
"""

import asyncio
import os
import aiohttp
import pandas as pd
from typing import Dict, Optional
from constants import API_URLS, TEAM_ID


class FPLDataExtractor:
    """Downloads FPL data and exports to CSV files"""

    def __init__(self):
        """Initialize the data extractor"""
        self.session: Optional[aiohttp.ClientSession] = None
        self.static_data: Optional[Dict] = None
        self.fixtures_data: Optional[Dict] = None

    async def __aenter__(self):
        """Async context manager entry"""
        self.session = aiohttp.ClientSession()
        # Pre-fetch static data
        await self._fetch_static_data()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit"""
        if self.session:
            await self.session.close()

    async def _fetch_static_data(self):
        """Fetch the bootstrap-static data"""
        url = API_URLS["static"]
        async with self.session.get(url) as response:
            self.static_data = await response.json()

    async def _fetch_fixtures_data(self):
        """Fetch fixtures data"""
        url = API_URLS["fixtures"]
        async with self.session.get(url) as response:
            self.fixtures_data = await response.json()

    async def _fetch_team_data(self, team_id: int):
        """Fetch team-specific data including picks and history"""
        user_url = API_URLS["user"].format(team_id)
        history_url = API_URLS["user_history"].format(team_id)

        async with self.session.get(user_url) as response:
            user_data = await response.json()

        async with self.session.get(history_url) as response:
            history_data = await response.json()

        return user_data, history_data

    async def _fetch_player_gameweek_stats(self, player_id: int):
        """Fetch detailed gameweek stats for a player"""
        url = API_URLS["player"].format(player_id)
        async with self.session.get(url) as response:
            return await response.json()

    def _ensure_dir(self, filepath: str):
        """Ensure directory exists for filepath"""
        directory = os.path.dirname(filepath)
        if directory and not os.path.exists(directory):
            os.makedirs(directory)

    async def export_players(self, filepath: str) -> str:
        """Export players data to CSV"""
        self._ensure_dir(filepath)

        # Extract players from static data
        players_data = self.static_data["elements"]
        df = pd.DataFrame(players_data)

        # Save to CSV
        df.to_csv(filepath, index=False)
        return filepath

    async def export_teams(self, filepath: str) -> str:
        """Export teams data to CSV"""
        self._ensure_dir(filepath)

        # Extract teams from static data
        teams_data = self.static_data["teams"]
        df = pd.DataFrame(teams_data)

        # Save to CSV
        df.to_csv(filepath, index=False)
        return filepath

    async def export_gameweeks(self, filepath: str) -> str:
        """Export gameweeks (events) data to CSV"""
        self._ensure_dir(filepath)

        # Extract events from static data
        events_data = self.static_data["events"]
        df = pd.DataFrame(events_data)

        # Save to CSV
        df.to_csv(filepath, index=False)
        return filepath

    async def export_fixtures(self, filepath: str) -> str:
        """Export fixtures data to CSV"""
        self._ensure_dir(filepath)

        # Fetch fixtures if not already fetched
        if self.fixtures_data is None:
            await self._fetch_fixtures_data()

        df = pd.DataFrame(self.fixtures_data)

        # Save to CSV
        df.to_csv(filepath, index=False)
        return filepath

    async def export_team_info(self, filepath: str, team_id: int = TEAM_ID) -> str:
        """Export team info to CSV"""
        self._ensure_dir(filepath)

        user_data, history_data = await self._fetch_team_data(team_id)

        # Create DataFrame with team info
        df = pd.DataFrame([user_data])

        # Save to CSV
        df.to_csv(filepath, index=False)
        return filepath

    async def export_team_history(self, filepath: str, team_id: int = TEAM_ID) -> str:
        """Export team history to CSV"""
        self._ensure_dir(filepath)

        user_data, history_data = await self._fetch_team_data(team_id)

        # Extract current season history
        current_history = history_data.get("current", [])
        df = pd.DataFrame(current_history)

        # Save to CSV
        df.to_csv(filepath, index=False)
        return filepath

    async def export_team_chips(self, filepath: str, team_id: int = TEAM_ID) -> str:
        """Export team chips usage to CSV"""
        self._ensure_dir(filepath)

        user_data, history_data = await self._fetch_team_data(team_id)

        # Extract chips data
        chips_data = history_data.get("chips", [])
        df = pd.DataFrame(chips_data) if chips_data else pd.DataFrame(columns=['name', 'time', 'event'])

        # Save to CSV
        df.to_csv(filepath, index=False)
        return filepath

    async def export_team_picks(self, filepath: str, team_id: int = TEAM_ID) -> str:
        """Export team picks for all gameweeks to CSV"""
        self._ensure_dir(filepath)

        # Get current gameweek
        current_event = next((event["id"] for event in self.static_data["events"] if event.get("is_current")), 1)

        all_picks = []

        # Fetch picks for each gameweek
        for gw in range(1, current_event + 1):
            picks_url = API_URLS["user_picks"].format(team_id, gw)
            try:
                async with self.session.get(picks_url) as response:
                    picks_data = await response.json()

                    # Add gameweek number to each pick
                    for pick in picks_data.get("picks", []):
                        pick["gameweek"] = gw
                        all_picks.append(pick)
            except Exception as e:
                print(f"Warning: Could not fetch picks for GW{gw}: {e}")
                continue

        df = pd.DataFrame(all_picks)

        # Save to CSV
        df.to_csv(filepath, index=False)
        return filepath

    async def export_player_gameweek_stats(self, filepath: str) -> str:
        """Export detailed player gameweek stats to CSV"""
        self._ensure_dir(filepath)

        all_stats = []
        players = self.static_data["elements"]

        print(f"Fetching gameweek stats for {len(players)} players...")

        # Fetch stats for each player
        for i, player in enumerate(players, 1):
            player_id = player["id"]

            if i % 50 == 0:
                print(f"  Progress: {i}/{len(players)} players...")

            try:
                player_data = await self._fetch_player_gameweek_stats(player_id)

                # Extract history data
                for gw_stat in player_data.get("history", []):
                    gw_stat["player_id"] = player_id
                    gw_stat["player_name"] = player["web_name"]
                    all_stats.append(gw_stat)

            except Exception as e:
                print(f"Warning: Could not fetch stats for player {player_id}: {e}")
                continue

        df = pd.DataFrame(all_stats)

        # Save to CSV
        df.to_csv(filepath, index=False)
        print(f"✓ Exported {len(all_stats)} player gameweek records")
        return filepath

    async def export_all(self, data_dir: str = "data") -> Dict[str, str]:
        """Export all data to CSV files"""
        results = {}

        print("📥 Downloading FPL data...")

        # Export basic data (fast)
        results["players"] = await self.export_players(f"{data_dir}/players.csv")
        print(f"  ✓ Players exported")

        results["teams"] = await self.export_teams(f"{data_dir}/teams.csv")
        print(f"  ✓ Teams exported")

        results["gameweeks"] = await self.export_gameweeks(f"{data_dir}/gameweeks.csv")
        print(f"  ✓ Gameweeks exported")

        results["fixtures"] = await self.export_fixtures(f"{data_dir}/fixtures.csv")
        print(f"  ✓ Fixtures exported")

        # Export team-specific data
        results["team_info"] = await self.export_team_info(f"{data_dir}/team_info.csv")
        print(f"  ✓ Team info exported")

        results["team_history"] = await self.export_team_history(f"{data_dir}/team_history.csv")
        print(f"  ✓ Team history exported")

        results["team_chips"] = await self.export_team_chips(f"{data_dir}/team_chips.csv")
        print(f"  ✓ Team chips exported")

        results["team_picks"] = await self.export_team_picks(f"{data_dir}/team_picks.csv")
        print(f"  ✓ Team picks exported")

        # Export detailed player stats (slow)
        results["player_gameweek_stats"] = await self.export_player_gameweek_stats(
            f"{data_dir}/player_gameweek_stats.csv"
        )

        print(f"\n✅ All data exported to {data_dir}/")
        return results


# Convenience function for direct use
async def export_fpl_data(data_dir: str = "data", include_player_stats: bool = True):
    """Export FPL data to CSV files"""
    async with FPLDataExtractor() as extractor:
        if include_player_stats:
            return await extractor.export_all(data_dir)
        else:
            results = {}
            results["players"] = await extractor.export_players(f"{data_dir}/players.csv")
            results["teams"] = await extractor.export_teams(f"{data_dir}/teams.csv")
            results["gameweeks"] = await extractor.export_gameweeks(f"{data_dir}/gameweeks.csv")
            results["fixtures"] = await extractor.export_fixtures(f"{data_dir}/fixtures.csv")
            return results


if __name__ == "__main__":
    # Test the extractor
    asyncio.run(export_fpl_data("data", include_player_stats=False))
