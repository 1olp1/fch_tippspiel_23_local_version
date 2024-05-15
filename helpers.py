from flask import redirect, render_template, session
from functools import wraps
import requests
import uuid
import os
from PIL import Image
import json
from datetime import datetime
from cs50 import SQL

# Prepare API requests
league = "bl1"      # bl1 for 1. Bundesliga
league_id = 4608
season = "2023"     # 2023 for 2023/2024 season
team = "1. FC Heidenheim 1846"
team_id = 199

# urls for openliga queries
url_matchdata = f"https://api.openligadb.de/getmatchdata/{league}/{season}/{team}"
url_table = f"https://api.openligadb.de/getbltable/{league}/{season}"
url_teams = f"https://api.openligadb.de/getavailableteams/{league}/{season}"


# Folder paths
local_folder_path = os.path.join(".", "static", league, season)
img_folder =  os.path.join(local_folder_path, "team-logos")


# Connect to the SQLite database
db = SQL("sqlite:///tippspiel.db")

# Control the update mechanism of the database concerning the openliga updates
automatic_updates = False

def get_local_FCH_matches():
    FCH_matches_db = db.execute("""
                            SELECT 
                            FCH_matches.*,
                            team1.teamName AS team1_name,
                            team2.teamName AS team2_name,
                            team1.teamIconPath AS team1IconPath,
                            team2.teamIconPath AS team2IconPath,
                            team1.shortName AS team1_shortname,
                            team2.shortName AS team2_shortname
                            FROM 
                                FCH_matches
                            JOIN 
                                teams AS team1 ON FCH_matches.team1_id = team1.id
                            JOIN 
                                teams AS team2 ON FCH_matches.team2_id = team2.id;
                            """)
    
    for match in FCH_matches_db:
        match["matchDateTime"] = convert_iso_datetime_to_human_readable(match["matchDateTime"])

    return FCH_matches_db
    

def get_teams():
    teams_db = db.execute("SELECT * FROM teams")
    return teams_db


def login_required(f):
    """
    Decorate routes to require login.

    https://flask.palletsprojects.com/en/latest/patterns/viewdecorators/
    """

    @wraps(f)
    def decorated_function(*args, **kwargs):
        if session.get("user_id") is None:
            return redirect("/login")
        return f(*args, **kwargs)

    return decorated_function


def apology(message, code=400):
    """Render message as an apology to user."""

    def escape(s):
        """
        Escape special characters.

        https://github.com/jacebrowning/memegen#special-characters
        """
        for old, new in [
            ("-", "--"),
            (" ", "-"),
            ("_", "__"),
            ("?", "~q"),
            ("%", "~p"),
            ("#", "~h"),
            ("/", "~s"),
            ('"', "''"),
        ]:
            s = s.replace(old, new)
        return s

    return render_template("apology.html", top=code, bottom=escape(message)), code


def make_image_filepath(team):
    img_file_name = team['shortName'] + os.path.splitext(team['teamIconUrl'])[1]
    img_file_path = os.path.join(img_folder, img_file_name)
    print("bims im image filepathmaker")

    return img_file_path


def get_openliga_json(url):
    try:
        response = requests.get(url)
        response.raise_for_status()

        return response.json()
    
    except (KeyError, IndexError, requests.RequestException, ValueError):
        return None
    

def get_league_table():
    if automatic_updates:
        if is_update_needed_league_table():
            update_league_table()
        

    table = db.execute("""SELECT * FROM teams
                       ORDER BY rank ASC
                       """)
    
    table[0]["lastUpdateTime"] = convert_iso_datetime_to_human_readable(table[0]["lastUpdateTime"])

    return table
        

def get_matches_FCH():
    #create_teams_table() ###
    #insert_matches_to_db()

    print("getting matches...")   

    if automatic_updates:
        if is_update_needed_FCH_matches():
            update_FCH_matches_db()

    # Return matches from the local database in the right format
    return get_local_FCH_matches()


def create_teams_table():
    teams = get_openliga_json(url_teams)
    
    for team in teams:
        db.execute("INSERT INTO teams (id, teamName, shortName, teamIconUrl, teamIconPath) VALUES(?, ?, ?, ?, ?)",
                   team["teamId"],
                   team["teamName"],
                   team["shortName"],
                   team["teamIconUrl"],
                   make_image_filepath(team))
        
    db.execute("UPDATE teams SET lastUpdateTime = ?", get_current_datetime())
               

def update_league_table():
    table = get_openliga_json(url_table)
    for rank, team in enumerate(table, start=1):
        db.execute("""
                   UPDATE teams SET
                   points = ?, opponentGoals = ?, goals = ?, matches = ?, won = ?, lost = ?, draw = ?, goalDiff = ?,
                   rank = ? WHERE id = ?
                   """,
                   team["points"],
                   team["opponentGoals"],
                   team["goals"],
                   team["matches"],
                   team["won"],
                   team["lost"],
                   team["draw"],
                   team["goalDiff"],
                   rank,
                   team["teamInfoId"]
                   )
    
    db.execute("UPDATE teams SET lastUpdateTime = ?", get_current_datetime())


def update_user_scores():
    # Get data for evaluating the predictions
    matches = get_local_FCH_matches()
    predictions = db.execute("SELECT * FROM predictions")

    # Evaluate predictions for each match
    for match in matches:
        if match["matchIsFinished"] == 1 and match["predictions_evaluated"] == 0:
            # Prerequisites
            match_id = match["id"]
            team1_score = match["team1_score"]
            team2_score = match["team2_score"]
            goal_diff = team1_score - team2_score
            winner = 1 if team1_score > team2_score else 2 if team1_score < team2_score else 0

            # Get predictions for this match
            predictions = db.execute("SELECT * FROM predictions WHERE match_id = ?", match_id)

            for prediction in predictions:
                awarded_points = 0                    

                # Award 4 points for correct result
                if team1_score == prediction["team1_score"] and team2_score == prediction["team2_score"]:
                    awarded_points = 4
                
                # 3 points for correct goal_diff
                elif goal_diff == prediction["goal_diff"]:
                    awarded_points = 3

                # 2 points for correct tendency
                elif winner == prediction["winner"]:
                    awarded_points = 2
                
                # 0 point for wrong prediction
                else:
                    awarded_points = 0

                # Set points for the current prediction
                db.execute("UPDATE predictions SET points = ? WHERE id = ?", awarded_points, prediction["id"])

            # Switch predictions_evaluated to 1 for the match, so that next time, only the not evaluated matches get evaluated
            db.execute("UPDATE FCH_matches SET predictions_evaluated = 1, evaluation_Date = ? WHERE id = ?",get_current_datetime(), match["id"])

    # Update total points in the users table (Query with help from chatGPT)
    db.execute("""
               UPDATE users
               SET total_points = (
                   SELECT SUM(points)
                   FROM predictions
                   WHERE user_id = users.id
               )
               WHERE id IN (
               SELECT DISTINCT user_id
               FROM predictions
               )
               """)
    
    # Update correct result count in users table (with help from chatGPT)
    db.execute("""
               UPDATE users
               SET correct_result = (
                    SELECT(COUNT(*))
                    FROM predictions
                    WHERE points = 4
                    AND user_id = users.id
               )
               WHERE id IN (
               SELECT DISTINCT user_id
               FROM predictions
               )
               """)
    
    # Update correct goal diff count in users table (with help from chatGPT)
    db.execute("""
               UPDATE users
               SET correct_goal_diff = (
                    SELECT(COUNT(*))
                    FROM predictions
                    WHERE points = 3
                    AND user_id = users.id
               )
               WHERE id IN (
               SELECT DISTINCT user_id
               FROM predictions
               )
               """)
    
    # Update correct tendency count in users table (query with help from chatGPT)
    db.execute("""
               UPDATE users
               SET correct_tendency = (
                    SELECT(COUNT(*))
                    FROM predictions
                    WHERE points = 2
                    AND user_id = users.id
               )
               WHERE id IN (
               SELECT DISTINCT user_id
               FROM predictions
               )
               """)


def insert_matches_to_db():
    # Query openliga API with link from above
    matchdata = get_openliga_json(url_matchdata)

    if matchdata:
        for match in matchdata:
            # Local variable if match is finished
            matchFinished = match["matchIsFinished"]

            team1_score = match["matchResults"][1]["pointsTeam1"] if matchFinished else None
            team2_score = match["matchResults"][1]["pointsTeam2"] if matchFinished else None

            print(f"Save to db")

            # Save to database
            db.execute("""
                       INSERT INTO FCH_matches(id, matchday, team1_id, team2_id, team1_score, team2_score, matchDateTime,
                       matchIsFinished, lastUpdateDateTime)
                       VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                       """,
                       match["matchID"],
                       match["group"]["groupOrderID"],
                       match["team1"]["teamId"],
                       match["team2"]["teamId"],
                       team1_score,
                       team2_score,
                       match["matchDateTime"],
                       matchFinished,
                       match["lastUpdateDateTime"] # So that it is always of same format
                       )


def update_FCH_matches_db():
    # Get unfinished matches of the local database
    unfinished_matches_db = db.execute("""
                                     SELECT * FROM FCH_matches
                                     WHERE matchIsFinished = 0;
                                     """)
    
    for match in unfinished_matches_db:
        # Get matchdata openliga
        matchdata_openliga = get_matchdata_openliga(match["id"])

        # Get lastUpdateTime for match in db
        last_update_time_openliga = matchdata_openliga["lastUpdateDateTime"]

        last_update_time_db = match["lastUpdateDateTime"]

        if last_update_time_openliga and last_update_time_db:
            # Convert dates to comparable format
            last_update_time_openliga = datetime.strptime(last_update_time_openliga, '%Y-%m-%dT%H:%M:%S.%f')
            last_update_time_db = datetime.strptime(last_update_time_db, '%Y-%m-%dT%H:%M:%S.%f')

            if last_update_time_openliga > last_update_time_db:
                update_match_in_db(matchdata_openliga)
        else:
            # Update if last update time is missing or inconsistent
            update_match_in_db(matchdata_openliga)


def update_match_in_db(match):
    print("Updating match: ", match["matchID"])
    # Local variable if match is finished
    matchFinished = match["matchIsFinished"]
    
    db.execute("""
               UPDATE FCH_matches SET
               team1_score = ?,
               team2_score = ?,
               matchDateTime = ?,
               matchIsFinished = ?,
               lastUpdateDateTime = ?
               WHERE id = ?
                """,
                [match["matchResults"][1]["pointsTeam1"] if matchFinished else None],
                [match["matchResults"][1]["pointsTeam2"] if matchFinished else None],
                match["matchDateTime"],
                match["matchIsFinished"],
                match["lastUpdateDateTime"],
                match["matchID"]
        )


def download_logos(table_data):
    # Make path for team logos if it does not exist already for the league and season
    os.makedirs(img_folder, exist_ok=True)

    # If folder empty, download images
    if not os.listdir(img_folder):
        
        for team in table_data:          
            try:
                img_url = team['teamIconUrl']
                response = requests.get(
                    img_url,
                    cookies={"session": str(uuid.uuid4())},
                    headers={"Accept": "*/*", "User-Agent": "python-requests"},
                )
                response.raise_for_status()

                # Create image paths
                img_file_path = make_image_filepath(team, img_folder)

                # Save images
                with open(img_file_path, 'wb') as f:
                    f.write(response.content)
                
                # Lower resolution
                resize_image(img_file_path)

            except (KeyError, IndexError, requests.RequestException, ValueError):
                return None


def add_random_predictions_to_db():
    user_ids = db.execute("SELECT id FROM users")
    print("USER IDS:" ,user_ids)

    pass


def get_insights():
    predictions_rated = db.execute("""
                                SELECT COUNT(*) AS predictions_rated
                                FROM predictions AS p
                                JOIN FCH_matches AS m ON m.id = p.match_id
                                WHERE p.user_id = ?
                                AND m.matchIsFinished = 1
                                """, session["user_id"])
    
    prediction_count = db.execute("SELECT COUNT(*) AS prediction_count FROM predictions AS p WHERE p.user_id = ?", session["user_id"])
    
    # Get some base data for statistics
    finished_matches = db.execute("SELECT COUNt(*) AS completed_matches FROM FCH_matches WHERE matchIsFinished = 1")
    total_points_user = db.execute("SELECT total_points FROM users WHERE id = ?", session["user_id"])
    rank = db.execute("""
                      SELECT rank
                      FROM (SELECT id, ROW_NUMBER() 
                      OVER (
                        ORDER BY total_points DESC) AS rank
                        FROM users
                      ) AS ranked_users
                      WHERE id = ?
                      """, session["user_id"])    # This query was created with help from chatgpt
    
    base_stats = db.execute("SELECT correct_result, correct_goal_diff, correct_tendency FROM users WHERE id = ?", session["user_id"])[0]
    no_users = db.execute("SELECT COUNT(*) AS no_users FROM users")[0]["no_users"]

    # Store the statistics in the insights dictionary
    insights = {}

    # If there have been predictions, count how many were made
    if predictions_rated:
        insights["predictions_rated"] = predictions_rated[0]['predictions_rated']
    else:
        insights["predictions_rated"] = 0

    # Create useful statistics and store in insights dict    
    insights["total_games_predicted"] = prediction_count[0]["prediction_count"]
    insights["missed_games"] = finished_matches[0]["completed_matches"] - predictions_rated[0]['predictions_rated']    
    insights["total_points"] = total_points_user[0]["total_points"]
    insights["username"] = db.execute("SELECT username FROM users WHERE id = ?", session["user_id"])[0]["username"]
    insights["no_users"] = no_users
    insights["rank"] = rank[0]["rank"]
    insights["corr_result"] = base_stats["correct_result"]
    insights["corr_goal_diff"] = base_stats["correct_goal_diff"]
    insights["corr_tendency"] = base_stats["correct_tendency"]
    insights["wrong_predictions"] = insights["predictions_rated"] - insights["corr_result"] - insights["corr_goal_diff"] - insights["corr_tendency"]

    # Differentiate if predictions have been rated to avoid dividing by 0 for the percentage
    if insights["predictions_rated"] != 0:
        insights["corr_result_p"] = round((base_stats["correct_result"] / insights["predictions_rated"])*100)
        insights["corr_goal_diff_p"] = round(base_stats["correct_goal_diff"] / insights["predictions_rated"]*100)
        insights["corr_tendency_p"] = round(base_stats["correct_tendency"] / insights["predictions_rated"]*100)
        insights["wrong_predictions_p"] = round(insights["wrong_predictions"] / insights["predictions_rated"]*100) 
        insights["points_per_tip"] = round(total_points_user[0]["total_points"] / insights["predictions_rated"], 2)
    else:
        insights["corr_result_p"] = 0
        insights["corr_goal_diff_p"] = 0
        insights["corr_tendency_p"] = 0
        insights["wrong_predictions_p"] = 0
        insights["points_per_tip"] = 0
    
    return insights

def is_update_needed_league_table():
    # Get current matchday by online query (returns the upcoming matchday after the middle of the week)
    current_matchday = get_current_matchday_id_openliga()

    # Get current matchday of the local database
    current_match_db = db.execute("""
                                     SELECT matches AS matchday, lastUpdateTime FROM teams
                                     ORDER BY matches DESC
                                     LIMIT 1;
                                     """)
    
    if current_matchday > current_match_db[0]["matchday"] + 1:  # +1 bcs of the current matchday calc. by openliga
        return True

    # Return last update times if the matchday is equal or + 1 ahead
    else:
         # Get last update time of the online matchdata
        lastUpdateTime_openliga = get_last_online_change(current_matchday)

        lastUpdateTime_db = current_match_db[0]["lastUpdateTime"]
        
        if lastUpdateTime_db:
            # Convert dates to comparable format
            lastUpdateTime_openliga = datetime.strptime(lastUpdateTime_openliga,  '%Y-%m-%dT%H:%M:%S.%f')
            lastUpdateTime_db = datetime.fromisoformat(lastUpdateTime_db)

            # If online data is more recent, update the database
            if lastUpdateTime_openliga > lastUpdateTime_db:
                return True
            
            else:
                return False
            
        else:
            # When there are no comparable update times, update anyway to be on the safe side
            return True


def is_update_needed_FCH_matches():
    # Get next match object through the api
    next_match_API = get_next_match_for_team_API()

    # Get upcoming matchday from the api
    next_matchday_API = next_match_API["group"]["groupOrderID"]

    # If table is empty, fill table
    empty_check_db  = db.execute("SELECT * FROM FCH_matches")

    if not empty_check_db:
        insert_matches_to_db() 

    # Get upcoming matchday of local database
    next_matchday_db = db.execute("""
                                     SELECT matchday FROM FCH_matches
                                     WHERE matchIsFinished = 0
                                     ORDER BY matchday ASC
                                     LIMIT 1;
                                     """)
    
    if not next_matchday_db:
        # If there is no 'next' matchday, current matchday has to be 34
        next_matchday_db = [34]

    print("Next matchday local: ", next_matchday_db[0]["matchday"])

    ### Compare matchdays and if they're the same check for update times

    # If the current local matchday is smaller than the current online matchday, update is needed
    if next_matchday_db[0]["matchday"] < next_matchday_API:
        return True
    
    # If the next matchdays are the same, check for last update times for these matches
    elif next_matchday_API == next_matchday_db[0]["matchday"]:
        # Get last update time of the online matchdata
        lastUpdateTime_openliga = next_match_API["lastUpdateDateTime"]

        print("last update time openliga", lastUpdateTime_openliga)

        # Get last update time of the locally saved db
        lastUpdateTime_db = db.execute("""
                                        SELECT lastUpdateDateTime FROM FCH_matches
                                        WHERE matchday = ?
                                        """,
                                        next_matchday_API)
        
        # If a last update time exists for the next match
        if lastUpdateTime_db[0]["lastUpdateDateTime"] and lastUpdateTime_openliga:
            # Convert dates to comparable format
            lastUpdateTime_openliga = datetime.strptime(lastUpdateTime_openliga, '%Y-%m-%dT%H:%M:%S.%f')
            lastUpdateTime_db = datetime.fromisoformat(lastUpdateTime_db[0]["lastUpdateDateTime"])

            # If online data is more recent, update the database
            print("Last update time openliga:", lastUpdateTime_openliga)
            print("Last update time db:", lastUpdateTime_db)
            if lastUpdateTime_openliga > lastUpdateTime_db:
                return True
            
            else:
                return False
            
        else:
            # When there are no comparable update times, update anyway to be on the safe side
            return True


def get_next_match_for_team_API():
    url = f"https://api.openligadb.de/getnextmatchbyleagueteam/{league_id}/{team_id}"

    nextmatch = get_openliga_json(url)

    if nextmatch != None:
        return nextmatch

    else:
        print("Error beim Laden von nextmatch")


def get_matchdata_openliga(id):
    url = f"https://api.openligadb.de/getmatchdata/{id}"

    matchdata = get_openliga_json(url)

    return matchdata


def convert_to_6_decimals(date_string):
    # Format dates of the API to make them usable with the datetime module. Intended to use with ISO formatted dates
    split_string = date_string.split('.')

    pre_decimals = split_string[0]
    decimals = split_string[1]

    while len(decimals) < 6:
        decimals += "0"
        
    return f"{pre_decimals}.{decimals}"


def get_last_online_change(matchday_id):
    # Make url to get last online change
    url = f"https://api.openligadb.de/getlastchangedate/{league}/{season}/{matchday_id}"

    # Query API and convert to correct format
    # (to ensure that the datetime module works correctly)
    online_change = convert_to_6_decimals(get_openliga_json(url))

    return online_change

def get_current_matchday_id_openliga():
    # Openliga DB API
    url = f"https://api.openligadb.de/getcurrentgroup/{league}"

    # Query API
    current_matchday = get_openliga_json(url)

    if current_matchday:
        return current_matchday["groupOrderID"]

    return None


def resize_image(image_path, max_size=(100, 100)):
    """ For faster load times of the page, it is useful to lower the resolution of the pictures """
    if image_path.lower().endswith(('.jpg', '.jpeg', '.png')):
        # Open the image
        with Image.open(image_path) as f:
            # Resize the image while maintaining the aspect ratio
            f.thumbnail(max_size)

            # Save the resized image to the output folder
            f.save(image_path)


def get_current_datetime():
    # Format the current date and time as a string in the desired format
    return datetime.now().isoformat()


def convert_iso_datetime_to_human_readable(datetime_iso_string):
    date = datetime.fromisoformat(datetime_iso_string)

    date.weekday()

    weekday_names = ["Mo.", "Di.", "Mi.", "Do.", "Fr.", "Sa.", "So."]

    # Format the datetime object into a more readable format
    match_time_readable = f"{weekday_names[date.weekday()]} {date.strftime('%d.%m.%Y %H:%M')}"

    return match_time_readable


def get_rangliste_data():
    predictions = db.execute("""SELECT u.id, u.username, u.total_points, u.correct_result, u.correct_goal_diff, u.correct_tendency, 
                             s.matchday, s.match_id, s.team1_score, s.team2_score, s.points
                             FROM users AS u
                             LEFT JOIN (
                                SELECT p.user_id, p.matchday, p.match_id, p.team1_score, p.team2_score, p.points
                                FROM predictions AS p
                                ORDER BY p.matchday ASC
                             ) AS s
                             ON u.id = s.user_id
                             ORDER BY u.total_points DESC, u.correct_result DESC, u.correct_goal_diff DESC, u. correct_tendency
                    """) # query with help from chatGPT
    
    
    user_predictions = {}
    for prediction in predictions:
        id, username, total_points, correct_result, correct_goal_diff, correct_tendency, matchday, match_id, team1_score, team2_score, points = prediction.values()
        if id not in user_predictions:
            user_predictions[id] = {'username':username, 'id':id, 'total_points':total_points, 'correct_result':correct_result, 
                                    'correct_goal_diff':correct_goal_diff, 'correct_tendency':correct_tendency, 'predictions':[]}

        user_predictions[id]['predictions'].append({
            'matchday': matchday,
            'match_id': match_id,
            'team1_score': team1_score,
            'team2_score': team2_score,
            'points': points})
    
    
    user_predictions_list = []
    for key in user_predictions.keys():
        user_predictions_list.append(user_predictions[key])

    return user_predictions_list