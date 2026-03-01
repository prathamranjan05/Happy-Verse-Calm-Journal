let moodChartInstance = null;
let trendChartInstance = null;
let calendarInstance = null;

// ================= HOME NAVIGATION =================
function goToHome() {
    // Update this with your actual home page URL
    window.location.href = "https://happy-verse-calm-journal.onrender.com";
}

// ================= SECTION SWITCHING =================

function showDashboard() {
    document.getElementById("dashboard-section").style.display = "block";
    document.getElementById("analysis-section").style.display = "none";
}

function showAnalysis() {
    document.getElementById("dashboard-section").style.display = "none";
    document.getElementById("analysis-section").style.display = "block";
    loadAnalysis();
}

// ================= SAVE MOOD =================

function saveMood() {
    const note = document.getElementById("note").value;
    if (!note.trim()) return;

    fetch("/add_mood", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ note })
    })
    .then(res => res.json())
    .then(data => {
        // Show mood result if you have that element
        const moodResult = document.getElementById('moodResult');
        const detectedMood = document.getElementById('detectedMood');
        const confidenceFill = document.getElementById('confidenceFill');
        
        if (moodResult && detectedMood && confidenceFill) {
            detectedMood.textContent = data.mood;
            detectedMood.className = `mood-badge mood-${data.mood}`;
            confidenceFill.style.width = `${data.confidence * 100}%`;
            moodResult.style.display = 'flex';
        }
        
        // Load only reflection (AI is removed)
        loadReflection();
        document.getElementById("note").value = "";
    })
    .catch(error => {
        console.error('Error:', error);
        alert('Failed to save mood');
    });
}

// ================= REFLECTION =================

function loadReflection() {
    fetch("/reflection_prompt")
    .then(res => res.json())
    .then(data => {
        document.getElementById("reflectionBox").innerText = data.prompt;
    })
    .catch(error => {
        console.error('Error loading reflection:', error);
        document.getElementById("reflectionBox").innerText = "Take a moment to reflect on your feelings.";
    });
}

// ================= LOAD ANALYSIS =================

function loadAnalysis() {
    fetch("/get_moods")
    .then(res => res.json())
    .then(data => {
        if (data.length > 0) {
            renderCharts(data);
            renderCalendar(data);
        }
    })
    .catch(error => console.error('Error loading moods:', error));

    fetch("/monthly_comparison")
    .then(res => res.json())
    .then(data => {
        document.getElementById("monthlyComparison").innerHTML =
            "Recent Avg: " + data.current +
            "<br>Overall Avg: " + data.previous +
            "<br>Change: " + data.change;
    })
    .catch(error => console.error('Error loading comparison:', error));
}

// ================= CHARTS =================

function renderCharts(data) {

    const counts = {};
    const scoreMap = {
        joy: 5,
        surprise: 4,
        neutral: 3,
        fear: 2,
        sadness: 1.5,
        anger: 1,
        disgust: 1
    };

    const dates = [];
    const scores = [];

    data.forEach(m => {
        counts[m.mood] = (counts[m.mood] || 0) + 1;
        dates.push(m.date);
        scores.push(scoreMap[m.mood] || 3);
    });

    const moodCtx = document.getElementById("moodChart").getContext("2d");
    const trendCtx = document.getElementById("trendChart").getContext("2d");

    if (moodChartInstance) moodChartInstance.destroy();
    moodChartInstance = new Chart(moodCtx, {
        type: "bar",
        data: {
            labels: Object.keys(counts),
            datasets: [{
                label: "Mood Count",
                data: Object.values(counts),
                backgroundColor: "#7bbfae"
            }]
        }
    });

    if (trendChartInstance) trendChartInstance.destroy();
    trendChartInstance = new Chart(trendCtx, {
        type: "line",
        data: {
            labels: dates,
            datasets: [{
                label: "Mood Trend",
                data: scores,
                borderColor: "#7bbfae",
                fill: false
            }]
        }
    });
}

// ================= CALENDAR =================

function renderCalendar(data) {

    const events = data.map(m => ({
        title: m.mood,
        start: m.date
    }));

    if (calendarInstance) calendarInstance.destroy();

    calendarInstance = new FullCalendar.Calendar(
        document.getElementById("calendar"),
        {
            initialView: "dayGridMonth",
            events: events
        }
    );

    calendarInstance.render();
}

// ================= DARK MODE =================

document.addEventListener("DOMContentLoaded", function () {

    const toggleBtn = document.getElementById("themeToggle");

    toggleBtn.addEventListener("click", function () {
        document.body.classList.toggle("dark-mode");
        localStorage.setItem(
            "theme",
            document.body.classList.contains("dark-mode") ? "dark" : "light"
        );
    });

    if (localStorage.getItem("theme") === "dark") {
        document.body.classList.add("dark-mode");
    }

    // Load initial data
    loadReflection();
    showDashboard();
});