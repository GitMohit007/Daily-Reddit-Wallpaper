import configparser
import os
import signal
import praw
from flask import Flask, request
import webbrowser
import threading
import time
from flask_socketio import SocketIO
import requests
import platform
import ctypes
import subprocess
from datetime import datetime, timedelta

# Constants
CONFIG_FILE = 'config.ini'
WALLPAPER_DIR = 'wallpapers'
OAUTH_PORT = 65010

# Set up Flask and SocketIO for OAuth callback
app = Flask(__name__)
socketio = SocketIO(app)
auth_code = None

@app.route('/')
def index():
    global auth_code
    auth_code = request.args.get('code')
    time.sleep(1)  # Allow time for response before shutdown
    return "Authorization Complete. You may close this window."

@app.route('/shutdown', methods=['POST'])
def shutdown():
    print("Shutting down gracefully...")
    os.kill(os.getpid(), signal.SIGINT)
    return 'Server shutting down...'

def run_socketio():
    socketio.run(app, port=OAUTH_PORT)

def read_config():
    config = configparser.ConfigParser()
    config.read(CONFIG_FILE)
    return config

def save_config(config):
    with open(CONFIG_FILE, 'w') as configfile:
        config.write(configfile)

def save_code(code):
    config = read_config()
    if 'AUTH' not in config:
        config['AUTH'] = {}
    config['AUTH']['code'] = code
    save_config(config)

def load_code():
    config = read_config()
    return config.get('AUTH', 'code', fallback=None)

def delete_code():
    config = read_config()
    if config.has_option('AUTH', 'code'):
        config.remove_option('AUTH', 'code')
        save_config(config)

def write_refresh_token(refresh_token):
    config = read_config()
    if 'REDDIT' not in config:
        config['REDDIT'] = {}
    config['REDDIT']['refresh_token'] = refresh_token
    save_config(config)
    print("Refresh token saved to config.ini")

def download_image(url, filename):
    try:
        response = requests.get(url, stream=True)
        if response.status_code == 200:
            with open(filename, 'wb') as f:
                for chunk in response.iter_content(1024):
                    f.write(chunk)
            print(f"Image downloaded successfully to {filename}.")
            return filename
        else:
            print(f"Failed to download image. Status code: {response.status_code}")
    except Exception as e:
        print(f"Error downloading image: {e}")
    return None

def set_wallpaper(image_path):
    current_os = platform.system()
    abs_path = os.path.abspath(image_path)
    if current_os == "Windows":
        SPI_SETDESKWALLPAPER = 20
        if ctypes.windll.user32.SystemParametersInfoW(SPI_SETDESKWALLPAPER, 0, abs_path, 3):
            print("Wallpaper set successfully on Windows.")
        else:
            print("Failed to set wallpaper on Windows.")
    elif current_os == "Linux":
        if os.system(f"feh --bg-scale {abs_path}") == 0:
            print("Wallpaper set successfully on Linux.")
        else:
            print("Failed to set wallpaper on Linux. Is feh installed?")
    elif current_os == "Darwin":
        script = f"""osascript -e 'tell application "Finder" to set desktop picture to POSIX file "{abs_path}"'"""
        if subprocess.call(script, shell=True) == 0:
            print("Wallpaper set successfully on macOS.")
        else:
            print("Failed to set wallpaper on macOS.")
    else:
        print("Unsupported operating system for setting wallpaper.")

def choose_best_image(reddit, subreddit_names):
    best_submission = None
    cutoff_timestamp = (datetime.now() - timedelta(days=1)).timestamp()

    for sub_name in subreddit_names:
        print(f"Checking subreddit: {sub_name}")
        subreddit = reddit.subreddit(sub_name.strip())
        for submission in subreddit.new(limit=100):
            if submission.url.lower().endswith(('.jpg', '.jpeg', '.png')) and submission.created_utc >= cutoff_timestamp:
                print(f"Found {submission.url} with score {submission.score}")
                if best_submission is None or submission.score > best_submission.score:
                    best_submission = submission
    return best_submission

def init_reddit():
    config = read_config()
    try:
        client_id = config['REDDIT']['client_id']
        client_secret = config['REDDIT']['client_secret']
        redirect_uri = config['REDDIT'].get('redirect_uri', f'http://localhost:{OAUTH_PORT}')
    except KeyError:
        raise Exception("Missing Reddit credentials in config.ini under [REDDIT].")

    stored_refresh_token = config.get('REDDIT', 'refresh_token', fallback=None)
    if stored_refresh_token:
        print("Found stored refresh token:", stored_refresh_token)
        return praw.Reddit(
            client_id=client_id,
            client_secret=client_secret,
            refresh_token=stored_refresh_token,
            user_agent='my_user_agent'
        )
    else:
        return praw.Reddit(
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=redirect_uri,
            user_agent='my_user_agent'
        )

def authenticate_reddit(reddit):
    config = read_config()
    if not config.has_option('REDDIT', 'refresh_token'):
        stored_code = load_code()
        if stored_code:
            print("Found stored code:", stored_code)
            try:
                refresh_token = reddit.auth.authorize(stored_code)
                print("Stored code valid. Refresh token:", refresh_token)
            except Exception as e:
                print("Stored code failed validation:", e)
                delete_code()
                raise SystemExit("Invalid auth code. Exiting.")
        else:
            oauth_url = reddit.auth.url(scopes=["identity", "read"], state="...", duration="permanent")
            print("Opening browser for authentication...")
            webbrowser.open(oauth_url)
            flask_thread = threading.Thread(target=run_socketio, daemon=True)
            flask_thread.start()
            attempts = 50
            while auth_code is None and attempts > 0:
                time.sleep(1)
                print("Waiting for auth code...", auth_code)
                attempts -= 1
            if auth_code is None:
                raise SystemExit("Authorization failed after multiple attempts.")
            webbrowser.open(f"http://localhost:{OAUTH_PORT}/shutdown")
            try:
                refresh_token = reddit.auth.authorize(auth_code)
                print("New auth code valid. Refresh token:", refresh_token)
                save_code(auth_code)
            except Exception as e:
                delete_code()
                raise SystemExit(f"Auth code exchange failed: {e}")
            write_refresh_token(refresh_token)

def main():
    if not os.path.exists(WALLPAPER_DIR):
        os.makedirs(WALLPAPER_DIR)
        print(f"Created directory: {WALLPAPER_DIR}")

    reddit = init_reddit()
    authenticate_reddit(reddit)

    config = read_config()
    if config.has_option('SUBREDDITS', 'names'):
        subreddit_list = [name.strip() for name in config['SUBREDDITS']['names'].split(',')]
        print("Subreddits loaded:", subreddit_list)
    else:
        raise Exception("No subreddits found in config.ini under [SUBREDDITS].")

    best_submission = choose_best_image(reddit, subreddit_list)
    if best_submission is None:
        print("No valid image found in the specified subreddits.")
        return

    print(f"Best image found: {best_submission.url} with score {best_submission.score}")
    local_filename = os.path.join(WALLPAPER_DIR, f"wallpaper{datetime.now().strftime("%d%m%Y_%H%M%S")}.jpg")
    if download_image(best_submission.url, local_filename):
        set_wallpaper(local_filename)

if __name__ == '__main__':
    main()
