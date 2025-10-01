import os
import psycopg2
import datetime
import io
import base64
from flask import Flask, render_template, request, redirect, url_for, Response
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

app = Flask(__name__)

# ---------------- DATABASE ---------------- #
def get_conn():
    return psycopg2.connect(os.environ["DATABASE_URL"], sslmode="require")

def init_db():
    conn = get_conn()
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS logs (
        id SERIAL PRIMARY KEY,
        date TEXT,
        day TEXT,
        exercise TEXT,
        set_number INTEGER,
        reps INTEGER,
        weight REAL
    )''')
    conn.commit()
    conn.close()

init_db()

# ---------------- WORKOUT PLAN ---------------- #
WORKOUT_PLAN = {
    "Push": [
        "Incline Dumbbell Press",
        "Flat Press (Barbell or Machine)",
        "Weighted Dips",
        "Incline Fly (DB or Machine)",
        "EZ Bar Skull Crushers",
        "Overhead Rope Extension",
        "Tricep Pressdowns"
    ],
    "Pull": [
        "Pull-Ups + Negatives",
        "Chest-Supported T-Bar Row",
        "Lat Pulldown (Neutral/Underhand)",
        "Seated Cable Row",
        "Face Pulls",
        "Incline Dumbbell Curl",
        "EZ Bar Preacher Curl",
        "Hammer Curls"
    ],
    "Legs & Shoulders": [
        "Pendulum or Hack Squat",
        "Romanian Deadlift",
        "Leg Extension",
        "Seated/Lying Leg Curl",
        "Standing Calf Raise",
        "Seated Dumbbell Overhead Press",
        "Cable Lateral Raise",
        "Rear Delt Fly",
        "Wide-Grip Upright Row"
    ]
}

# ---------------- HELPERS ---------------- #
def get_training_week():
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT MIN(date) FROM logs")
    first_date = c.fetchone()[0]
    conn.close()

    if first_date:
        start = datetime.datetime.strptime(first_date, "%Y-%m-%d").date()
        today = datetime.date.today()
        return ((today - start).days // 7) + 1
    return 1

def last_set_for_exercise(exercise):
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        SELECT reps, weight FROM logs
        WHERE exercise=%s
        ORDER BY date DESC, set_number DESC LIMIT 1
    """, (exercise,))
    row = c.fetchone()
    conn.close()
    return row

def get_next_progression(exercise):
    week_number = get_training_week()
    last = last_set_for_exercise(exercise)

    if not last:
        return 20.0, 6

    last_reps, last_weight = last

    if week_number % 6 == 0:
        deload_w = round((last_weight * 0.9) / 2.5) * 2.5
        if deload_w < 2.5: deload_w = 2.5
        return deload_w, 6

    if last_reps >= 10:
        return round(last_weight + 2.5, 1), 6
    else:
        return last_weight, last_reps + 1

def list_all_exercises():
    out = []
    for day, exs in WORKOUT_PLAN.items():
        out.extend(exs)
    return list(dict.fromkeys(out))

def fetch_exercise_history(exercise):
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        SELECT date, set_number, reps, weight
        FROM logs
        WHERE exercise=%s
        ORDER BY date ASC, set_number ASC
    """, (exercise,))
    rows = c.fetchall()
    conn.close()
    return rows

def group_by_date(rows):
    by_date = {}
    for d, s, r, w in rows:
        by_date.setdefault(d, []).append((s, r, w))
    return by_date

def epley_1rm(weight, reps):
    return weight * (1 + reps/30.0)

# ---------------- ROUTES ---------------- #
@app.route("/")
def index():
    return render_template("index.html", plan=WORKOUT_PLAN)

@app.route("/weekly/<day>", methods=["GET", "POST"])
def weekly(day):
    if day not in WORKOUT_PLAN:
        return redirect(url_for("index"))

    week_number = get_training_week()

    if request.method == "POST":
        date = datetime.date.today().isoformat()
        for exercise in WORKOUT_PLAN[day]:
            for s in range(1, 4):
                reps_val = request.form.get(f"{exercise}_reps{s}", "").strip()
                weight_val = request.form.get(f"{exercise}_weight{s}", "").strip()
                if reps_val and weight_val:
                    try:
                        reps = int(reps_val)
                        weight = float(weight_val)
                        if reps > 0 and weight > 0:
                            conn = get_conn()
                            c = conn.cursor()
                            c.execute("""INSERT INTO logs
                                (date, day, exercise, set_number, reps, weight)
                                VALUES (%s,%s,%s,%s,%s,%s)""",
                                (date, day, exercise, s, reps, weight))
                            conn.commit()
                            conn.close()
                    except:
                        pass
        return redirect(url_for("weekly", day=day))

    recommendations = {}
    for exercise in WORKOUT_PLAN[day]:
        next_weight, next_reps = get_next_progression(exercise)
        recommendations[exercise] = {"weight": next_weight, "reps": next_reps}

    return render_template(
        "weekly.html",
        day=day,
        exercises=WORKOUT_PLAN[day],
        recommendations=recommendations,
        week_number=week_number
    )

@app.route("/history/<exercise>")
def history(exercise):
    logs = fetch_exercise_history(exercise)
    return render_template("history.html", exercise=exercise, logs=logs)

@app.route("/dashboard")
def dashboard():
    exercises = list_all_exercises()
    selected = request.args.get("exercise", exercises[0] if exercises else "")
    return render_template("dashboard.html", exercises=exercises, selected=selected)

def make_png(fig):
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    b64 = base64.b64encode(buf.read()).decode("ascii")
    return f"data:image/png;base64,{b64}"

@app.route("/chart/<kind>")
def chart(kind):
    exercise = request.args.get("exercise", None)
    if not exercise:
        return Response(status=404)

    rows = fetch_exercise_history(exercise)
    if not rows:
        fig = plt.figure()
        plt.title(f"No data for {exercise}")
        return make_png(fig)

    by_date = group_by_date(rows)
    dates_sorted = sorted(by_date.keys())

    x, y = [], []
    for d in dates_sorted:
        sets = by_date[d]
        if kind == "top_weight":
            y.append(max(w for _, _, w in sets))
        elif kind == "volume":
            y.append(sum(w * r for _, r, w in sets))
        elif kind == "e1rm":
            y.append(max(epley_1rm(w, r) for _, r, w in sets))
        else:
            fig = plt.figure()
            plt.title("Unknown chart")
            return make_png(fig)
        x.append(datetime.datetime.strptime(d, "%Y-%m-%d").date())

    fig = plt.figure()
    plt.plot(x, y, marker="o")
    if kind == "top_weight":
        plt.title(f"{exercise} — Top Set Weight Over Time")
        plt.ylabel("Weight")
    elif kind == "volume":
        plt.title(f"{exercise} — Session Volume")
        plt.ylabel("Volume")
    elif kind == "e1rm":
        plt.title(f"{exercise} — Estimated 1RM (Epley)")
        plt.ylabel("Estimated 1RM")
    plt.xlabel("Date")
    plt.xticks(rotation=45)
    return make_png(fig)

if __name__ == "__main__":
    app.run(debug=True)
