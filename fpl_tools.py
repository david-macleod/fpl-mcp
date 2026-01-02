#!/usr/bin/env python3
"""
FPL Tools - Standalone version for testing FPL data functionality
"""

import asyncio
import json
import os
import csv
import pandas as pd
from fpl_data_extractor import FPLDataExtractor

class FPLTools:
    """Standalone FPL tools for testing functionality"""
    
    def __init__(self, data_dir="data"):
        self.data_dir = data_dir
    
    async def download_latest_data(self, include_player_stats=True):
        """Download the latest FPL data to CSV files."""
        try:
            async with FPLDataExtractor() as extractor:
                if include_player_stats:
                    results = await extractor.export_all(self.data_dir)
                    message = f"✓ Downloaded all FPL data to {self.data_dir}/ directory:\n"
                    for key, filepath in results.items():
                        message += f"  - {key}: {filepath}\n"
                else:
                    # Download only basic tables (faster)
                    results = {}
                    results["players"] = await extractor.export_players(f"{self.data_dir}/players.csv")
                    results["teams"] = await extractor.export_teams(f"{self.data_dir}/teams.csv")
                    results["gameweeks"] = await extractor.export_gameweeks(f"{self.data_dir}/gameweeks.csv")
                    results["fixtures"] = await extractor.export_fixtures(f"{self.data_dir}/fixtures.csv")
                    
                    message = f"✓ Downloaded basic FPL data to {self.data_dir}/ directory:\n"
                    for key, filepath in results.items():
                        message += f"  - {key}: {filepath}\n"
                    message += "\nNote: Player gameweek stats not included"
            
            print(message)
            return results
            
        except Exception as e:
            error_msg = f"Failed to download FPL data: {str(e)}"
            print(error_msg)
            raise

    def get_data_summary(self):
        """Get a summary of available FPL data."""
        if not os.path.exists(self.data_dir):
            return f"Data directory '{self.data_dir}' not found. Run download_latest_data first."
        
        summary = f"FPL Data Summary ({self.data_dir}/):\n\n"
        
        expected_files = {
            "players.csv": "Player information and season stats",
            "teams.csv": "Team information and league standings", 
            "gameweeks.csv": "Gameweek schedule and statistics",
            "fixtures.csv": "All Premier League fixtures",
            "player_gameweek_stats.csv": "Historical player performance by gameweek"
        }
        
        for filename, description in expected_files.items():
            filepath = os.path.join(self.data_dir, filename)
            if os.path.exists(filepath):
                try:
                    with open(filepath, 'r', encoding='utf-8') as f:
                        reader = csv.reader(f)
                        headers = next(reader)
                        row_count = sum(1 for row in reader)
                    
                    summary += f"✓ {filename}\n"
                    summary += f"  {description}\n"
                    summary += f"  Rows: {row_count}, Columns: {len(headers)}\n\n"
                except Exception as e:
                    summary += f"✗ {filename} - Error reading file: {e}\n\n"
            else:
                summary += f"✗ {filename} - Not found\n\n"
        
        print(summary)
        return summary

    async def get_current_gameweek(self):
        """Get current gameweek information."""
        try:
            async with FPLDataExtractor() as extractor:
                static_data = extractor.static_data
                
                current_gw = None
                for event in static_data["events"]:
                    if event["is_current"]:
                        current_gw = event
                        break
                
                if current_gw:
                    message = f"Current Gameweek: {current_gw['name']}\n"
                    message += f"Deadline: {current_gw['deadline_time']}\n"
                    message += f"Finished: {'Yes' if current_gw['finished'] else 'No'}\n"
                    if current_gw['finished']:
                        message += f"Average Score: {current_gw['average_entry_score']}\n"
                        message += f"Highest Score: {current_gw['highest_score']}\n"
                else:
                    message = "No current gameweek found (season may be over)"
            
            print(message)
            return message
            
        except Exception as e:
            error_msg = f"Error getting current gameweek: {str(e)}"
            print(error_msg)
            return error_msg

    def get_top_players(self, metric="total_points", position="all", limit=10):
        """Get top players by specified metric."""
        players_file = os.path.join(self.data_dir, "players.csv")
        
        if not os.path.exists(players_file):
            message = f"Players data not found at {players_file}. Run download_latest_data first."
            print(message)
            return message
        
        try:
            # Read players data
            df = pd.read_csv(players_file)
            
            # Filter by position if specified
            if position != "all":
                position_map = {"GK": 1, "DEF": 2, "MID": 3, "FWD": 4}
                if position in position_map:
                    df = df[df["element_type"] == position_map[position]]
            
            # Sort by metric
            df = df.sort_values(metric, ascending=False)
            
            # Get top players
            top_players = df.head(limit)
            
            # Format output
            position_names = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}
            
            message = f"Top {limit} players by {metric}"
            if position != "all":
                message += f" ({position})"
            message += ":\n\n"
            
            for i, (_, player) in enumerate(top_players.iterrows(), 1):
                pos = position_names.get(player["element_type"], "?")
                cost = player["now_cost"] / 10.0  # Convert from 0.1m units
                
                message += f"{i:2d}. {player['web_name']} ({pos})\n"
                message += f"    {metric.replace('_', ' ').title()}: {player[metric]}"
                if metric != "now_cost":
                    message += f" | Cost: £{cost}m"
                else:
                    message += f" (£{cost}m)"
                message += f" | Owned: {player['selected_by_percent']}%\n"
            
            print(message)
            return message
            
        except Exception as e:
            error_msg = f"Error getting top players: {str(e)}"
            print(error_msg)
            return error_msg

# CLI interface for testing
async def main():
    tools = FPLTools()
    
    print("FPL Tools - Testing Interface")
    print("=" * 40)
    
    # Test 1: Download data
    print("\n1. Downloading basic FPL data...")
    await tools.download_latest_data(include_player_stats=False)
    
    # Test 2: Data summary
    print("\n2. Getting data summary...")
    tools.get_data_summary()
    
    # Test 3: Current gameweek
    print("\n3. Getting current gameweek...")
    await tools.get_current_gameweek()
    
    # Test 4: Top players
    print("\n4. Getting top 5 players by total points...")
    tools.get_top_players(metric="total_points", limit=5)
    
    print("\n5. Getting top 5 midfielders by form...")
    tools.get_top_players(metric="form", position="MID", limit=5)

if __name__ == "__main__":
    asyncio.run(main())