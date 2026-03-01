import os
import sqlite3
import random
import re
from datetime import datetime
from flask import Flask, render_template, request, jsonify
from spellchecker import SpellChecker

app = Flask(__name__)

# ================= SPELL CHECKER =================
spell = SpellChecker()

# ================= DATABASE =================
DB_PATH = '/tmp/moods.db' if os.environ.get('RENDER') else 'moods.db'

def init_db():
    conn = sqlite3.connect(DB_PATH)
    # Moods table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS moods (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mood TEXT,
            note TEXT,
            date TEXT
        )
    """)
    # Prompt history table for progressive tracking
    conn.execute("""
        CREATE TABLE IF NOT EXISTS prompt_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mood TEXT,
            prompt TEXT,
            date TEXT
        )
    """)
    conn.close()

init_db()

def db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

# ================= SPELL CHECK FUNCTION =================
def correct_spelling(text):
    """Correct spelling in the given text"""
    words = text.split()
    corrected_words = []
    
    for word in words:
        # Get correction (returns None if word is misspelled)
        correction = spell.correction(word)
        # Use correction if available, otherwise keep original
        corrected_words.append(correction if correction else word)
    
    return " ".join(corrected_words)

# ================= MOOD DETECTION =================
def detect_mood(text):
    text = text.lower()
    
    mood_keywords = {
        "joy": ["happy", "joy", "great", "awesome", "love", "good", "excited", "wonderful", "amazing", "blessed"],
        "sadness": ["sad", "unhappy", "down", "depressed", "blue", "crying", "heartbroken", "gloomy", "miserable"],
        "anger": ["angry", "mad", "frustrated", "annoyed", "hate", "furious", "irritated", "pissed"],
        "fear": ["scared", "afraid", "worried", "anxious", "nervous", "terrified", "panic", "stressed"],
        "surprise": ["surprised", "shocked", "wow", "unexpected", "amazed", "astonished"],
        "disgust": ["disgusted", "gross", "awful", "terrible", "horrible", "yuck", "disappointed"]
    }
    
    scores = {}
    for mood, keywords in mood_keywords.items():
        scores[mood] = sum(1 for k in keywords if k in text)
    
    if max(scores.values()) > 0:
        detected_mood = max(scores, key=scores.get)
        confidence = scores[detected_mood] / 10
        return detected_mood, min(confidence, 0.95)
    
    return "neutral", 0.5

# ================= TEMPLATE-BASED PROMPTS BY MOOD =================
def get_prompt_templates(mood):
    """Return prompt templates for a specific mood"""
    
    templates = {
        "joy": [
            "What made you feel {word} today?",
            "How can you create more {word} moments?",
            "Who would appreciate sharing your {word}?",
            "What's the best part about feeling {word}?",
            "How can you spread this {word} to others?",
            "What does '{word}' mean to you right now?",
            "What triggered this feeling of {word}?",
            "How long have you been feeling {word}?",
            "What would amplify this sense of {word}?",
            "Where in your body do you feel this {word}?"
        ],
        "sadness": [
            "What would comfort you about '{word}'?",
            "When did you start feeling {word} about this?",
            "What's one small thing that might lift the {word}?",
            "Who could you talk to about feeling {word}?",
            "What usually helps when you feel {word}?",
            "Is there a past experience with {word} that you learned from?",
            "What does this feeling of {word} need from you?",
            "If {word} had a color, what would it be?",
            "What's underneath the {word}?",
            "How can you be kind to yourself in this {word} moment?"
        ],
        "anger": [
            "What specifically triggered this {word}?",
            "What would help release this {word}?",
            "Is there a boundary being crossed that's causing {word}?",
            "What would a fair resolution to this {word} look like?",
            "How can you channel this {word} constructively?",
            "What's the real need behind this {word}?",
            "If this {word} could speak, what would it say?",
            "What would you need to let go of this {word}?",
            "How does this {word} affect your body?",
            "What would your wiser self advise about this {word}?"
        ],
        "fear": [
            "What's the worst that could happen with '{word}'?",
            "What helps you feel safe when you're {word}?",
            "What evidence contradicts this feeling of {word}?",
            "What's one small step you could take despite the {word}?",
            "Who would understand your {word}?",
            "What past {word} turned out okay?",
            "What would make this {word} more manageable?",
            "Is this {word} trying to protect you from something?",
            "What do you need to hear right now about this {word}?",
            "How real is the thing causing {word}?"
        ],
        "neutral": [
            "What would add a spark to your {word} day?",
            "What's one thing you're curious about in this {word} moment?",
            "How would you like to feel differently from {word}?",
            "What's one small experiment you could try today?",
            "What does your ideal day look like?",
            "What's something you've been postponing?",
            "What energy would you like to cultivate?",
            "Who inspires you right now?",
            "What's one thing you're grateful for?",
            "What would make today slightly better than {word}?"
        ],
        "surprise": [
            "Was this {word} welcome or unwelcome?",
            "How does this {word} change things?",
            "What opportunity might this {word} bring?",
            "What's your first instinct about this {word}?",
            "How will you respond to this {word}?",
            "What feels different after this {word}?",
            "Who would you tell about this {word}?",
            "What does this {word} reveal about your priorities?",
            "How are you processing this {word}?",
            "What's one thing you learned from this {word}?"
        ],
        "disgust": [
            "What specifically about '{word}' bothers you?",
            "Is there something you need to distance from?",
            "What would feel more aligned than this {word}?",
            "How can you honor this feeling of {word} appropriately?",
            "What values are being challenged here?",
            "What would restore your sense of {word}?",
            "How does this {word} manifest physically?",
            "What would you rather focus on instead?",
            "Is this {word} about the situation or something deeper?",
            "What boundary might need reinforcing?"
        ]
    }
    
    return templates.get(mood, templates["neutral"])

# ================= EXTRACT KEYWORDS FROM NOTE =================
def extract_keywords(text, max_words=3):
    """Extract meaningful keywords from text for templates"""
    # Remove common words and punctuation
    text = text.lower()
    text = re.sub(r'[^\w\s]', '', text)
    
    # Common words to filter out
    stop_words = {'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for', 
                  'of', 'with', 'by', 'from', 'up', 'about', 'into', 'through', 'during',
                  'before', 'after', 'between', 'under', 'again', 'further', 'then', 'once',
                  'here', 'there', 'when', 'where', 'why', 'how', 'all', 'any', 'both', 
                  'each', 'few', 'more', 'most', 'other', 'some', 'such', 'no', 'nor', 
                  'not', 'only', 'own', 'same', 'so', 'than', 'too', 'very', 's', 't', 
                  'can', 'will', 'just', 'don', 'should', 'now', 'i', 'me', 'my', 'myself',
                  'we', 'our', 'ours', 'ourselves', 'you', 'your', 'yours', 'yourself',
                  'he', 'him', 'his', 'himself', 'she', 'her', 'hers', 'herself',
                  'it', 'its', 'itself', 'they', 'them', 'their', 'theirs', 'themselves',
                  'am', 'is', 'are', 'was', 'were', 'be', 'been', 'being', 'have', 'has',
                  'had', 'having', 'do', 'does', 'did', 'doing', 'would', 'could', 'should',
                  'might', 'must', 'im', 'ive', 'id', 'youre', 'youve', 'youll', 'youd',
                  'hes', 'hes', 'hES', 'hES', 'shes', 'she', 'its', 'were', 'theyre',
                  'theyve', 'theyll', 'theyd'}
    
    words = text.split()
    # Filter out stop words and short words
    keywords = [w for w in words if w not in stop_words and len(w) > 3]
    
    # If no keywords found, use mood-appropriate defaults
    if not keywords:
        return ["today"]
    
    # Return unique keywords up to max_words
    seen = set()
    unique_keywords = []
    for w in keywords:
        if w not in seen:
            seen.add(w)
            unique_keywords.append(w)
    
    return unique_keywords[:max_words]

# ================= ROUTES =================

@app.route("/")
def home():
    return render_template("index.html")

@app.route("/add_mood", methods=["POST"])
def add_mood():
    data = request.json
    note = data.get("note", "").strip()
    
    if not note:
        return jsonify({"error": "Empty entry"}), 400
    
    # Spell check the note
    corrected_note = correct_spelling(note)
    
    # Detect mood from corrected note
    mood, confidence = detect_mood(corrected_note.lower())
    date = datetime.now().strftime("%Y-%m-%d")
    
    conn = db_connection()
    conn.execute(
        "INSERT INTO moods (mood, note, date) VALUES (?, ?, ?)",
        (mood, corrected_note, date)  # Store the corrected version
    )
    conn.commit()
    conn.close()
    
    return jsonify({
        "mood": mood,
        "confidence": round(confidence, 2),
        "corrected": corrected_note != note  # Optional: let frontend know if corrections were made
    })

@app.route("/get_moods")
def get_moods():
    conn = db_connection()
    moods = conn.execute("SELECT * FROM moods ORDER BY date ASC").fetchall()
    conn.close()
    return jsonify([dict(row) for row in moods])

@app.route("/monthly_comparison")
def monthly_comparison():
    conn = db_connection()
    moods = conn.execute("SELECT mood FROM moods ORDER BY date ASC").fetchall()
    conn.close()
    
    if not moods:
        return jsonify({"current": 0, "previous": 0, "change": 0})
    
    score_map = {
        "joy": 5, "surprise": 4, "neutral": 3,
        "fear": 2, "sadness": 1.5, "anger": 1, "disgust": 1
    }
    
    scores = [score_map.get(m["mood"], 3) for m in moods]
    
    overall_avg = sum(scores) / len(scores)
    recent = scores[-7:] if len(scores) >= 7 else scores
    recent_avg = sum(recent) / len(recent) if recent else 0
    
    return jsonify({
        "current": round(recent_avg, 2),
        "previous": round(overall_avg, 2),
        "change": round(recent_avg - overall_avg, 2)
    })

@app.route("/reflection_prompt")
def reflection_prompt():
    conn = db_connection()
    
    # Get latest mood and note
    latest = conn.execute(
        "SELECT mood, note FROM moods ORDER BY id DESC LIMIT 1"
    ).fetchone()
    
    if not latest:
        conn.close()
        # Default prompt for new users
        return jsonify({"prompt": "How are you feeling today?"})
    
    mood = latest["mood"]
    note = latest["note"]
    
    # Get last 3 prompts shown for this mood (progressive tracking)
    used = conn.execute(
        "SELECT prompt FROM prompt_history WHERE mood = ? ORDER BY id DESC LIMIT 3",
        (mood,)
    ).fetchall()
    
    used_prompts = [p["prompt"] for p in used]
    
    # Get templates for this mood
    templates = get_prompt_templates(mood)
    
    # Extract keywords from the note for template filling
    keywords = extract_keywords(note)
    word = random.choice(keywords) if keywords else "this"
    
    # Generate all possible prompts by filling templates
    all_prompts = []
    for template in templates:
        try:
            # Fill the template with the keyword
            prompt = template.format(word=word)
            all_prompts.append(prompt)
        except:
            # If formatting fails, use template as-is
            all_prompts.append(template)
    
    # Filter out recently used prompts
    available = [p for p in all_prompts if p not in used_prompts]
    
    # If all prompts used recently, reset and use all
    if not available:
        available = all_prompts
    
    # Select random prompt
    prompt = random.choice(available)
    
    # Log this prompt
    conn.execute(
        "INSERT INTO prompt_history (mood, prompt, date) VALUES (?, ?, ?)",
        (mood, prompt, datetime.now().strftime("%Y-%m-%d %H:%M"))
    )
    conn.commit()
    conn.close()
    
    return jsonify({"prompt": prompt})

@app.route("/health")
def health():
    return jsonify({"status": "healthy"})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)