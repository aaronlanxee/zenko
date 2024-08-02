from flask import Flask, flash, redirect, render_template, request, session, send_file
from flask_socketio import SocketIO, send, join_room, leave_room, rooms, emit
from cs50 import SQL

from flask_session import Session
from werkzeug.security import check_password_hash, generate_password_hash

from helpers import login_required
from pytubefix import Search

import re
import os

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret!'
app.config["SESSION_PERMANENT"] = False
app.config["SESSION_TYPE"] = "filesystem"
socketio = SocketIO(app)
Session(app)

db = SQL("sqlite:///zenko.db")


@app.route("/", methods=["GET", "POST"])
@login_required
def index():
    convos = db.execute("SELECT * FROM conversations JOIN conversation_members ON id = conversation_id WHERE member_id = ?", session["user_id"])
    convos.reverse()
    return render_template("main.html", username=session["username"], convos=convos)



@app.route("/login", methods=["GET", "POST"])
def login():

    session.clear()

    if request.method == "POST":
        username = request.form.get("username").strip().lower()
        password = request.form.get("password").strip().lower()
        if not username:
            flash("Must provide a username!")
            return render_template("login.html")

        elif not password:
            flash("Must provide a password!")
            return render_template("login.html")

        userinfo = db.execute("SELECT * FROM users WHERE username = ?", username)

        if len(userinfo) != 1 or not check_password_hash(userinfo[0]["hash"], password):
            flash("Invalid username or password")
            return render_template("login.html")

        session["user_id"] = userinfo[0]["id"]
        session["username"] = username
        session["prev_music"] = ""

        return redirect("/")
    else:
        return render_template("login.html")

@app.route("/logout")
@login_required
def logout():
    session.clear()

    flash("Logged out!")
    return redirect("/")

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username").strip().lower()
        password = request.form.get("password").strip().lower()
        verify = request.form.get("verify").strip()

        if not username:
            flash("Must provide username!")
            return render_template("register.html")
        elif not password:
            flash("must provide password!")
            return render_template("register.html")
        elif not verify:
            flash("must confirm password!")
            return render_template("register.html")
        elif len(db.execute("SELECT * FROM users WHERE username = ?", username)):
            flash("Username already used")
            return render_template("register.html")
        elif not password == verify:
            flash("Password not match")
            return render_template("register.html")
        elif len(username) < 11:
            flash("Username must greater than 11 characters")
            return render_template("register.html")
        elif len(password) < 11:
            flash("Password must greater than 11 characters")
            return render_template("register.html")

        hash = generate_password_hash(password)
        db.execute("INSERT INTO users (username, hash) VALUES (?, ?)", username, hash)

        return redirect("/login")
    else:
        return render_template("register.html")


@app.route("/newroom", methods=["POST"])
def newchat():
    chatname = request.form.get("chatname")
    code = db.execute("SELECT abs(random() % 10000) AS code FROM sqlite_master WHERE code NOT IN (SELECT code FROM conversations) LIMIT 1")[0]["code"]
    db.execute("INSERT INTO conversations (name, code) VALUES (?, ?)", chatname, code)
    convo_id = db.execute("SELECT id FROM conversations WHERE code = ?", code)[0]["id"]
    db.execute("INSERT INTO conversation_members (member_id, conversation_id) VALUES(?, ?)", session["user_id"], convo_id)
    return redirect("/")

@app.route("/joinroom", methods=["POST"])
def joinchat():
    chatcode = int(request.form.get("chatcode"))
    if chatcode not in [ code["code"] for code in db.execute("SELECT * FROM conversations")]:
        flash("ChatRoom Does'nt Exist!")
        return redirect("/")
    convo_id = db.execute("SELECT id FROM conversations WHERE code = ?", chatcode)[0]["id"]
    db.execute("INSERT INTO conversation_members (member_id, conversation_id) VALUES(?, ?)", session["user_id"], convo_id)
    return redirect("/")

@app.route("/leaveroom", methods=["POST"])
def leaveroom():
    chatcode = int(request.form.get("chatcode"))
    convo_id = db.execute("SELECT id FROM conversations WHERE code = ?", chatcode)[0]["id"]
    db.execute("DELETE FROM conversation_members WHERE member_id = ? AND conversation_id = ?", session["user_id"], convo_id)
    if len(db.execute("SELECT * FROM conversation_members WHERE conversation_id = ?", convo_id)) == 0:
        db.execute("DELETE FROM messages WHERE conversation_id = ?", convo_id)
        db.execute("DELETE FROM conversations WHERE id = ?", convo_id)
    return redirect("/")

# Handle the socketIO
@socketio.on("message")
def handle_message(message):
    if match := re.match(r"^\$play ([\s\S]+-[\s\S]+)$", message["message"].strip().lower()):
        if os.path.exists(session["prev_music"]):
            os.remove(session["prev_music"])
        yt = Search(match.group(1)).videos[0]
        path = yt.streams.get_audio_only().download(filename=f"{yt.title}.mp3", output_path="static/buffer")
        music_src = f"static/buffer/{yt.title}.mp3"

        Mmessages = {
            "username": message["username"],
            "message": yt.title,
            "music_src": music_src
        }
        session["prev_music"] = path
        emit('music', Mmessages, room=message["room"])
    elif re.match(r"^\$stop$", message["message"].strip().lower()):
        Mmessages = {
            "username": message["username"],
            "message": "Stopped the music",
            "music_src": "Stop"
        }
        emit('music', Mmessages, room=message["room"])
    elif re.match(r"^\$leave$", message["message"].strip().lower()):
        Mmessages = {
            "username": message["username"],
            "message": "Left the room",
            "room": message["room"]
        }
        emit('leaveroom', Mmessages, room=message["room"])
    else:
        content = {
        "username": message["username"],
        "message": message["message"],
        }
        send(content, room=message["room"])
        convo_id = db.execute("SELECT id FROM conversations WHERE code = ?", int(message["room"]))[0]["id"]
        db.execute("INSERT INTO messages (conversation_id, sender_id, content) VALUES(?, ?, ?)", convo_id, session["user_id"], message["message"])

@socketio.on("leave")
def handle_leave(data):
    if os.path.exists(session["prev_music"]):
        os.remove(session["prev_music"])
    leave_room(data["room"])
    send({"username": data["username"], "message": "Left the room", "music_src": "None"}, room=data["room"])


@socketio.on("join")
def handle_join(data):
    join_room(data["room"])
    send({"username": data["username"], "message": "Entered the room",  "music_src": "None"}, room=data["room"])
    messagelist = db.execute("SELECT * FROM users JOIN messages ON users.id = sender_id JOIN conversations ON conversation_id = conversations.id WHERE code = ?", int(data["room"]))
    emit('messagelist', messagelist, room=data["room"])

@socketio.on('disconnect')
def handle_disconnect():
    if os.path.exists(session["prev_music"]):
        os.remove(session["prev_music"])
    send({"username": data["username"], "message": "Left the room", "music_src": "None"}, room=data["room"])

if __name__ == "__main__":
    socketio.run(app, debug=True)
