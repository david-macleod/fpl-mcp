#!/usr/bin/env python3
"""
FPL Data Extractor

Async module for downloading Fantasy Premier League data from the official API
and exporting to CSV files.
"""

import asyncio
import aiohttp
import csv
import os
from datetime import datetime

from constants import API_URLS, TEAM_ID
from utils import fetch


class FPLDataExtractor:
    """Async context manager for extracting FPL data to CSV files."""

    def __init__(self):
        self.session = None
        self.static_data = None

    async def __aenter__(self):
        self.session = aiohttp.ClientSession()
        self.static_data = await fetch(self.session, API_URLS['static'])
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.session.close()

    def _ensure_dir(self, filepath):
        """Ensure directory exists for filepath"""
        os.makedirs(os.path.dirname(filepath), exist_ok=True)

    async def export_players(self, filepath="data/players.csv"):
        """Export all players data to CSV"""
        self._ensure_dir(filepath)
        players = self.static_data.get('elements')
        if not players:
            raise ValueError("No players data found")

        fieldnames = list(players[0].keys())
        with open(filepath, 'w', newline='') as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(players)

        print(f"✓ Exported {len(players)} players to {filepath}")
        return filepath

    async def export_teams(self, filepath="data/teams.csv"):
        """Export all teams data to CSV"""
        self._ensure_dir(filepath)
        teams = self.static_data.get('teams')
        if not teams:
            raise ValueError("No teams data found")

        fieldnames = list(teams[0].keys())
        with open(filepath, 'w', newline='') as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(teams)

        print(f"✓ Exported {len(teams)} teams to {filepath}")
        return filepath

    async def export_gameweeks(self, filepath="data/gameweeks.csv"):
        """Export all gameweeks/events data to CSV"""
        self._ensure_dir(filepath)
        gameweeks = self.static_data.get('events')
        if not gameweeks:
            raise ValueError("No gameweeks data found")

        fieldnames = list(gameweeks[0].keys())
        with open(filepath, 'w', newline='') as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(gameweeks)

        print(f"✓ Exported {len(gameweeks)} gameweeks to {filepath}")
        return filepath

    async def export_fixtures(self, filepath="data/fixtures.csv"):
        """Export all fixtures data to CSV"""
        self._ensure_dir(filepath)
        fixtures = await fetch(self.session, API_URLS['fixtures'])
        if not fixtures:
            raise ValueError("No fixtures data found")

        fieldnames = list(fixtures[0].keys())
        with open(filepath, 'w', newline='') as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(fixtures)

        print(f"✓ Exported {len(fixtures)} fixtures to {filepath}")
        return filepath

    async def export_player_gameweek_stats(self, player_ids=None, filepath="data/player_gameweek_stats.csv"):
        """Export player gameweek performance data to CSV"""
        self._ensure_dir(filepath)

        if player_ids is None:
            player_ids = [p['id'] for p in self.static_data.get('elements', [])]

        print(f"Fetching gameweek stats for {len(player_ids)} players...")

        all_stats = []
        batch_size = 50

        for i in range(0, len(player_ids), batch_size):
            batch = player_ids[i:i + batch_size]
            tasks = [fetch(self.session, API_URLS['player'].format(player_id)) for player_id in batch]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for player_id, result in zip(batch, results):
                if isinstance(result, Exception):
                    print(f"Failed to fetch data for player {player_id}")
                    continue

                history = result.get('history', [])
                for stat in history:
                    stat['player_id'] = player_id
                    all_stats.append(stat)

            print(f"Processed {min(i + batch_size, len(player_ids))} players")

        if not all_stats:
            raise ValueError("No player gameweek stats found")

        fieldnames = list(all_stats[0].keys())
        with open(filepath, 'w', newline='') as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(all_stats)

        print(f"✓ Exported {len(all_stats)} gameweek stats to {filepath}")
        return filepath

    async def export_team_info(self, team_id=None, filepath="data/team_info.csv"):
        """Export team basic information to CSV"""
        self._ensure_dir(filepath)
        if team_id is None:
            team_id = TEAM_ID

        team_data = await fetch(self.session, API_URLS['user'].format(team_id))
        if not team_data:
            raise ValueError(f"No team data found for team ID {team_id}")

        team_list = [team_data]
        fieldnames = list(team_data.keys())
        with open(filepath, 'w', newline='') as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(team_list)

        print(f"✓ Exported team info for {team_data.get('player_first_name')} {team_data.get('player_last_name')}")
        return filepath

    async def export_team_history(self, team_id=None, filepath="data/team_history.csv"):
        """Export team season history to CSV"""
        self._ensure_dir(filepath)
        if team_id is None:
            team_id = TEAM_ID

        history_data = await fetch(self.session, API_URLS['user_history'].format(team_id))
        if not history_data:
            raise ValueError(f"No team history data found for team ID {team_id}")

        current_season = history_data.get('current', [])
        if not current_season:
            print(f"⚠ No current season history found for team {team_id}")
            return filepath

        fieldnames = list(current_season[0].keys())
        with open(filepath, 'w', newline='') as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(current_season)

        print(f"✓ Exported {len(current_season)} gameweeks of team history to {filepath}")
        return filepath

    async def export_team_chips(self, team_id=None, filepath="data/team_chips.csv"):
        """Export team chip usage history to CSV"""
        self._ensure_dir(filepath)
        if team_id is None:
            team_id = TEAM_ID

        history_data = await fetch(self.session, API_URLS['user_history'].format(team_id))
        if not history_data:
            raise ValueError(f"No team history data found for team ID {team_id}")

        chips_used = history_data.get('chips', [])
        if not chips_used:
            print(f"✓ No chips used yet for team {team_id} - creating empty chips file")
            with open(filepath, 'w', newline='') as csvfile:
                writer = csv.writer(csvfile)
                writer.writerow(['name', 'event', 'time'])
            return filepath

        fieldnames = list(chips_used[0].keys())
        with open(filepath, 'w', newline='') as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(chips_used)

        print(f"✓ Exported {len(chips_used)} chip usages to {filepath}")
        return filepath

    async def export_team_picks(self, team_id=None, filepath="data/team_picks.csv"):
        """Export team picks for all gameweeks including current to CSV"""
        self._ensure_dir(filepath)
        if team_id is None:
            team_id = TEAM_ID

        # Get completed gameweeks plus current
        completed_gameweeks = [gw['id'] for gw in self.static_data.get('events', []) if gw['finished']]
        current_gameweek = next((gw['id'] for gw in self.static_data.get('events', []) if gw['is_current']), None)

        gameweeks_to_fetch = completed_gameweeks.copy()
        if current_gameweek and current_gameweek not in gameweeks_to_fetch:
            gameweeks_to_fetch.append(current_gameweek)

        if not gameweeks_to_fetch:
            print("⚠ No gameweeks found to fetch picks")
            return filepath

        print(f"Fetching team picks for {len(gameweeks_to_fetch)} gameweeks (including current)...")

        all_picks = []
        for gameweek in gameweeks_to_fetch:
            try:
                picks_data = await fetch(self.session, API_URLS['user_picks'].format(team_id, gameweek))
                if picks_data:
                    for pick in picks_data.get('picks', []):
                        pick['gameweek'] = gameweek
                        pick['team_id'] = team_id
                        pick['active_chip'] = picks_data.get('active_chip')
                        pick['entry_history'] = picks_data.get('entry_history', {})
                        all_picks.append(pick)
            except Exception as e:
                print(f"Failed to fetch picks for gameweek {gameweek}")

        if not all_picks:
            print(f"⚠ No team picks found for team {team_id}")
            return filepath

        fieldnames = list(all_picks[0].keys())
        with open(filepath, 'w', newline='') as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(all_picks)

        print(f"✓ Exported {len(all_picks)} team picks to {filepath}")
        return filepath

    async def export_all(self, data_dir="data"):
        """Export all main datasets to CSV files"""
        print("Starting FPL data export...")

        results = {}

        results['players'] = await self.export_players(f"{data_dir}/players.csv")
        results['teams'] = await self.export_teams(f"{data_dir}/teams.csv")
        results['gameweeks'] = await self.export_gameweeks(f"{data_dir}/gameweeks.csv")
        results['fixtures'] = await self.export_fixtures(f"{data_dir}/fixtures.csv")

        print("\nFetching detailed player gameweek stats (this may take a few minutes)...")
        results['player_stats'] = await self.export_player_gameweek_stats(filepath=f"{data_dir}/player_gameweek_stats.csv")

        try:
            print(f"\nFetching team data for team ID {TEAM_ID}...")
            results['team_info'] = await self.export_team_info(filepath=f"{data_dir}/team_info.csv")
            results['team_history'] = await self.export_team_history(filepath=f"{data_dir}/team_history.csv")
            results['team_chips'] = await self.export_team_chips(filepath=f"{data_dir}/team_chips.csv")
            results['team_picks'] = await self.export_team_picks(filepath=f"{data_dir}/team_picks.csv")
        except Exception as e:
            print(f"⚠ Failed to fetch some team data: {e}")

        print(f"\n✓ All exports completed to {data_dir}/ directory")
        return results


async def export_fpl_data(data_dir="data"):
    """Export all FPL data to CSV files"""
    async with FPLDataExtractor() as extractor:
        return await extractor.export_all(data_dir)


async def main():
    async with FPLDataExtractor() as extractor:
        await extractor.export_all("data")


if __name__ == "__main__":
    asyncio.run(main())
