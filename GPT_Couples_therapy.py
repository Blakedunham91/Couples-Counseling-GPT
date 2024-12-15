from flask import Flask, request, jsonify, render_template, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from dotenv import load_dotenv
from openai import OpenAI, APIError, RateLimitError
import os
import json

# Load environment variables
load_dotenv()

# Initialize Flask app
app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY")
if not app.secret_key:
    raise ValueError("SECRET_KEY not set in the .env file.")

app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///users.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Initialize database
db = SQLAlchemy(app)

# Initialize login manager
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"

# Initialize OpenAI client
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# Models
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)

class GPTData(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    input_text = db.Column(db.Text, nullable=False)
    gpt_response = db.Column(db.Text, nullable=False)

# Login manager user loader
@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# Routes
@app.route("/", methods=['GET'])
def home():
    return redirect(url_for("login"))

@app.route("/login", methods=['GET', 'POST'])
def login():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]

        # Load credentials from .env
        blake_username = os.getenv("BLAKE_USERNAME")
        blake_password = os.getenv("BLAKE_PASSWORD")
        chona_username = os.getenv("CHONA_USERNAME")
        chona_password = os.getenv("CHONA_PASSWORD")

        # Authenticate user
        if (username == blake_username and password == blake_password) or \
           (username == chona_username and password == chona_password):
            user = User.query.filter_by(username=username).first()
            if not user:
                # Create user in the database if they don't exist
                user = User(username=username)
                db.session.add(user)
                db.session.commit()

            login_user(user)
            return redirect(url_for("dashboard"))

        flash("Invalid username or password", "danger")
    return render_template("login.html")

@app.route("/logout")
@login_required
def logout():
    logout_user()
    flash("Logged out successfully.", "success")
    return redirect(url_for("login"))

@app.route("/dashboard", methods=["GET", "POST"])
@login_required
def dashboard():
    if request.method == "POST":
        # Parse incoming JSON data
        data = request.get_json()
        user_input = data.get("message")
        chat_type = data.get("chat_type")  # 'private' or 'mediator'

        # Validate input
        if not user_input or chat_type not in ["private", "mediator"]:
            return jsonify({"error": "Invalid input. Ensure message and chat_type are provided."}), 400

        try:
            # Retrieve the last 10 messages for context
            conversation_history = GPTData.query.filter_by(user_id=current_user.id).order_by(GPTData.id.desc()).limit(10).all()
            context = [
                {"role": "user", "content": convo.input_text} if convo.input_text else
                {"role": "assistant", "content": convo.gpt_response}
                for convo in reversed(conversation_history)
            ]
            context.append({"role": "user", "content": user_input})

            # System prompts based on chat type
            system_prompts = {
                "private": "You are a professional couples counselor inspired by Abraham Hicks. Provide compassionate guidance.",
                "mediator": "You are a neutral mediator helping couples communicate effectively. Provide constructive feedback."
            }

            # Prepare API payload
            messages = [{"role": "system", "content": system_prompts[chat_type]}] + context

            # Call OpenAI API
            response = client.chat.completions.create(
                model="gpt-4",
                messages=messages
            )
            gpt_response = response.choices[0].message.content

            # Save conversation to the database
            gpt_data = GPTData(user_id=current_user.id, input_text=user_input, gpt_response=gpt_response)
            db.session.add(gpt_data)
            db.session.commit()

            return jsonify({"role": "assistant", "content": gpt_response})

        except RateLimitError:
            return jsonify({"error": "Rate limit reached. Retry later."}), 429
        except APIError as e:
            return jsonify({"error": f"Error communicating with OpenAI API: {e}"}), 500

    # GET request: Retrieve recent chat history
    chat_history = GPTData.query.filter_by(user_id=current_user.id).order_by(GPTData.id.desc()).limit(10).all()
    private_chat = [
        {"role": "user", "content": convo.input_text} if convo.input_text else
        {"role": "assistant", "content": convo.gpt_response}
        for convo in reversed(chat_history)
    ]

    return render_template("couples_dash.html", private_chat=private_chat)

@app.route("/upload-history", methods=["POST"])
@login_required
def upload_history():
    file = request.files.get("file")
    if file and file.content_type == 'application/json':
        try:
            history = json.loads(file.read().decode("utf-8"))
            for entry in history:
                db.session.add(GPTData(
                    user_id=current_user.id,
                    input_text=entry.get("user_input", ""),
                    gpt_response=entry.get("gpt_response", "")
                ))
            db.session.commit()
            flash("History uploaded successfully.", "success")
        except Exception as e:
            flash(f"Failed to upload history: {e}", "danger")
    else:
        flash("Invalid file type. Please upload a JSON file.", "danger")
    return redirect(url_for("dashboard"))

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(host="0.0.0.0", port=5000)