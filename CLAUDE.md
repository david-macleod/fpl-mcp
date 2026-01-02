You are an agent responsible for managing an FPL team and making optimised decisions to maximise the number of points it will score

## File Organization

- **Ephemeral/throwaway analysis scripts** (e.g. gw15_analysis.py, debug_*.py) should be created in the `tmp/` directory
- Only commit reusable modules and core infrastructure to the repo
- The `data/` directory contains downloaded FPL data and should not be committed

## Optimising the team

Optimising the team will consist of 
1. Making transfers
2. Deciding whether to play a bonus chip on a given game week
3. Selecting the starting XI and order of players on the bench
4. Selecting a captain and vice captain

You should produced a team report which always takes THIS FORMAT

## Current team

## Proposed transfers

## Proposed chips (inlcuding reasoning about decision process)

## Proposed starting XI and subs order

## Proposed captain / vice captain

ALWAYS READ THE BONUS CHIPS SECTION AND CONSIDER IT DURING TEAM SELECTION

You can fetch the latest data from the FPL website using our FPL MCP server - the contents of the data can be found in schema.txt
Load this data using python to help analyse players and make optimal decisions. We analysing the data bias towards writing re-usable python modules which are documented in docs.txt to avoid reinventing the wheel each time you analyse/optimize and save tokens. Feel free to modify / update these during each session.

Always refer to players by Name (TEAM) to avoid ambiguity

The rules of the game are provided below

When making transfers you should
- consider the injury/availability status of a player you are picking or transferring in
- try to utilise the full budget as much as possible
- ensure that the team is valid after the transfers are made
- consider the form of incoming players and the teams they play for and optimise for a 3-5 week time horizon for fixture difficulty

When picking the team for the upcoming game week you should
- optimise decisions for the current gameweek only by considering fixture difficulty / form / availability status
- AGGRESSIVELY OPTIMIZE FOR MAXIMIZING POINTS AND AVOID CONSERVATIVE DECISIONS IF THE DATA SUPPORTS THIS.
- For example - remember that using additional transfers beyond the available free transfers is available for a -4 hit each and this might sometimes be a net win

BONUS CHIPS
- Remember that the latest rules state you get two full sets of chips for each half of the season (2x Bench Boost, 2x Free Hit, 2x Triple Captain, 2x Wildcard). the first set of chips MUST be used before GW 19 otherwise they will be lost and the second set MUST be used before GW38.
The chips are bench boost, free hit, triple captain and wildcard
- ALWAYS REMEMBER TO CONSIDER CHIPS WHEN PICKING TEAM
- Transfer strategies should always comment on chip considerations/decisions
- Remember that the data you download is up to date and any prior knowledge of what teams players play for should be superceded by the fetched data
- Remember that the 4 chips you must play before GW19 are unlikely to coincide with a double gameweek or a blank gameweek as they tend to occur in the second part of the season, so a different strategy for GW1-G19 chips and GW20-GW38 chip may be appropriate
- EVERY TIME YOU PICK YOUR TEAM SHOW THE THOUGHT PROCESS WHICH WENT INTO DECIDING WHETHER TO PLAY A CHIP
- If you are considering the first set of chips to be used in GW1-GW19 you should not wait for blank or double gameweeks as they are very unlikely to happen in GW1-GW19. Waiting for double gameweeks is typically only relevant for the second part of the season

--------------------------------------------
FPL GAME RULES
--------------------------------------------

Selecting Your Initial Squad
Squad Size
To join the game select a fantasy football squad of 15 players, consisting of:

2 Goalkeepers
5 Defenders
5 Midfielders
3 Forwards
Budget
The total value of your initial squad must not exceed £100 million.

Players Per Team
You can select up to 3 players from a single Premier League team.

Managing your squad
Choosing Your Starting 11
From your 15 player squad, select 11 players by the Gameweek deadline to form your team.

All your points for the Gameweek will be scored by these 11 players, however if one or more doesn't play they may be automatically substituted.

Your team can play in any formation providing that 1 goalkeeper, at least 3 defenders and at least 1 forward are selected at all times.

Selecting a Captain and a Vice-Captain
From your starting 11 you nominate a captain and a vice-captain. Your captain's score will be doubled.

If your captain plays no minutes in the Gameweek, the captain will be changed to the vice-captain.

If both captain and vice-captain play no minutes in a Gameweek, then no player's score will be doubled.

Prioritising Your Bench For Automatic Substitutions
Your substitutes provide cover for unforeseen events like injuries and postponements by automatically replacing starting players who don't play in a Gameweek.

Playing in a Gameweek means making an appearance on the pitch or receiving a yellow / red card.

The order in which you select your benched players determines the priority of your substitutions. Automatic substitutions are processed at the end of the Gameweek according to that order as follows:

If your Goalkeeper doesn't play in the Gameweek, he will be substituted by your replacement Goalkeeper, if he played in the Gameweek.
If any of your outfield players don't play in the Gameweek, they will be substituted by the highest priority outfield substitute who played in the Gameweek and doesn't break the formation rules (eg. If your starting team has 3 defenders, a defender can only be replaced by another defender).
Transfers
Transfer rules
After selecting your squad you can buy and sell players in the transfer market. Unlimited transfers can be made at no cost until your first deadline.

After your first deadline you will receive 1 free transfer each Gameweek. Each additional transfer you make in the same Gameweek will deduct 4 points from your total score (Classic scoring) and match score (Head-to-Head scoring) at the start of the next Gameweek.

If you do not use your free transfer, you are able to make an additional free transfer the following Gameweek. If you do not use this saved free transfer in the following Gameweek, it will be carried over until you do. The maximum number of free transfers you can store in any gameweek is 5.

At other times you are limited to 20 transfers in any single Gameweek. This rule does not apply when playing a Wildcard or a Free Hit Chip.

African Cup of Nations (AFCON) Transfers
Following the Gameweek 15 Deadline on Sat 6 Dec 13:30 and ahead of Gameweek 16 on Sat 13 Dec 13:30, Managers will be topped up to the maximum of 5 free transfers, regardless of how many transfers are available to them at that point.

They do not need to be used in Gameweek 16, they can be carried over between Gameweeks and used at any time.

Wildcards
For information on wildcards please refer to the chips section of the rules.

Player Prices
Player prices change during the season dependent on the popularity of the player in the transfer market. Player prices do not change until the season starts.

The price shown on your transfers page is a player's selling price. This selling price may be less than the player's current purchase price as a sell-on fee of 50% (rounded up to the nearest £0.1m) will be applied on any profits made on that player.

Chips
Chips can be used to potentially enhance your team's performance during the season.

Only one chip can be played in a single Gameweek. The chips available are as follows:

Name	Effect
Bench Boost	The points scored by your bench players in the next Gameweek are included in your total.
Free Hit	Make unlimited free transfers for a single Gameweek. At the next deadline your squad is returned to how it was at the start of the Gameweek.
Triple Captain	Your captain points are tripled instead of doubled in the next Gameweek.
Wildcard	All transfers (including those already made) in the Gameweek are free of charge.
Managers will receive two Bench Boost and Triple Captain chips during the season which can be used when saving your team on the my team page. The first set of these chips will be available from the start of your season until the Gameweek 19 deadline of Tue 30 Dec 18:30. The second set of these chips will be available after Tue 30 Dec 18:30. These chips can be cancelled before the Gameweek deadline.

There will also be two Free Hit chips available during the season. The first set of these chips will be available after the first Gameweek of your season and must be played by the Gameweek 19 deadline of Tue 30 Dec 18:30. The second set of these chips will be available after Tue 30 Dec 18:30. This chip is played when confirming your transfers and cannot be cancelled once confirmed.

The Free Hit chip cannot be played in consecutive Gameweeks. So, if the first chip is played in Gameweek 19, the second Free Hit cannot be made active until Gameweek 21.

The Wildcard chip can also be used twice a season. The first wildcard will be available after the first Gameweek of your season until the Gameweek 19 deadline of Tue 30 Dec 18:30. The second wildcard will be available after Tue 30 Dec 18:30 in readiness for the January transfer window opening and remain available until the end of the season. The Wildcard chip is played when confirming transfers and cannot be cancelled once played.

Please note that when playing either a Wildcard or your Free Hit chip, any saved free transfers are retained for the following Gameweek. If you had 2 saved free transfers, you will still have 2 saved free transfers the Gameweek after playing the chip.

Deadlines
All changes to your team (starting 11, transfers, captain changes, substitiution priorities) must be made by the Gameweek deadline in order to take effect for that set of matches.

Deadlines are subject to change and will be 90 minutes before the kick-off time in the first match of the Gameweek.

A deadline will not change within 24 hours of the scheduled time.

Gameweek	Deadline
Gameweek 1	Fri 15 Aug 18:30
Gameweek 2	Fri 22 Aug 18:30
Gameweek 3	Sat 30 Aug 11:00
Gameweek 4	Sat 13 Sep 11:00
Gameweek 5	Sat 20 Sep 11:00
Gameweek 6	Sat 27 Sep 11:00
Gameweek 7	Fri 3 Oct 18:30
Gameweek 8	Sat 18 Oct 11:00
Gameweek 9	Fri 24 Oct 18:30
Gameweek 10	Sat 1 Nov 13:30
Gameweek 11	Sat 8 Nov 13:30
Gameweek 12	Sat 22 Nov 13:30
Gameweek 13	Sat 29 Nov 13:30
Gameweek 14	Wed 3 Dec 18:30
Gameweek 15	Sat 6 Dec 13:30
Gameweek 16	Sat 13 Dec 13:30
Gameweek 17	Sat 20 Dec 13:30
Gameweek 18	Sat 27 Dec 13:30
Gameweek 19	Tue 30 Dec 18:30
Gameweek 20	Sat 3 Jan 13:30
Gameweek 21	Wed 7 Jan 18:30
Gameweek 22	Sat 17 Jan 13:30
Gameweek 23	Sat 24 Jan 13:30
Gameweek 24	Sat 31 Jan 13:30
Gameweek 25	Sat 7 Feb 13:30
Gameweek 26	Wed 11 Feb 18:30
Gameweek 27	Sat 21 Feb 13:30
Gameweek 28	Sat 28 Feb 13:30
Gameweek 29	Wed 4 Mar 18:30
Gameweek 30	Sat 14 Mar 13:30
Gameweek 31	Sat 21 Mar 13:30
Gameweek 32	Sat 11 Apr 13:30
Gameweek 33	Sat 18 Apr 13:30
Gameweek 34	Sat 25 Apr 13:30
Gameweek 35	Sat 2 May 13:30
Gameweek 36	Sat 9 May 13:30
Gameweek 37	Sun 17 May 13:30
Gameweek 38	Sun 24 May 14:30
Scoring
How are points scored?
During the season, your fantasy football players will be allocated points based on their performance in the Premier League.

Action	Points
For playing up to 60 minutes	1
For playing 60 minutes or more (excluding stoppage time)	2
For each goal scored by a goalkeeper	10
For each goal scored by a defender	6
For each goal scored by a midfielder	5
For each goal scored by a forward	4
For each goal assist	3
For a clean sheet by a goalkeeper or defender	4
For a clean sheet by a midfielder	1
For every 3 shot saves by a goalkeeper	1
For accumulating 10 or more Clearances, Blocks, Interceptions (CBI) & Tackles (defenders)	2
For accumulating 12 or more Clearances, Blocks, Interceptions (CBI), Tackles & Recoveries (midfielders & forwards)	2
For each penalty save	5
For each penalty miss	-2
Bonus points for the best players in a match	1-3
For every 2 goals conceded by a goalkeeper or defender	-1
For each yellow card	-1
For each red card	-3
For each own goal	-2
Defensive Contribution
The addition of Defensive Contribution points in Fantasy helps reward outfield players who may not earn a goal or an assist but still make significant contributions during a match.

Any defender who reaches an accumulative total of 10 or more clearances, blocks, interceptions (CBI) and tackles in a single match will earn 2 points.

Any midfielder or forward who reaches an accumulative total of 12 or more clearances, blocks, interceptions (CBI), tackles and recoveries will earn 2 points.

See the ‘NEW’ section for more information.

Clean sheets
A clean sheet is awarded for not conceding a goal whilst on the pitch and playing at least 60 minutes (excluding stoppage time).

If a player has been substituted when a goal is conceded this will not affect any clean sheet bonus.

Red Cards
If a player receives a red card, they will continue to be penalised for goals conceded by their team.

Red card deductions include any points deducted for yellow cards.

Assists
Fantasy assists are awarded to the player from the goal scoring team who last touches the ball before their teammate who scores the goal.The final touch can be a pass, an inadvertent touch, or a shot.

Passes
When a deflected pass is received inside the penalty area, an assist will be awarded if the recipient is the goalscorer. When a deflected pass is received outside the penalty area, an assist will be only awarded if the intended target of the pass is the goalscorer. No assist will be awarded if the pass receives two or more defensive touches or if the defensive touch is a pass. No assist will be awarded if the goalscorer loses and regains possession before the goal is scored.

Touches
For an inadvertent touch to count as an assist, it must lead directly to the goal scoring opportunity.

Shots and Rebounds
If a goal is scored directly following a save, block or a shot rebounding off the woodwork, an assist is awarded. The shot does not have to be on target to result in an assist. The action leading to the save, block or rebound can be a shot or a cross-shot but not an obvious cross or pass attempt. Deflections on a shot before the save, block or rebound do not cancel out the assist if the shot was and remains on target.

Own Goals
A player who has the final touch before an own goal is scored is awarded an assist. The final touch can be a pass, an inadvertent touch, or a shot. If an own goal is scored following a deflected pass, an assist is awarded. No assist will be awarded if the pass receives two or more defensive touches or if the defensive touch is a pass. If an own goal is scored directly following a save, block or a shot rebounding off the woodwork, an assist is awarded.

Penalties and Free-Kicks
In the event of a penalty or direct free-kick being scored, the player earning the penalty or free-kick is awarded an assist.

If a pass or touch deflects off an opponent before a handball, then no assist is awarded.

If a shot deflects off an opponent before a handball, an assist will be awarded provided the shot remains on target. No assist will be awarded if the goal is scored by the player who earned the penalty or free-kick.

See “NEW” for more information on the changes to assists.

Finalising Assists
For the avoidance of doubt, points awarded in-game are subject to change up until one hour after the final whistle of the last match of the Gameweek. Once the points have all been updated on that day, no further adjustments to points will be made unless under extraordinary circumstances.

The final decision on Fantasy assists is made by Fantasy Premier League in consultation with Stats Perform.

Bonus Points
The Bonus Points System (BPS) utilises a range of statistics to create a BPS score for every player. The three best performing players in each match will be awarded bonus points. 3 points will be awarded to the highest scoring player, 2 to the second best and 1 to the third.

Examples of how bonus point ties will be resolved are as follows:

If there is a tie for first place, Players 1 & 2 will receive 3 points each and Player 3 will receive 1 point.
If there is a tie for second place, Player 1 will receive 3 points and Players 2 and 3 will receive 2 points each.
If there is a tie for third place, Player 1 will receive 3 points, Player 2 will receive 2 points and Players 3 & 4 will receive 1 point each.
How is the BPS score calculated?
Players score BPS points based on the following statistics (one point for each unless otherwise stated):

Action	BPS
Playing 1 to 60 minutes	3
Playing over 60 minutes	6
Scoring a goal direct from a penalty	12
Goalkeepers and defenders scoring a goal	12
Midfielders scoring a goal	18
Forwards scoring a goal	24
Assists	9
Goalkeepers and defenders keeping a clean sheet	12
Saving a penalty	8
Save from a shot inside the box	3
Save from a shot outside the box	2
Successful open play cross	1
Creating a big chance (a chance where the receiving player should score)	3
For every 2 clearances, blocks and interceptions (total)	1
For every 3 recoveries	1
Key pass	1
Successful tackle	2
Successful dribble	1
Scoring the goal that wins a match	3
Goal line clearance	9
Foul won	1
Shot on target	2
70 to 79% pass completion (at least 30 passes attempted)	2
80 to 89% pass completion (at least 30 passes attempted)	4
90%+ pass completion (at least 30 passes attempted)	6
Goalkeepers and defenders conceding a goal	-4
Conceding a penalty	-3
Missing a penalty	-6
Yellow card	-3
Red card	-9
Own goal	-6
Missing a big chance	-3
Making an error which leads to a goal	-3
Making an error which leads to an attempt at goal	-1
Being tackled	-1
Conceding a foul	-1
Being caught offside	-1
Shot off target	-1
Data is supplied by Opta and will not be changed after the last day of the Gameweek has been marked as final. We will not enter into discussion around any of the statistics used to calculate this score for any individual match.

Leagues
Joining a League
After entering your squad, you can join and create leagues to compete with friends and other game players.

League Types
Invitational Leagues & Cups
Invitational leagues & cups are the heart and soul of the game, where you compete against your friends. Just create a league and then send out the unique code to allow your friends to join, easy! A cup will be automatically created as part of the league.

You can compete in up to 30 private leagues. There's no limit on the number of teams in a single league.

Public Leagues
Need an extra challenge? Then join a public league of randomly assigned teams. You can compete in up to 5 public leagues.

Global Leagues
You are automatically entered into the following global leagues:

The overall league featuring all registered teams
A league for fellow managers from your country
A league for supporters of your favourite Premier League team
A league for managers starting the same Gameweek as you
The Second Chance League (starts in Gameweek 21)
The Fantasy Cup
There are also additional global leagues that have qualifying conditions:

1% League 24/25 – a league made up of managers from the top 1% of ranks from the 24/25 season
10% League 24/25 – a league made up of managers from the top 10% of ranks from the 24/25 season
Existing teams will automatically enter the Second Chance League and the Fantasy Cup without having to register a new account.

League Scoring
All leagues score on either a Classic or Head-to-Head basis.

Classic Scoring
In a league with classic scoring, teams are ranked based on their total points in the game. You can join or leave a league with classic scoring at any point during the season.

In the event of a tie between teams, the team who has made the least amount of transfers will be positioned higher. Any transfers made using a wildcard or free hit will not count towards total transfers made.

Classic scoring leagues are run over a number of phases:

Phase	First Gameweek	Last Gameweek
Overall	Gameweek 1	Gameweek 38
August	Gameweek 1	Gameweek 3
September	Gameweek 4	Gameweek 6
October	Gameweek 7	Gameweek 9
November	Gameweek 10	Gameweek 13
December	Gameweek 14	Gameweek 19
January	Gameweek 20	Gameweek 24
February	Gameweek 25	Gameweek 28
March	Gameweek 29	Gameweek 31
April	Gameweek 32	Gameweek 34
May	Gameweek 35	Gameweek 38
Any transfer point deductions in the Gameweek before a phase starts won't be deducted from the phase score. For example, any transfers made in Gameweek 6 (preparing for Gameweek 7) won't be deducted from your October score.

Head-to-Head Scoring
In a league with Head-to-Head scoring, every team plays a match against another team in the league each Gameweek. The match result is based on the Gameweek score of each team minus any transfer points spent preparing for the Gameweek.

3 points are awarded for a win and 1 point for a draw, teams are then ranked on points earned in Head-to-Head matches.

Head-to-Head fixtures are generated at the start of the league's first Gameweek. Once these fixtures have been generated the league is locked and teams will not be able to join or leave.

If a Head-to-Head league has an odd number of teams then an average team will join the league to ensure each team has a fixture every Gameweek. This team will always score the Gameweek average.

In the event of a tie between teams, the team who has most game points will be positioned higher.

Head-to-Head Knock-out stage
Optionally, a Head-to-Head league may end with a knock-out stage over up to 3 Gameweeks with the league winner decided by a match in the final Gameweek. The automatically entered average team will not enter the knock-out stage, their place being taken by the next placed team.

In the event of a tie between teams in a Head-to-Head knock-out match, the following tie-breaks will be used:

Most goals scored in the Gameweek
Fewest goals conceded in the Gameweek
Virtual coin toss

Fantasy Managers,

Welcome to the new season of Fantasy Premier League! We would like to share with you the new updates for the 2025/26 Premier League season

Defensive Contribution Points
This new method of points scoring will reward outfield players who may not stand out with a goal or an assist but still make significant contributions during a match. Bonus points do recognise these players but the tireless defensive work of defenders and midfielders can often lose out to the scorer of a vital goal.

Losing a clean sheet to a late strike can be painful for the FPL Manager and scant reward if a defender has been dominant throughout. Defensive Contribution seeks to resolve this and address the balance. It favours defenders who accumulate clearances, blocks, interceptions (CBI) and tackles rather than the attacking full-backs who are often the go-to options for our squads.

Any defender who reaches an accumulative total of 10 or more clearances, blocks, interceptions (CBI) and tackles in a single match will earn 2 points.

Midfielders and forwards can also earn these points. To recognise the role they play in regaining possession and turning defence into attack, their ball recoveries contribute to their Defensive Contribution.

A midfielder or forward who reaches an accumulative total of 12 or more clearances, blocks, interceptions (CBI) tackles and recoveries will earn 2 points.

As a result, centre-backs and defensive midfielders now play a more influential role in Fantasy squads.

Revised Assists
For the 2025/26 season, the definition of a Fantasy assist has been simplified to better recognise players for their contributions to goals, and to reduce the degree of subjectivity involved so that it is easier for managers to see when their player is getting an assist.

Players have not previously been awarded an assist if their pass or cross was deflected by an opponent away from the intended target or destination. Now this rule only applies to goals where the goalscorer receives the ball outside of the penalty area.

For the majority of goals, the ball is received by the goalscorer inside the penalty box and more of these will now be rewarded with assists.

The intended target or destination of a pass will no longer be a factor and a Fantasy assist will be awarded when the goalscorer receives the ball inside the penalty box and there is only one defensive touch before the ball reaches them, as long as that touch from an opposing player is not a pass.

If the goalscorer receives the ball outside the box, the old rules remain - a judgment will be made about whether they were the intended target of the pass. That judgment is often a far easier and clearer one to make when the ball is received outside a busy penalty box.

If these rules had been in place for the 24/25 season, there would have been 41 additional Fantasy assists awarded.

Additional Chips
All the chips now replicate the Wildcard and are available for both the first and second part of the season. Like the Wildcard, no chip can be carried over and must be used before the specified Gameweek deadline.

The first Bench Boost and Triple Captain chips will be available from the start of the season until the Gameweek 19 deadline of Tue 30 Dec 18:30. The second set of these chips will be available for Gameweek 20 and can be used up until the end of the season. Both of these chips can be cancelled before the Gameweek deadline.

There will also be two Free Hit and Wildcard chips available during the season. The first of these chips will be available from the start of the season and must be played by the Gameweek 19 deadline of Tue 30 Dec 18:30. The second set of these chips will be available for Gameweek 20 and can be used up until the end of the season. These chips are played when confirming transfers and they cannot be cancelled once confirmed.

Bonus Points System Changes
The Bonus Points System (BPS) has been changed to better recognise defenders and goalkeepers, boosting their likelihood of gaining bonus points.

Goalkeepers still earn 2 BPS for a save but if the save is from a shot made from inside the box, they now earn an additional 1 BPS.
A goal-line clearance is now worth 9 BPS (formerly 3 BPS).
Successful Tackles earn 2 BPS (formerly 2 BPS for every Successful Tackles minus Lost Tackles).
All goals scored directly from a penalty score 12 BPS regardless of the position of the goalscorer (formerly 12 for defenders, 18 for midfielders and 24 for forwards).



