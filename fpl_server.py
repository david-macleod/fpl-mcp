#!/usr/bin/env python3
"""
FPL MCP Server

A Model Context Protocol server that provides tools for downloading and accessing
Fantasy Premier League data for optimal team selection.
"""

import asyncio
import json
import sys
from typing import Any, Sequence
import logging

from mcp.server import Server
from mcp.server.models import InitializationOptions
from mcp.server.stdio import stdio_server
from mcp.types import (
    Resource,
    Tool,
    TextContent,
    ImageContent,
    EmbeddedResource,
    LoggingLevel
)

from fpl_data_extractor import FPLDataExtractor

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("fpl-server")

server = Server("fpl-server")

@server.list_tools()
async def handle_list_tools() -> list[Tool]:
    """
    List available tools.
    Each tool specifies its arguments using JSON Schema validation.
    """
    return [
        Tool(
            name="download_latest_data",
            description="Download the latest Fantasy Premier League data to CSV files",
            inputSchema={
                "type": "object",
                "properties": {
                    "data_dir": {
                        "type": "string",
                        "description": "Directory to save the CSV files (default: 'data')",
                        "default": "data"
                    },
                    "include_player_stats": {
                        "type": "boolean", 
                        "description": "Whether to include detailed player gameweek stats (takes longer)",
                        "default": True
                    }
                },
                "required": []
            },
        ),
        Tool(
            name="get_team_summary",
            description="Get a summary of your current FPL team including rankings, recent performance, and current squad",
            inputSchema={
                "type": "object",
                "properties": {
                    "data_dir": {
                        "type": "string",
                        "description": "Directory containing the CSV files (default: 'data')",
                        "default": "data"
                    }
                },
                "required": []
            },
        )
    ]

@server.call_tool()
async def handle_call_tool(name: str, arguments: dict[str, Any] | None) -> list[TextContent]:
    """
    Handle tool execution requests.
    Tools can modify server state and notify clients of changes.
    """
    try:
        if name == "download_latest_data":
            return await handle_download_latest_data(arguments or {})
        elif name == "get_team_summary":
            return await handle_get_team_summary(arguments or {})
        else:
            raise ValueError(f"Unknown tool: {name}")
    except Exception as e:
        logger.error(f"Error executing tool {name}: {e}")
        return [TextContent(type="text", text=f"Error: {str(e)}")]

async def handle_download_latest_data(arguments: dict[str, Any]) -> list[TextContent]:
    """Download the latest FPL data to CSV files."""
    data_dir = arguments.get("data_dir", "data")
    include_player_stats = arguments.get("include_player_stats", True)
    
    try:
        async with FPLDataExtractor() as extractor:
            if include_player_stats:
                results = await extractor.export_all(data_dir)
                message = f"✓ Downloaded all FPL data to {data_dir}/ directory:\n"
                for key, filepath in results.items():
                    message += f"  - {key}: {filepath}\n"
            else:
                # Download only basic tables (faster)
                results = {}
                results["players"] = await extractor.export_players(f"{data_dir}/players.csv")
                results["teams"] = await extractor.export_teams(f"{data_dir}/teams.csv")
                results["gameweeks"] = await extractor.export_gameweeks(f"{data_dir}/gameweeks.csv")
                results["fixtures"] = await extractor.export_fixtures(f"{data_dir}/fixtures.csv")
                
                message = f"✓ Downloaded basic FPL data to {data_dir}/ directory:\n"
                for key, filepath in results.items():
                    message += f"  - {key}: {filepath}\n"
                message += "\nNote: Player gameweek stats not included (set include_player_stats=true for complete data)"
        
        return [TextContent(type="text", text=message)]
        
    except Exception as e:
        error_msg = f"Failed to download FPL data: {str(e)}"
        logger.error(error_msg)
        return [TextContent(type="text", text=error_msg)]

async def handle_get_team_summary(arguments: dict[str, Any]) -> list[TextContent]:
    """Get a summary of the current FPL team."""
    import os
    import pandas as pd
    from constants import TEAM_ID
    
    data_dir = arguments.get("data_dir", "data")
    
    # Check if required files exist
    required_files = ["team_info.csv", "team_history.csv", "team_chips.csv", "team_picks.csv", "players.csv"]
    missing_files = []
    
    for filename in required_files:
        filepath = os.path.join(data_dir, filename)
        if not os.path.exists(filepath):
            missing_files.append(filename)
    
    if missing_files:
        return [TextContent(type="text", text=f"Missing data files: {', '.join(missing_files)}. Run download_latest_data first.")]
    
    try:
        # Load data
        team_info = pd.read_csv(os.path.join(data_dir, "team_info.csv"))
        team_history = pd.read_csv(os.path.join(data_dir, "team_history.csv"))
        team_chips = pd.read_csv(os.path.join(data_dir, "team_chips.csv"))
        team_picks = pd.read_csv(os.path.join(data_dir, "team_picks.csv"))
        players = pd.read_csv(os.path.join(data_dir, "players.csv"))
        
        # Extract team info
        team_name = team_info.iloc[0]["name"]
        manager_name = f"{team_info.iloc[0]['player_first_name']} {team_info.iloc[0]['player_last_name']}"
        overall_rank = team_info.iloc[0]["summary_overall_rank"]
        total_points = team_info.iloc[0]["summary_overall_points"]
        current_event = team_info.iloc[0]["current_event"]
        
        # Get latest gameweek performance
        latest_gw = team_history.iloc[-1] if len(team_history) > 0 else None
        
        # Get current squad (latest gameweek picks)
        if len(team_picks) > 0:
            # Use the most recent gameweek we have data for
            latest_gameweek = team_picks["gameweek"].max()
            current_squad = team_picks[team_picks["gameweek"] == latest_gameweek]
        else:
            current_squad = pd.DataFrame()
        
        # Build summary
        summary = f"🏆 FPL Team Summary\n"
        summary += f"=" * 50 + "\n\n"
        
        # Basic info
        summary += f"👤 Manager: {manager_name}\n"
        summary += f"⚽ Team Name: {team_name}\n"
        summary += f"🆔 Team ID: {TEAM_ID}\n\n"
        
        # Performance
        summary += f"📊 Season Performance:\n"
        summary += f"  • Total Points: {total_points:,}\n"
        summary += f"  • Overall Rank: {overall_rank:,}\n"
        
        if latest_gw is not None:
            summary += f"  • Latest GW{int(latest_gw['event'])}: {int(latest_gw['points'])} points\n"
            summary += f"  • GW Rank: {int(latest_gw['rank']):,}\n"
            summary += f"  • Team Value: £{latest_gw['value']/10:.1f}m\n"
            summary += f"  • Bank: £{latest_gw['bank']/10:.1f}m\n\n"
        
        # Current squad analysis using proper ID-based matching
        if len(current_squad) > 0:
            position_names = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}
            status_icons = {'a': '✅', 'i': '🚑', 'd': '⚠️', 's': '🟥', 'u': '❌'}
            
            summary += f"👥 Current Squad (GW{latest_gameweek}):\n\n"
            
            # Merge squad picks with player metadata using proper ID joins
            squad_with_metadata = current_squad.merge(
                players, 
                left_on='element', 
                right_on='id',
                how='left',
                suffixes=('_pick', '_player')
            )
            
            # Group by position using the element_type from merged data
            for pos_id, pos_name in position_names.items():
                pos_players = squad_with_metadata[squad_with_metadata['element_type_player'] == pos_id].copy()
                pos_players = pos_players.sort_values('position')
                
                if len(pos_players) > 0:
                    summary += f"  {pos_name}:\n"
                    for _, player in pos_players.iterrows():
                        captain_indicator = " (C)" if player['is_captain'] else " (VC)" if player['is_vice_captain'] else ""
                        status_icon = status_icons.get(player['status'], '❓')
                        cost = player['now_cost'] / 10 if pd.notna(player['now_cost']) else 0
                        summary += f"    {status_icon} {player['web_name']} - £{cost:.1f}m{captain_indicator}\n"
                    summary += "\n"
        
        # Recent form
        if len(team_history) >= 3:
            recent_points = team_history.tail(3)['points'].tolist()
            summary += f"📈 Recent Form (last 3 GWs): {' → '.join(map(str, recent_points))}\n\n"
        
        # Team value and transfers
        if latest_gw is not None:
            total_value = (latest_gw['value'] + latest_gw['bank']) / 10

            # Calculate free transfers available (FPL rules)
            # Key rules:
            # 1. You get 1 FT per gameweek, can accumulate up to max 5
            # 2. AFCON rule: After GW15 deadline, everyone is topped up to 5 FTs
            # 3. Unused FTs roll over (capped at 5)

            AFCON_TOPUP_GW = 15  # After GW15 deadline, everyone gets 5 FTs
            MAX_FREE_TRANSFERS = 5

            # Get transfers made each gameweek from history
            transfers_by_gw = dict(zip(team_history['event'].astype(int), team_history['event_transfers'].astype(int)))

            # Calculate FTs available for next gameweek
            free_transfers = 1  # Start with 1 FT after GW1
            started_event = int(team_info.iloc[0].get('started_event', 1))

            for gw in range(started_event, current_event + 1):
                if gw == AFCON_TOPUP_GW + 1:  # After GW15 deadline (i.e., for GW16)
                    free_transfers = MAX_FREE_TRANSFERS  # AFCON top-up

                transfers_used = transfers_by_gw.get(gw, 0)
                free_transfers = free_transfers - transfers_used

                # Add 1 FT for next week (capped at 5)
                # This applies to all GWs since we want FTs available for the UPCOMING gameweek
                free_transfers = min(MAX_FREE_TRANSFERS, free_transfers + 1)
            
            total_transfers_made = int(team_history['event_transfers'].sum())

            summary += f"💰 Team Economics:\n"
            summary += f"  • Total Value: £{total_value:.1f}m\n"
            summary += f"  • Total Transfers Made: {total_transfers_made}\n"
            summary += f"  • Free Transfers Available: {free_transfers}\n"
            summary += f"  • Last GW Transfer Cost: {int(latest_gw.get('event_transfers_cost', 0))} pts\n\n"
        
        # Availability issues summary (using proper ID-based analysis)
        if len(current_squad) > 0:
            squad_with_metadata = current_squad.merge(players, left_on='element', right_on='id', how='left', suffixes=('_pick', '_player'))
            availability_issues = squad_with_metadata[squad_with_metadata['status'] != 'a']
            
            if len(availability_issues) > 0:
                summary += f"⚠️  Squad Issues:\n"
                for _, player in availability_issues.iterrows():
                    status_map = {'i': 'Injured', 'd': 'Doubtful', 's': 'Suspended', 'u': 'Unavailable'}
                    status_desc = status_map.get(player['status'], 'Unknown')
                    position_map = {1: 'GK', 2: 'DEF', 3: 'MID', 4: 'FWD'}
                    position = position_map.get(player['element_type_player'], 'Unknown')
                    summary += f"  🔄 {player['web_name']} ({position}) - {status_desc}\n"
                summary += f"\n"
        
        # Chip usage analysis - now with actual historical data!
        try:
            summary += f"🃏 Chip Status:\n"
            
            # Check chip usage history
            if len(team_chips) > 0:
                summary += f"  Used chips:\n"
                for _, chip in team_chips.iterrows():
                    if pd.notna(chip.get('name')):
                        chip_name = chip['name']
                        event = f"GW{chip['event']}" if pd.notna(chip.get('event')) else "Unknown GW"
                        summary += f"    • {chip_name.title()} ({event})\n"
                summary += f"\n"
            else:
                summary += f"  ✅ No chips used yet this season\n\n"
            
            # Standard FPL chips available each season
            all_chips = {
                'wildcard': 'Wildcard (2 per season: GW2-19, GW20-38)',
                'bboost': 'Bench Boost',
                'freehit': 'Free Hit', 
                'tripec': 'Triple Captain'
            }
            
            # Count used chips
            used_chip_names = set()
            if len(team_chips) > 0:
                for _, chip in team_chips.iterrows():
                    if pd.notna(chip.get('name')):
                        used_chip_names.add(chip['name'].lower())
            
            # Show remaining chips
            summary += f"  Remaining chips:\n"
            for chip_code, chip_name in all_chips.items():
                if chip_code not in used_chip_names:
                    summary += f"    • {chip_name}\n"
                    
            if len(used_chip_names) == len(all_chips):
                summary += f"    • All chips have been used\n"
                
        except Exception as e:
            summary += f"🃏 Chip Status: Unable to determine (error: {str(e)})\n"
        
        return [TextContent(type="text", text=summary)]
        
    except Exception as e:
        error_msg = f"Error generating team summary: {str(e)}"
        logger.error(error_msg)
        return [TextContent(type="text", text=error_msg)]

async def main():
    # Run the server using stdin/stdout streams
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="fpl-server",
                server_version="0.1.0",
                capabilities=server.get_capabilities(
                    notification_options=type('NotificationOptions', (), {
                        'tools_changed': False,
                        'prompts_changed': False, 
                        'resources_changed': False,
                        'roots_changed': False
                    })(),
                    experimental_capabilities={}
                )
            )
        )

if __name__ == "__main__":
    asyncio.run(main())