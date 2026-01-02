#!/usr/bin/env python3
"""
FPL Analyzer - Reusable analysis module for Fantasy Premier League optimization

This module provides core functionality for analyzing FPL data and making
optimal team decisions.
"""

import pandas as pd
import numpy as np
from typing import Dict, List, Tuple, Optional
from constants import TEAM_ID

class FPLAnalyzer:
    """Main analysis class for FPL data"""
    
    def __init__(self, data_dir: str = "data"):
        """Initialize analyzer with data directory"""
        self.data_dir = data_dir
        self.players = None
        self.teams = None
        self.fixtures = None
        self.gameweeks = None
        self.team_picks = None
        self.team_history = None
        self.player_stats = None
        
    def load_data(self):
        """Load all CSV data into memory"""
        print("📊 Loading FPL data...")
        self.players = pd.read_csv(f"{self.data_dir}/players.csv")
        self.teams = pd.read_csv(f"{self.data_dir}/teams.csv") 
        self.fixtures = pd.read_csv(f"{self.data_dir}/fixtures.csv")
        self.gameweeks = pd.read_csv(f"{self.data_dir}/gameweeks.csv")
        self.team_picks = pd.read_csv(f"{self.data_dir}/team_picks.csv")
        self.team_history = pd.read_csv(f"{self.data_dir}/team_history.csv")
        self.player_stats = pd.read_csv(f"{self.data_dir}/player_gameweek_stats.csv")
        print("✅ Data loaded successfully")
        
    def get_current_gameweek(self) -> int:
        """Get current gameweek number"""
        current = self.gameweeks[self.gameweeks['is_current']]
        return current['id'].iloc[0] if len(current) > 0 else 4
        
    def get_next_gameweek(self) -> int:
        """Get next gameweek number for planning"""
        current_gw = self.get_current_gameweek()
        return current_gw + 1 if current_gw < 38 else current_gw
        
    def get_current_squad(self) -> pd.DataFrame:
        """Get current team selection with player details"""
        if self.team_picks is None or self.players is None:
            raise ValueError("Data not loaded. Call load_data() first.")
            
        # Get most recent picks
        latest_gw = self.team_picks['gameweek'].max()
        current_picks = self.team_picks[self.team_picks['gameweek'] == latest_gw].copy()
        
        # Merge with player data
        squad = current_picks.merge(
            self.players[['id', 'web_name', 'now_cost', 'total_points', 'form', 'team', 'element_type', 'status']], 
            left_on='element', right_on='id'
        )
        
        # Add team names
        squad = squad.merge(
            self.teams[['id', 'short_name']], 
            left_on='team', right_on='id', 
            suffixes=('', '_team')
        )
        
        return squad.sort_values(['element_type_x', 'position'])
        
    def analyze_squad_value(self) -> Dict:
        """Analyze current squad value and bank"""
        squad = self.get_current_squad()
        latest_history = self.team_history.iloc[-1] if len(self.team_history) > 0 else None
        
        squad_value = squad['now_cost'].sum() / 10
        bank = latest_history['bank'] / 10 if latest_history is not None else 0
        total_value = squad_value + bank
        
        return {
            'squad_value': squad_value,
            'bank': bank, 
            'total_value': total_value,
            'player_count': len(squad)
        }
        
    def get_upcoming_fixtures(self, team_id: int, gameweeks: int = 5) -> pd.DataFrame:
        """Get upcoming fixtures for a team"""
        current_gw = self.get_current_gameweek()
        upcoming_gws = list(range(current_gw + 1, min(current_gw + gameweeks + 1, 39)))
        
        upcoming = self.fixtures[
            (self.fixtures['event'].isin(upcoming_gws)) &
            ((self.fixtures['team_h'] == team_id) | (self.fixtures['team_a'] == team_id))
        ].copy()
        
        # Add opponent and home/away info
        upcoming['opponent'] = upcoming.apply(
            lambda x: x['team_a'] if x['team_h'] == team_id else x['team_h'], axis=1
        )
        upcoming['is_home'] = upcoming['team_h'] == team_id
        
        # Merge opponent names
        upcoming = upcoming.merge(
            self.teams[['id', 'short_name']], 
            left_on='opponent', right_on='id',
            suffixes=('', '_opp')
        )
        
        return upcoming[['event', 'opponent', 'short_name', 'is_home', 'kickoff_time']].sort_values('event')
        
    def calculate_fixture_difficulty(self, team_id: int, gameweeks: int = 5) -> float:
        """Calculate average fixture difficulty for a team"""
        fixtures = self.get_upcoming_fixtures(team_id, gameweeks)
        if len(fixtures) == 0:
            return 3.0  # Neutral difficulty
            
        # Use opponent team strength as difficulty proxy
        opponent_strengths = []
        for _, fixture in fixtures.iterrows():
            opponent_team = self.teams[self.teams['id'] == fixture['opponent']]
            if len(opponent_team) > 0:
                # Higher strength = harder fixture
                strength = opponent_team.iloc[0]['strength']
                difficulty = min(5.0, max(1.0, strength / 20))  # Scale to 1-5
                opponent_strengths.append(difficulty)
        
        return np.mean(opponent_strengths) if opponent_strengths else 3.0
        
    def get_form_analysis(self, player_id: int, gameweeks: int = 5) -> Dict:
        """Analyze player form over recent gameweeks"""
        player_data = self.player_stats[self.player_stats['player_id'] == player_id]
        recent = player_data.nlargest(gameweeks, 'round')
        
        if len(recent) == 0:
            return {'points': [], 'avg_points': 0, 'games': 0}
            
        return {
            'points': recent['total_points'].tolist(),
            'avg_points': recent['total_points'].mean(),
            'games': len(recent),
            'minutes': recent['minutes'].sum()
        }
        
    def identify_transfer_targets(self, position: int, max_cost: float, min_form: float = 5.0) -> pd.DataFrame:
        """Identify potential transfer targets by position"""
        available = self.players[
            (self.players['element_type'] == position) &
            (self.players['now_cost'] <= max_cost * 10) &
            (self.players['form'] >= min_form) &
            (self.players['status'] == 'a')  # Available
        ].copy()
        
        # Add fixture difficulty
        available['fixture_difficulty'] = available['team'].apply(
            lambda x: self.calculate_fixture_difficulty(x, 5)
        )
        
        # Sort by form and value
        available['value_score'] = available['total_points'] / (available['now_cost'] / 10)
        
        return available.sort_values(['form', 'value_score'], ascending=[False, False])
        
    def get_transfer_recommendations(self, max_transfers: int = 2) -> Dict:
        """Generate transfer recommendations for problem players"""
        squad = self.get_current_squad()
        value_info = self.analyze_squad_value()
        
        # Identify problem players (low form, injury status)
        problem_players = squad[
            (squad['form'] < 2.0) | 
            (squad['status'] != 'a')
        ].sort_values('form')
        
        recommendations = []
        position_names = {1: 'GK', 2: 'DEF', 3: 'MID', 4: 'FWD'}
        
        for _, problem_player in problem_players.head(max_transfers).iterrows():
            pos = int(problem_player['element_type_x'])
            current_cost = problem_player['now_cost'] / 10
            max_budget = current_cost + value_info['bank']
            
            # Get top transfer target
            targets = self.identify_transfer_targets(pos, max_budget, 5.0)
            if len(targets) > 0:
                best_target = targets.iloc[0]
                target_cost = best_target['now_cost'] / 10
                cost_diff = target_cost - current_cost
                
                team_name = self.teams[self.teams['id'] == best_target['team']]['short_name'].iloc[0]
                
                recommendations.append({
                    'out_player': problem_player['web_name'],
                    'out_cost': current_cost,
                    'out_form': problem_player['form'],
                    'out_points': problem_player['total_points'],
                    'in_player': best_target['web_name'],
                    'in_team': team_name,
                    'in_cost': target_cost,
                    'in_form': best_target['form'],
                    'in_points': best_target['total_points'],
                    'cost_change': cost_diff,
                    'form_improvement': best_target['form'] - problem_player['form'],
                    'position': position_names[pos]
                })
        
        return {
            'recommendations': recommendations,
            'problem_count': len(problem_players),
            'bank_balance': value_info['bank'],
            'total_cost': sum(r['cost_change'] for r in recommendations if r['cost_change'] > 0)
        }
    
    def print_transfer_recommendations(self, max_transfers: int = 2):
        """Print formatted transfer recommendations"""
        rec_data = self.get_transfer_recommendations(max_transfers)
        current_gw = self.get_current_gameweek()
        
        print(f"🔄 TRANSFER RECOMMENDATIONS (GW{current_gw + 1}):")
        print("=" * 55)
        
        print(f"💰 Budget: £{rec_data['bank_balance']:.1f}m available")
        print(f"🚨 Problem players: {rec_data['problem_count']}")
        
        if not rec_data['recommendations']:
            print("✅ No urgent transfers needed - squad looking good!")
            return
        
        print(f"\n📋 PRIORITY TRANSFERS:")
        print("-" * 40)
        
        for i, rec in enumerate(rec_data['recommendations'], 1):
            cost_text = f"costs £{rec['cost_change']:.1f}m" if rec['cost_change'] > 0 else f"saves £{abs(rec['cost_change']):.1f}m"
            
            print(f"{i}. {rec['out_player']} → {rec['in_player']} ({rec['in_team']}) [{rec['position']}]")
            print(f"   £{rec['out_cost']:.1f}m → £{rec['in_cost']:.1f}m ({cost_text})")
            print(f"   Form: {rec['out_form']} → {rec['in_form']} (+{rec['form_improvement']:.1f})")
            print(f"   Points: {rec['out_points']} → {rec['in_points']} season total")
            print()
        
        if rec_data['total_cost'] > rec_data['bank_balance']:
            print("⚠️  WARNING: Total cost exceeds available budget - prioritize transfers")
        
        print(f"\n💡 TO EXECUTE: Use fpl_session.execute_transfers_sync(email, password, recommendations)")
    
    def build_optimal_team(self, budget: float = 100.0, formation: str = "3-5-2") -> Dict:
        """Build an optimal 15-player team within budget constraints"""
        formation_map = {
            "3-4-3": [2, 3, 4, 3],  # GK, DEF, MID, FWD
            "3-5-2": [2, 3, 5, 2], 
            "4-3-3": [2, 4, 3, 3],
            "4-4-2": [2, 4, 4, 2],
            "4-5-1": [2, 4, 5, 1],
            "5-3-2": [2, 5, 3, 2],
            "5-4-1": [2, 5, 4, 1]
        }
        
        if formation not in formation_map:
            raise ValueError(f"Invalid formation: {formation}. Valid: {list(formation_map.keys())}")
        
        required_players = formation_map[formation]
        available_players = self.players[self.players['status'] == 'a'].copy()  # Available only
        
        selected_team = []
        remaining_budget = budget * 10  # Convert to 0.1m units
        
        # Select players by position
        for pos_id, count in enumerate(required_players, 1):
            pos_players = available_players[available_players['element_type'] == pos_id]
            
            # Sort by value score (points per cost)
            pos_players = pos_players.copy()
            pos_players['value_score'] = pos_players['total_points'] / (pos_players['now_cost'] / 10)
            pos_players = pos_players.sort_values(['form', 'value_score'], ascending=[False, False])
            
            selected_count = 0
            for _, player in pos_players.iterrows():
                if selected_count >= count:
                    break
                    
                if player['now_cost'] <= remaining_budget:
                    selected_team.append({
                        'id': player['id'],
                        'name': player['web_name'],
                        'team': player['team'],
                        'position': pos_id,
                        'cost': player['now_cost'] / 10,
                        'points': player['total_points'],
                        'form': player['form']
                    })
                    remaining_budget -= player['now_cost']
                    selected_count += 1
        
        # Calculate team stats
        total_cost = sum(p['cost'] for p in selected_team)
        total_points = sum(p['points'] for p in selected_team)
        avg_form = sum(p['form'] for p in selected_team) / len(selected_team) if selected_team else 0
        
        # Suggest captain (highest points in starting XI)
        starting_xi = selected_team[:11] if len(selected_team) >= 11 else selected_team
        captain = max(starting_xi, key=lambda x: x['points']) if starting_xi else None
        vice_captain = max([p for p in starting_xi if p != captain], key=lambda x: x['points']) if len(starting_xi) > 1 else None
        
        return {
            'team': selected_team,
            'formation': formation,
            'total_cost': total_cost,
            'remaining_budget': (budget - total_cost),
            'total_points': total_points,
            'avg_form': avg_form,
            'captain': captain,
            'vice_captain': vice_captain,
            'team_complete': len(selected_team) == 15
        }
    
    def set_optimal_team(self, budget: float = 100.0, formation: str = "3-5-2", 
                        email: str = None, password: str = None) -> Dict:
        """Build and set optimal team lineup"""
        try:
            from fpl_session import FPLSession
            import asyncio
            
            # Build optimal team
            team_data = self.build_optimal_team(budget, formation)
            
            if not team_data['team_complete']:
                print(f"❌ Could not build complete team (only {len(team_data['team'])} players)")
                return None
            
            # Get credentials if not provided
            if not email or not password:
                from credentials_manager import get_fpl_credentials
                creds = get_fpl_credentials()
                if not creds:
                    raise Exception("No credentials provided or stored")
                email, password = creds['username'], creds['password']
            
            async def set_team():
                async with FPLSession() as session:
                    if not await session.login(email, password):
                        raise Exception("Login failed")
                    
                    player_ids = [p['id'] for p in team_data['team']]
                    captain_id = team_data['captain']['id']
                    vice_id = team_data['vice_captain']['id']
                    
                    return await session.set_team_lineup(player_ids, captain_id, vice_id)
            
            result = asyncio.run(set_team())
            print("✅ Optimal team set successfully!")
            return result
            
        except Exception as e:
            print(f"❌ Failed to set team: {e}")
            return None
    
    def execute_transfers(self, email: str = None, password: str = None, max_transfers: int = 2):
        """Execute recommended transfers automatically - uses stored credentials if not provided"""
        try:
            from fpl_session import execute_transfers_sync
            
            rec_data = self.get_transfer_recommendations(max_transfers)
            if not rec_data['recommendations']:
                print("✅ No transfers needed")
                return None
            
            result = execute_transfers_sync(rec_data['recommendations'], email, password)
            print("✅ Transfers executed successfully!")
            return result
            
        except ImportError:
            print("❌ FPL session module not available")
        except Exception as e:
            print(f"❌ Transfer failed: {e}")
            return None
    
    def print_squad_summary(self):
        """Print current squad analysis"""
        squad = self.get_current_squad()
        value_info = self.analyze_squad_value()
        current_gw = self.get_current_gameweek()
        
        print(f"🏠 CURRENT SQUAD (After GW{current_gw}):")
        print("=" * 50)
        
        position_names = {1: 'GK', 2: 'DEF', 3: 'MID', 4: 'FWD'}
        
        for pos_id in [1, 2, 3, 4]:
            pos_players = squad[squad['element_type_x'] == pos_id]
            if len(pos_players) > 0:
                print(f"\n{position_names[pos_id]}:")
                for _, p in pos_players.iterrows():
                    captain_mark = '(C)' if p['is_captain'] else '(VC)' if p['is_vice_captain'] else ''
                    cost = p['now_cost'] / 10
                    status_mark = '⚠️' if p['status'] != 'a' else ''
                    print(f"  {p['web_name']} ({p['short_name']}) - £{cost:.1f}m, {p['total_points']}pts, {p['form']} form {captain_mark} {status_mark}")
        
        print(f"\n💰 FINANCES:")
        print(f"Squad Value: £{value_info['squad_value']:.1f}m")
        print(f"Bank: £{value_info['bank']:.1f}m")
        print(f"Total Value: £{value_info['total_value']:.1f}m")

# Convenience functions
def quick_analysis():
    """Quick squad analysis"""
    analyzer = FPLAnalyzer()
    analyzer.load_data()
    analyzer.print_squad_summary()
    return analyzer

def get_recommendations(max_transfers: int = 2):
    """Quick transfer recommendations"""
    analyzer = FPLAnalyzer()
    analyzer.load_data()
    analyzer.print_transfer_recommendations(max_transfers)
    return analyzer

def execute_recommended_transfers(max_transfers: int = 2):
    """Quick analysis and transfer execution using stored credentials"""
    analyzer = FPLAnalyzer()
    analyzer.load_data()
    return analyzer.execute_transfers(max_transfers=max_transfers)

def build_and_set_team(budget: float = 100.0, formation: str = "3-5-2"):
    """Build and set optimal team using stored credentials"""
    analyzer = FPLAnalyzer()
    analyzer.load_data()
    return analyzer.set_optimal_team(budget, formation)

def find_transfers(position: str, max_cost: float, min_form: float = 5.0):
    """Quick transfer target finder"""
    position_map = {'GK': 1, 'DEF': 2, 'MID': 3, 'FWD': 4}
    pos_id = position_map.get(position.upper(), 3)
    
    analyzer = FPLAnalyzer()
    analyzer.load_data()
    
    targets = analyzer.identify_transfer_targets(pos_id, max_cost, min_form)
    
    print(f"\n🎯 TRANSFER TARGETS ({position}, max £{max_cost:.1f}m):")
    print("-" * 50)
    
    for _, player in targets.head(10).iterrows():
        cost = player['now_cost'] / 10
        team = analyzer.teams[analyzer.teams['id'] == player['team']]['short_name'].iloc[0]
        print(f"{player['web_name']} ({team}) - £{cost:.1f}m, {player['total_points']}pts, {player['form']} form, FD:{player['fixture_difficulty']:.1f}")
    
    return targets