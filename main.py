from openai import OpenAI
from pydantic import BaseModel
from typing import List, Set
from flask import Flask, request, send_from_directory
from dotenv import load_dotenv
from flask_cors import CORS  # NEW
import json
from uuid import uuid4
import feed_generator
from concurrent.futures import ThreadPoolExecutor
import threading
import traceback
import time
import os

app = Flask(__name__)
CORS(  # NEW
    app,
    # restrict to your frontend origin in prod
    resources={r"/.*": {"origins": "*"}},
    methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)
client: OpenAI = None

data = {}
executor = ThreadPoolExecutor(max_workers=4)
data_lock = threading.RLock()
# Track which studysets currently have an active poller
active_poller_ids: Set[str] = set()


def _find_studyset(studyset_id: str):
    with data_lock:
        for ss in data.setdefault("studysets", []):
            if ss.get("id") == studyset_id:
                return ss
    return None


def get_user():
    username = request.args.get("username", type=str)
    if not username:
        return None

    with data_lock:
        users = data.setdefault("users", [])
        for u in users:
            if u.get("name") == username:
                return u

        user = {"name": username, "progress": {}}
        users.append(user)
        save_data()
        return user


def _has_pending_reels(ss: dict) -> bool:
    reels = ss.get("reels", [])
    for r in reels:
        vid = r.get("video_id")
        status = r.get("video_status")
        if vid and status not in ("success", "failed"):
            return True
    return False


def schedule_pending_reel_pollers():
    """Start pollers for any studyset that has pending reels."""
    with data_lock:
        studysets = data.get("studysets", [])
        to_schedule = [ss["id"] for ss in studysets if _has_pending_reels(ss)]
        for sid in to_schedule:
            if sid not in active_poller_ids:
                active_poller_ids.add(sid)
                executor.submit(_poll_reels_task, sid)


def _poll_reels_task(studyset_id: str):
    """Poll all reels for this studyset until finished or failed, download when ready."""
    print(f"poll_reels: start for studyset {studyset_id}")
    os.makedirs("data", exist_ok=True)
    try:
        while True:
            ss = _find_studyset(studyset_id)
            if ss is None:
                print(f"poll_reels: studyset {studyset_id} disappeared")
                return

            reels = ss.get("reels", [])
            remaining = 0

            for reel in reels:
                vid = reel.get("video_id")
                status = reel.get("video_status")
                if not vid or status in ("success", "failed"):
                    continue

                remaining += 1
                try:
                    # Retrieve latest status
                    info = client.videos.retrieve(vid)

                    new_status = info.status
                    error = info.error

                    # Update status
                    with data_lock:
                        reel["video_status"] = "failed" if error else new_status
                        if error:
                            reel["video_error"] = str(error)
                        save_data()

                    # If done, download
                    if error:
                        print(f"poll_reels: video {vid} failed: {error}")
                    elif new_status in ("completed", "succeeded", "ready"):
                        filepath = f"data/{vid}.mp4"
                        client.videos.download_content(
                            vid).write_to_file(filepath)
                        with data_lock:
                            reel["video_status"] = "success"
                            reel["video_file"] = filepath
                            save_data()
                        print(f"poll_reels: downloaded {filepath}")
                except Exception as e:
                    with data_lock:
                        reel["video_status"] = "failed"
                        reel["video_error"] = f"{e}"
                        save_data()
                    print(f"poll_reels: error polling {vid}: {e}")

            if remaining == 0:
                print(f"poll_reels: done for studyset {studyset_id}")
                return

            time.sleep(2)  # backoff between polls
    finally:
        with data_lock:
            active_poller_ids.discard(studyset_id)


def _generate_studyset_task(studyset_id: str, prompt: str, generate_reels: bool):
    try:
        print(f"generate_studysets: starting {studyset_id}")
        gen_studyset = feed_generator.generate_topics(
            client, prompt)  # returns a JSON-serializable dict
        print(f"generate_studysets: finished {studyset_id}")
        with data_lock:
            ss = _find_studyset(studyset_id)
            if ss is not None:
                ss["status"] = "ready"
                ss.update(gen_studyset)
                save_data()
    except Exception as e:
        err = f"{e}\n{traceback.format_exc()}"
        with data_lock:
            ss = _find_studyset(studyset_id)
            if ss is not None:
                ss["status"] = "error"
                ss["error"] = err
                save_data()
        return

    if generate_reels:
        # Create videos for reels and start background polling
        try:
            created_any = False
            with data_lock:
                ss = _find_studyset(studyset_id)
                reels = ss.get("reels", []) if ss else []

            for reel in reels:
                try:
                    prompt = reel.get("video_prompt")
                    if not prompt:
                        continue
                    video = client.videos.create(prompt=prompt, seconds="12")
                    vid = video.id
                    status = getattr(video, "status", "processing")
                    with data_lock:
                        reel["video_id"] = vid
                        reel["video_status"] = status
                        save_data()
                    created_any = True
                    print(
                        f"generate_studysets: created video {vid} for studyset {studyset_id}")
                except Exception as e:
                    with data_lock:
                        reel["video_status"] = "failed"
                        reel["video_error"] = f"{e}"
                        save_data()
                    print(
                        f"generate_studysets: failed to create video for reel: {e}")

            if created_any:
                with data_lock:
                    if studyset_id not in active_poller_ids:
                        active_poller_ids.add(studyset_id)
                        executor.submit(_poll_reels_task, studyset_id)
        except Exception as e:
            print(f"generate_studysets: error scheduling poller: {e}")


def generate_studyset(prompt: str, generate_reels: bool):
    id = str(uuid4())
    ss = {"id": id, "status": "pending", "prompt": prompt}

    with data_lock:
        studysets = data.setdefault("studysets", [])
        studysets.append(ss)
        save_data()

    # Run generation in the background
    executor.submit(_generate_studyset_task, id, prompt, generate_reels)
    return id


@app.route("/users/get", methods=["GET"])
def get_user_endpoint():
    user = get_user()
    if user is None:
        return {"error": "username missing"}, 400
    return user


@app.route("/studysets/get", methods=["GET"])
def get_studyset_endpoint():
    user = get_user()
    if user is None:
        return {"error": "username missing"}, 400

    studyset_id = request.args.get("id", type=str)
    if not studyset_id:
        return {"error": "id missing"}, 400

    ss = _find_studyset(studyset_id)
    if ss is None:
        return {"error": "not found"}, 404

    # Build combined feed with type and sort by (topic, section)
    feed_items = []
    for key, tname in (("question", "question"), ("reels", "reel"), ("posts", "post"), ("images", "image")):
        for obj in ss.get(key, []) or []:
            item = dict(obj)
            item["type"] = tname
            feed_items.append(item)

    # Order within a section: reels (reel), posts (post), questions (question)
    type_order = {"reel": 0, "post": 1, "question": 3, "image": 2}
    feed_items.sort(key=lambda o: (
        o.get("topic", 0),
        o.get("section", 0),
        type_order.get(o.get("type", ""), 99),
    ))

    resp = dict(ss)
    resp["feed"] = feed_items
    print(json.dumps(resp["feed"], indent=2))
    return json.dumps(resp), 200


@app.route("/studysets/create", methods=["POST"])
def create_studyset_endpoint():
    user = get_user()
    if user is None:
        return {"error": "username missing"}, 400

    if not request.is_json:
        return {"error": "Content-Type must be application/json"}, 400

    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return {"error": "invalid JSON body"}, 400

    prompt = body.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        return {"error": "prompt missing"}, 400

    generate_reels = bool(body.get("generate_reels"))

    print(generate_reels)

    id = generate_studyset(prompt.strip(), generate_reels)
    return {"id": id}, 200


@app.route("/comments/response", methods=["POST"])
def comment_response_endpoint():
    user = get_user()
    if user is None:
        return {"error": "username missing"}, 400

    if not request.is_json:
        return {"error": "Content-Type must be application/json"}, 400

    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return {"error": "invalid JSON body"}, 400

    comment = body.get("comment")
    if not isinstance(comment, str) or not comment.strip():
        return {"error": "answer missing"}, 400

    post_context = body.get("post_context")
    if not isinstance(post_context, str) or not post_context.strip():
        return {"error": "post_context missing"}, 400

    conversation = body.get("conversation")

    print(post_context)

    system_prompt = f"""
    You are an intelligent online commenter responding to another users comment. This is a instagram-like study tool where users are able to scroll through posts. Please generate a reply to this comment in a gen-z style, while being informative. If the user is not asking a serious question, feel free to make fun of them, or continue with their meme. If the user is asking a question, respond to the question is accurate information. Treat the user like any other commenter, do not address them by name. Try to keep the response brief and to the point. The response should be about a sentence or two. Don't include a lot of symbols.
        
    Post Context (What the user is commenting under):
    {post_context}
    
    Current Conversation:
    "you" refers to the user, and anybody else is a bot. If there is already a bot, keep the same bot's name and pfp_emoji for your response. If there is no bot in this conversation, make up your own profile.
    {conversation}
    """

    input_list = [{
        "role": "system",
        "content": system_prompt
    },
        {
        "role": "user",
        "content": comment
    }]

    response = client.responses.parse(model="gpt-5-mini",
                                      input=input_list,
                                      text_format=feed_generator.ScrollSectionComment)

    if response.status != "completed":
        return {"error": response.error}, 500

    return response.output_parsed.model_dump(), 200


@app.route("/images/<path:filename>", methods=["GET"])
def serve_image(filename):
    images_dir = os.path.join("data", "images")
    return send_from_directory(images_dir, filename)


def save_data():
    with data_lock:
        with open("data/data.json", "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)


if __name__ == '__main__':
    load_dotenv()
    client = OpenAI()

    try:
        with open("data/data.json", 'r', encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        os.makedirs("data", exist_ok=True)
        data = {"users": [], "studysets": []}
        save_data()
    else:
        data.setdefault("users", [])
        data.setdefault("studysets", [])

    # Ensure images directory exists
    os.makedirs(os.path.join("data", "images"), exist_ok=True)

    # Start pollers for any studysets with pending reels on startup
    schedule_pending_reel_pollers()

    app.run(debug=True, use_reloader=False)
