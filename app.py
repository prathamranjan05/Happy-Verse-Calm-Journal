import os
import sys
import sqlite3
import gc
from datetime import datetime
from pathlib import Path
from flask import Flask, render_template, request, jsonify
from spellchecker import SpellChecker
import torch

# ================= RENDER CONFIG =================
# Check if running on Render
IS_RENDER = os.environ.get('RENDER', False)
# Use /tmp for writable storage on Render
DB_PATH = '/tmp/moods.db' if IS_RENDER else 'moods.db'
# Disable tokenizers parallelism to avoid warnings
os.environ["TOKENIZERS_PARALLELISM"] = "false"

app = Flask(__name__)

# ================= LAZY MODEL LOADING =================
# Only import heavy libraries when needed
_model_cache = {}
_spell_checker = None

def get_spellchecker():
    """Lazy load spell checker"""
    global _spell_checker
    if _spell_checker is None:
        _spell_checker = SpellChecker()
    return _spell_checker

def get_emotion_classifier():
    """Lazy load emotion classifier with Render optimizations"""
    if 'emotion' not in _model_cache:
        from transformers import pipeline
        
        # Use smaller model and aggressive memory optimization
        _model_cache['emotion'] = pipeline(
            "text-classification",
            model="j-hartmann/emotion-english-distilroberta-base",
            top_k=1,
            device=-1,  # Force CPU
            framework="pt",
            model_kwargs={
                "low_cpu_mem_usage": True,
                "torch_dtype": torch.float32
            }
        )
    return _model_cache['emotion']

def get_seq2seq_model():
    """Lazy load seq2seq model with memory optimizations"""
    if 'seq2seq' not in _model_cache:
        from transformers import AutoTokenizer, AutoModelForSeq2SeqLM
        
        # Use smaller variant for memory constraints
        model_name = "google/flan-t5-small"  # Changed from base to small
        
        tokenizer = AutoTokenizer.from_pretrained(
            model_name, 
            use_fast=True,
            model_max_length=256
        )
        model = AutoModelForSeq2SeqLM.from_pretrained(
            model_name,
            low_cpu_mem_usage=True,
            torch_dtype=torch.float32
        )
        model.eval()
        
        _model_cache['seq2seq_tokenizer'] = tokenizer
        _model_cache['seq2seq_model'] = model
    
    return _model_cache['seq2seq_tokenizer'], _model_cache['seq2seq_model']

def clear_model_cache():
    """Force clear model cache to free memory"""
    global _model_cache
    for key in list(_model_cache.keys()):
        del _model_cache[key]
    _model_cache = {}
    
    # Aggressive garbage collection
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()

# ================= DATABASE =================

def init_db():
    """Initialize database with Render compatibility"""
    # Ensure directory exists for non-Render environments
    if not IS_RENDER:
        Path('.').mkdir(exist_ok=True)
    
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS moods (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mood TEXT,
            note TEXT,
            date TEXT
        )
    """)
    
    # Optimize SQLite for better performance
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=-2000")  # Use 2MB cache
    
    conn.commit()
    conn.close()

def db_connection():
    """Get database connection with row factory"""
    conn = sqlite3.connect(DB_PATH, timeout=10)  # Add timeout for concurrent access
    conn.row_factory = sqlite3.Row
    
    # Apply performance optimizations per connection
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    
    return conn

# Initialize database
init_db()

# ================= ROUTES =================

@app.route("/")
def home():
    return render_template("index.html")

@app.route("/health")
def health():
    """Health check endpoint for Render"""
    return jsonify({
        "status": "healthy",
        "environment": "render" if IS_RENDER else "local",
        "memory_usage": get_memory_usage()
    })

def get_memory_usage():
    """Get current memory usage"""
    import psutil
    process = psutil.Process()
    memory_info = process.memory_info()
    return {
        "rss": memory_info.rss / 1024 / 1024,  # MB
        "vms": memory_info.vms / 1024 / 1024   # MB
    }

@app.route("/add_mood", methods=["POST"])
def add_mood():
    try:
        data = request.json
        raw_note = data.get("note", "").strip()

        if not raw_note:
            return jsonify({"error": "Empty entry"}), 400

        # Spell correction (lightweight)
        spell = get_spellchecker()
        note = raw_note.lower()
        corrected = []
        for word in note.split():
            fixed = spell.correction(word)
            corrected.append(fixed if fixed else word)
        corrected_note = " ".join(corrected)

        # Emotion detection - with timeout protection
        try:
            emotion_classifier = get_emotion_classifier()
            prediction = emotion_classifier(corrected_note)[0][0]
            detected_mood = prediction["label"]
            confidence = round(prediction["score"], 2)
        except Exception as e:
            # Fallback if model fails
            print(f"Model error: {e}", file=sys.stderr)
            detected_mood = "neutral"
            confidence = 0.5

        date = datetime.now().strftime("%Y-%m-%d")

        # Database operation
        conn = db_connection()
        conn.execute(
            "INSERT INTO moods (mood, note, date) VALUES (?, ?, ?)",
            (detected_mood, corrected_note, date)
        )
        conn.commit()
        conn.close()

        # Periodically clear cache on Render (every 10 requests)
        if IS_RENDER and hasattr(app, 'request_counter'):
            app.request_counter += 1
            if app.request_counter % 10 == 0:
                clear_model_cache()

        return jsonify({
            "mood": detected_mood,
            "confidence": confidence
        })
    
    except Exception as e:
        print(f"Error in add_mood: {e}", file=sys.stderr)
        return jsonify({"error": "Internal server error"}), 500

@app.route("/get_moods")
def get_moods():
    try:
        conn = db_connection()
        moods = conn.execute("SELECT * FROM moods ORDER BY date ASC").fetchall()
        conn.close()
        return jsonify([dict(row) for row in moods])
    except Exception as e:
        print(f"Error in get_moods: {e}", file=sys.stderr)
        return jsonify([]), 200

@app.route("/monthly_comparison")
def monthly_comparison():
    try:
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
        recent_avg = sum(recent) / len(recent) if recent else 0

        change = round(recent_avg - overall_avg, 2)

        return jsonify({
            "current": round(recent_avg, 2),
            "previous": round(overall_avg, 2),
            "change": change
        })
    except Exception as e:
        print(f"Error in monthly_comparison: {e}", file=sys.stderr)
        return jsonify({"current": 0, "previous": 0, "change": 0})

@app.route("/ai_suggestion")
def ai_suggestion():
    try:
        conn = db_connection()
        latest = conn.execute(
            "SELECT mood, note FROM moods ORDER BY id DESC LIMIT 1"
        ).fetchone()
        conn.close()

        if not latest:
            return jsonify({"suggestion": "Start journaling to receive insight 🌿"})

        mood = latest["mood"]
        note = latest["note"]

        # Use a simple template-based response if model fails
        simple_suggestions = {
            "joy": "Your joy is beautiful. What's one way you can share this positive energy today?",
            "sadness": "It's okay to feel sad. Consider talking to someone you trust.",
            "anger": "Anger can be a signal. Maybe take a few deep breaths.",
            "fear": "Fear is natural. What's one small step you can take?",
            "neutral": "A calm moment. What would bring you joy right now?"
        }

        try:
            tokenizer, model = get_seq2seq_model()
            
            prompt = f"""
            The user wrote: "{note}"
            Detected emotion: {mood}
            Provide one brief insight and one practical suggestion.
            Keep it under 50 words.
            """

            inputs = tokenizer(
                prompt, 
                return_tensors="pt", 
                truncation=True, 
                max_length=192  # Reduced for memory
            )

            with torch.no_grad():
                outputs = model.generate(
                    **inputs,
                    max_new_tokens=60,  # Reduced
                    temperature=0.7,
                    do_sample=True,
                    num_beams=1,
                    repetition_penalty=1.1
                )

            suggestion = tokenizer.decode(outputs[0], skip_special_tokens=True)
            
            # Fallback if generation is too short or contains the original
            if len(suggestion) < 20 or note.lower() in suggestion.lower():
                suggestion = simple_suggestions.get(mood, "How are you feeling about this?")
                
        except Exception as e:
            print(f"Model error in ai_suggestion: {e}", file=sys.stderr)
            suggestion = simple_suggestions.get(mood, "What would help you right now?")

        return jsonify({"suggestion": suggestion})
    
    except Exception as e:
        print(f"Error in ai_suggestion: {e}", file=sys.stderr)
        return jsonify({"suggestion": "Take a moment to reflect on your feelings."})

@app.route("/reflection_prompt")
def reflection_prompt():
    try:
        conn = db_connection()
        latest = conn.execute(
            "SELECT note FROM moods ORDER BY id DESC LIMIT 1"
        ).fetchone()
        conn.close()

        if not latest:
            return jsonify({"prompt": "What are you feeling right now?"})

        note = latest["note"]

        # Simple fallback prompts
        prompts = [
            "What triggered this emotion?",
            "How does this feeling affect your day?",
            "What would you like to change about this situation?",
            "What support do you need right now?"
        ]
        import random

        try:
            tokenizer, model = get_seq2seq_model()
            
            prompt = f"Generate one reflection question about: '{note}'"

            inputs = tokenizer(prompt, return_tensors="pt", truncation=True, max_length=128)

            with torch.no_grad():
                outputs = model.generate(
                    **inputs,
                    max_new_tokens=40,
                    temperature=0.7,
                    do_sample=True,
                    num_beams=1
                )

            result = tokenizer.decode(outputs[0], skip_special_tokens=True)
            
            # Fallback if generation fails validation
            if len(result) < 10 or "?" not in result:
                result = random.choice(prompts)
                
        except Exception:
            result = random.choice(prompts)

        return jsonify({"prompt": result})
    
    except Exception as e:
        print(f"Error in reflection_prompt: {e}", file=sys.stderr)
        return jsonify({"prompt": "What's on your mind right now?"})

# ================= CLEANUP =================

@app.before_request
def before_request():
    """Initialize request counter for Render"""
    if not hasattr(app, 'request_counter'):
        app.request_counter = 0

@app.teardown_appcontext
def cleanup(error):
    """Optional cleanup after each request"""
    # Only clear on Render when memory is tight
    if IS_RENDER and app.request_counter % 5 == 0:
        import psutil
        memory_percent = psutil.virtual_memory().percent
        if memory_percent > 80:  # If memory usage > 80%
            clear_model_cache()

# ================= RUN =================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = not IS_RENDER  # Disable debug on Render
    app.run(host="0.0.0.0", port=port, debug=debug, threaded=False)