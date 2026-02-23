from flask import Flask, render_template, request, jsonify
import sqlite3
from datetime import datetime
import torch
from spellchecker import SpellChecker
from transformers import pipeline, AutoTokenizer, AutoModelForSeq2SeqLM

app = Flask(__name__)

# ================= DATABASE =================

def init_db():
    conn = sqlite3.connect("moods.db")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS moods (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mood TEXT,
            note TEXT,
            date TEXT
        )
    """)
    conn.close()

init_db()

def db_connection():
    conn = sqlite3.connect("moods.db")
    conn.row_factory = sqlite3.Row
    return conn

# ================= LOAD MODELS =================

emotion_classifier = pipeline(
    "text-classification",
    model="j-hartmann/emotion-english-distilroberta-base",
    top_k=1
)

tokenizer = AutoTokenizer.from_pretrained("google/flan-t5-base")
seq2seq_model = AutoModelForSeq2SeqLM.from_pretrained("google/flan-t5-base")

spell = SpellChecker()

# ================= HOME =================

@app.route("/")
def home():
    return render_template("index.html")

# ================= ADD MOOD =================

@app.route("/add_mood", methods=["POST"])
def add_mood():
    data = request.json
    raw_note = data.get("note", "").strip()

    if not raw_note:
        return jsonify({"error": "Empty entry"}), 400

    # Normalize + Spell correction
    note = raw_note.lower()
    corrected = []
    for word in note.split():
        fixed = spell.correction(word)
        corrected.append(fixed if fixed else word)
    corrected_note = " ".join(corrected)

    # Emotion detection
    prediction = emotion_classifier(corrected_note)[0][0]
    detected_mood = prediction["label"]

    date = datetime.now().strftime("%Y-%m-%d")

    conn = db_connection()
    conn.execute(
        "INSERT INTO moods (mood, note, date) VALUES (?, ?, ?)",
        (detected_mood, corrected_note, date)
    )
    conn.commit()
    conn.close()

    return jsonify({
        "mood": detected_mood,
        "confidence": round(prediction["score"], 2)
    })

# ================= GET MOODS =================

@app.route("/get_moods")
def get_moods():
    conn = db_connection()
    moods = conn.execute("SELECT * FROM moods ORDER BY date ASC").fetchall()
    conn.close()
    return jsonify([dict(row) for row in moods])

# ================= MONTHLY COMPARISON =================

@app.route("/monthly_comparison")
def monthly_comparison():

    conn = db_connection()
    moods = conn.execute("SELECT mood FROM moods ORDER BY date ASC").fetchall()
    conn.close()

    if not moods:
        return jsonify({"current": 0, "previous": 0, "change": 0})

    score_map = {
        "joy": 5,
        "surprise": 4,
        "neutral": 3,
        "fear": 2,
        "sadness": 1.5,
        "anger": 1,
        "disgust": 1
    }

    scores = [score_map.get(m["mood"], 3) for m in moods]

    overall_avg = sum(scores) / len(scores)
    recent = scores[-7:] if len(scores) >= 7 else scores
    recent_avg = sum(recent) / len(recent)

    change = round(recent_avg - overall_avg, 2)

    return jsonify({
        "current": round(recent_avg, 2),
        "previous": round(overall_avg, 2),
        "change": change
    })

# ================= AI INSIGHT =================

@app.route("/ai_suggestion")
def ai_suggestion():

    conn = db_connection()
    latest = conn.execute(
        "SELECT mood, note FROM moods ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()

    if not latest:
        return jsonify({"suggestion": "Start journaling to receive insight 🌿"})

    mood = latest["mood"]
    note = latest["note"]

    prompt = f"""
    The user wrote:
    "{note}"

    Detected emotion: {mood}

    DO NOT repeat the sentence.
    Provide:
    - One emotionally intelligent insight.
    - One practical suggestion related to this emotion.
    - One short reflection question.

    Keep response under 70 words.
    """

    inputs = tokenizer(prompt, return_tensors="pt", truncation=True)

    with torch.no_grad():
        outputs = seq2seq_model.generate(
            **inputs,
            max_new_tokens=90,
            temperature=0.8,
            do_sample=True,
            repetition_penalty=1.2
        )

    result = tokenizer.decode(outputs[0], skip_special_tokens=True)

    if note.lower() in result.lower():
        result = "It seems this emotion carries meaning for you. What do you think is influencing it right now?"

    return jsonify({"suggestion": result})

# ================= REFLECTION PROMPT =================

@app.route("/reflection_prompt")
def reflection_prompt():

    conn = db_connection()
    latest = conn.execute(
        "SELECT note FROM moods ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()

    if not latest:
        return jsonify({"prompt": "What are you feeling right now?"})

    note = latest["note"]

    prompt = f"""
    Based on this journal entry:
    "{note}"

    Generate one thoughtful self-reflection question.
    """

    inputs = tokenizer(prompt, return_tensors="pt", truncation=True)

    with torch.no_grad():
        outputs = seq2seq_model.generate(
            **inputs,
            max_new_tokens=60,
            temperature=0.7,
            do_sample=True
        )

    result = tokenizer.decode(outputs[0], skip_special_tokens=True)

    return jsonify({"prompt": result})

# ================= RUN =================

if __name__ == "__main__":
    app.run(debug=True)
