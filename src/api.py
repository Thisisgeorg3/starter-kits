import requests
import os
import json
import datetime
from datetime import timedelta
from datetime import datetime
import aiohttp
import requests
import json


import csv
with open('secrets.json', 'r') as secrets_file:
    secrets = json.load(secrets_file)


SLACK_URL = secrets.get("SLACK_URL")
GOOGLE_URL = secrets.get("GOOGLE_SHEET")

def send_slack_message(custom_text):
    slack_url = SLACK_URL
    google_url= GOOGLE_URL
    message = {
        "text": custom_text
    }
    headers = {
        "Content-Type": "application/json"
    }

    response = requests.post(slack_url, data=json.dumps(message), headers=headers)
    response2 = requests.post(google_url, data=json.dumps(message), headers=headers)

    if response.status_code == 200:
        print("Messages sent successfully")
    else:
        print("Error sending message to slack. Response code: " + str(response.status_code))
    if response2.status_code == 200:
        print("Messages to google sent successfully")
    else:
        print("Error sending message to google. Response code: " + str(response.status_code))



from .remove_duplicates_tweets import remove_duplicates_tweets
from .process_tweets import process_tweets


with open('secrets.json', 'r') as secrets_file:
    secrets2 = json.load(secrets_file)

BEARER_TOKEN = secrets2.get("BEARER_TOKEN")

def create_url(pagination_token=None, since_id=None):
    list_id = "1639353275777441804"
    max_results = 1
    tweet_fields = "created_at"
    expansions = "author_id"
    user_fields = "username"
    url = f"https://api.twitter.com/2/lists/{list_id}/tweets?max_results={max_results}&tweet.fields={tweet_fields}&expansions={expansions}&user.fields={user_fields}"
    if pagination_token:
        url += f"&pagination_token={pagination_token}"
    if since_id:
        url += f"&since_id={since_id}"
    return url

def create_headers(bearer_token):
    headers = {"Authorization": f"Bearer {bearer_token}"}
    return headers

def connect_to_endpoint(url, headers):
    response = requests.request("GET", url, headers=headers)
    if response.status_code != 200:
        raise Exception(response.status_code, response.text)
    return response.json()

def save_next_token(next_token):
    with open("next_token_file.json", "w") as f:
        json.dump({"next_token": next_token, "timestamp": datetime.now().isoformat()}, f)

def save_hour(hour):
    with open("next_hour_file.json", "w") as f:
        json.dump({"next_token": hour}, f)

def load_next_token():
    try:
        with open("next_token_file.json", "r") as f:
            data = json.load(f)
            next_token = data["next_token"]
            timestamp = datetime.fromisoformat(data["timestamp"])
            return next_token, timestamp
    except FileNotFoundError:
        with open("next_token_file.json", "w") as f:
            json.dump({"next_token": None, "timestamp": None}, f)
        return None, None
    
def load_last_hour():
    try:
        with open("next_hour_file.json", "r") as f:
            data = json.load(f)
            hour = data["next_token"]            
            return hour
    except FileNotFoundError:
        with open("next_hour_file.json", "w") as f:
            json.dump({"next_token": None}, f)
        return None

def save_tweets_to_csv(tweets, users):
    with open("tweets.csv", "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        for tweet in tweets:
            tweet_id = tweet["id"]
            text = tweet["text"].replace("\n", " ")
            timestamp = tweet["created_at"]
            author_id = tweet["author_id"]
            username = f'@{next(user["username"] for user in users if user["id"] == author_id)}'
            tweet_link = f'https://twitter.com/{username[1:]}/status/{tweet_id}'
            writer.writerow([tweet_id, username, text, timestamp, tweet_link])
            timestamp = datetime.fromisoformat(tweet["created_at"].rstrip("Z"))
            formatted_timestamp = timestamp.strftime('%m/%d/%Y %H:%M')
            
            send_slack_message(f"{username}, {text}, {formatted_timestamp}, {tweet_link}")

    print(tweet["created_at"])
            


async def pull_25_tweets():
    bearer_token = BEARER_TOKEN
    headers = create_headers(bearer_token)

    # Load the next token and timestamp from file if they exist
    pagination_token, last_timestamp = load_next_token()

    # Check if it's been less than an hour since the last retrieval
    if last_timestamp is None or datetime.now() - last_timestamp < timedelta(minutes=30):
        # Pull the first 25 tweets using aiohttp
        url = create_url(pagination_token)
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(url) as response:
                if response.status != 200:
                    raise Exception(response.status, await response.text())
                json_response = await response.json()

        # Save the tweets to a CSV file
        save_tweets_to_csv(json_response["data"], json_response["includes"]["users"])

        # Save the next token to file if it exists
        if "next_token" in json_response["meta"]:
            next_token = json_response["meta"]["next_token"]
            save_next_token(next_token)
    else:
        # Make a new API call with the last results using aiohttp
        url = create_url()
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(url) as response:
                if response.status != 200:
                    raise Exception(response.status, await response.text())
                json_response = await response.json()

        # Save the tweets to a CSV file
        save_tweets_to_csv(json_response["data"], json_response["includes"]["users"])

        # Save the next token to file if it exists
        if "next_token" in json_response["meta"]:
            next_token = json_response["meta"]["next_token"]
            save_next_token(next_token)

    # Run the duplicate removal script as a subprocess
    remove_duplicates_tweets()

    # Run the duplicate removal script as a subprocess
    process_tweets()