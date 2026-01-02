# FPL MCP Server

A Model Context Protocol (MCP) server that provides tools for downloading and accessing Fantasy Premier League data to help with optimal team selection.

## Features

- **download_latest_data**: Download the latest FPL data to CSV files
- **get_data_summary**: Get summary of available data including row/column counts  
- **get_current_gameweek**: Get information about the current gameweek
- **get_top_players**: Get top performing players by various metrics

## Installation

1. Install Python dependencies:
```bash
pip3 install aiohttp pandas
```

2. For MCP support, you'll need to install the MCP Python SDK when available, or use the server directly with Claude Code.

## Usage

### As MCP Server

The server can be used as an MCP server by running:
```bash
python3 fpl_server.py
```

### Direct Data Export

You can also use the data extractor directly:
```python
from fpl_data_extractor import export_fpl_data
import asyncio

# Export all FPL data to CSV files
asyncio.run(export_fpl_data("data"))
```

## Data Structure

The server provides access to 5 key datasets:

1. **players.csv** (101 columns) - Complete player data including stats, pricing, and performance
2. **teams.csv** (21 columns) - Team information and strength ratings  
3. **gameweeks.csv** (29 columns) - Gameweek schedule and statistics
4. **fixtures.csv** (17 columns) - All Premier League fixtures with results
5. **player_gameweek_stats.csv** (42 columns) - Historical player performance by gameweek

## Available Tools

### download_latest_data
Downloads the latest Fantasy Premier League data to CSV files.

**Parameters:**
- `data_dir` (string, optional): Directory to save CSV files (default: "data")
- `include_player_stats` (boolean, optional): Include detailed player gameweek stats (default: true)

### get_data_summary  
Get a summary of available FPL data including row counts and columns.

**Parameters:**
- `data_dir` (string, optional): Directory containing CSV files (default: "data")

### get_current_gameweek
Get information about the current gameweek including deadline and scores.

### get_top_players
Get top performing players by various metrics.

**Parameters:**
- `metric` (string): Metric to sort by (total_points, points_per_game, form, selected_by_percent, value_season)
- `position` (string): Filter by position (all, GK, DEF, MID, FWD) 
- `limit` (integer): Number of players to return (1-50, default: 10)

## Example Usage

```python
# Download latest data
await download_latest_data({"data_dir": "fpl_data", "include_player_stats": True})

# Get top 15 midfielders by total points  
await get_top_players({"metric": "total_points", "position": "MID", "limit": 15})

# Get current gameweek info
await get_current_gameweek({})
```

## Data Sources

All data is sourced from the official Fantasy Premier League API:
- https://fantasy.premierleague.com/api/bootstrap-static/
- https://fantasy.premierleague.com/api/fixtures/  
- https://fantasy.premierleague.com/api/element-summary/{player_id}/