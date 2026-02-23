from flask import Flask, render_template, request, jsonify
import sqlite3
from datetime import datetime
import torch
from spellchecker import SpellChecker
from transformers import pipeline, AutoTokenizer, AutoModelForSeq2SeqLM
import gc

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

# ================= LAZY LOAD MODELS =================
# Store models in a cache that loads on first use
_model_cache = {}

def get_emotion_classifier():
    """Lazy load emotion classifier"""
    if 'emotion' not in _model_cache:
        # Use smaller model and CPU optimizations
        _model_cache['emotion'] = pipeline(
            "text-classification",
            model="j-hartmann/emotion-english-distilroberta-base",
            top_k=1,
            device=-1,  # Force CPU
            framework="pt",
            model_kwargs={"low_cpu_mem_usage": True}
        )
    return _model_cache['emotion']

def get_seq2seq_model():
    """Lazy load seq2seq model with memory optimizations"""
    if 'seq2seq' not in _model_cache:
        # Load with memory optimizations
        tokenizer = AutoTokenizer.from_pretrained("google/flan-t5-base", use_fast=True)
        model = AutoModelForSeq2SeqLM.from_pretrained(
            "google/flan-t5-base",
            low_cpu_mem_usage=True,
            torch_dtype=torch.float32  # Use float32 explicitly
        )
        model.eval()  # Set to evaluation mode
        _model_cache['seq2seq_tokenizer'] = tokenizer
        _model_cache['seq2seq_model'] = model
    
    return _model_cache['seq2seq_tokenizer'], _model_cache['seq2seq_model']

def clear_model_cache():
    """Clear model cache to free memory"""
    global _model_cache
    for model in _model_cache.values():
        if hasattr(model, 'to'):
            model.to('cpu')
        del model
    _model_cache = {}
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()

# Initialize spell checker (lightweight)
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

    # Normalize + Spell correction (lightweight operation)
    note = raw_note.lower()
    corrected = []
    for word in note.split():
        fixed = spell.correction(word)
        corrected.append(fixed if fixed else word)
    corrected_note = " ".join(corrected)

    # Emotion detection - load model only when needed
    emotion_classifier = get_emotion_classifier()
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

    # Load models only for this request
    tokenizer, model = get_seq2seq_model()
    
    inputs = tokenizer(prompt, return_tensors="pt", truncation=True, max_length=256)

    with torch.no_grad():
        outputs = model.generate(
            **inputs,
            max_new_tokens=90,
            temperature=0.8,
            do_sample=True,
            repetition_penalty=1.2,
            num_beams=1  # Use greedy search instead of beam search
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

    # Load models only for this request
    tokenizer, model = get_seq2seq_model()
    
    inputs = tokenizer(prompt, return_tensors="pt", truncation=True, max_length=256)

    with torch.no_grad():
        outputs = model.generate(
            **inputs,
            max_new_tokens=60,
            temperature=0.7,
            do_sample=True,
            num_beams=1  # Use greedy search
        )

    result = tokenizer.decode(outputs[0], skip_special_tokens=True)

    return jsonify({"prompt": result})

# ================= CLEANUP =================

@app.teardown_appcontext
def cleanup(error):
    """Clear model cache periodically to free memory"""
    # Uncomment if you want to clear cache after each request (slower but less memory)
    # clear_model_cache()
    pass

# Optional: Add endpoint to manually clear cache
@app.route("/clear_cache", methods=["POST"])
def manual_clear_cache():
    clear_model_cache()
    return jsonify({"status": "Cache cleared"})

# ================= RUN =================

if __name__ == "__main__":
    app.run(debug=True, threaded=False)  # Disable threading for less memory overhead