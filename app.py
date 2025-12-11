###############################################################
# APP DI NAVIGAZIONE — AirRide (versione corretta + migliorata)
# Flask + TomTom API + SSE
###############################################################

from flask import Flask, request, Response, jsonify
from flask_cors import CORS
import requests, json, math, time, traceback
from datetime import datetime

###############################################################
# FLASK APP
###############################################################

app = Flask(_name_)
CORS(app)

# Memoria temporanea:
active_sessions = {}
current_positions = {}

# Utente fisso per modalità demo
DEMO_USER_ID = "demo"

# TomTom API
API_KEY = "XeNHiK6pLDHE2MYxOyW5bOmv01ZN73oy"

###############################################################
# UTILITY
###############################################################

def distanza_m(lat1, lon1, lat2, lon2):
    R = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)*2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlambda/2)*2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))


def translate_instruction_to_italian(txt: str) -> str:
    """Traduzioni base TomTom → italiano."""
    if not txt:
        return ""

    t = txt.lower()

    replacements = {
        "turn right": "Svolta a destra",
        "turn left": "Svolta a sinistra",
        "keep right": "Mantieni la destra",
        "keep left": "Mantieni la sinistra",
        "go straight": "Prosegui dritto",
        "continue straight": "Continua dritto",
        "u-turn": "Fai inversione",
        "at the roundabout": "Alla rotonda",
        "take the": "Prendi la",
        "exit": "uscita",
    }

    translated = txt
    for eng, ita in replacements.items():
        if eng in t:
            translated = translated.replace(eng, ita)

    return translated


def geocode_address(address: str):
    """Restituisce 'lat,lon' oppure None."""
    try:
        url = f"https://api.tomtom.com/search/2/geocode/{requests.utils.quote(address)}.json"
        params = {"key": API_KEY, "limit": 1}
        r = requests.get(url, params=params, timeout=10)
        data = r.json()

        if data.get("results"):
            pos = data["results"][0]["position"]
            return f"{pos['lat']},{pos['lon']}"
    except:
        pass

    return None


def ensure_coordinates(value: str):
    """
    Se value è "lat,lon" → restituisce la stringa.
    Se è un indirizzo → esegue geocoding.
    """
    try:
        lat, lon = map(float, value.split(","))
        return value  # È già coordinate
    except:
        # Non era lat,lon → geocoding
        coords = geocode_address(value)
        return coords


def get_route_from_tomtom(start: str, end: str):
    """start e end DEVONO essere coordinate 'lat,lon'."""
    try:
        slat, slon = map(float, start.split(","))
        elat, elon = map(float, end.split(","))

        url = f"https://api.tomtom.com/routing/1/calculateRoute/{slat},{slon}:{elat},{elon}/json"
        params = {
            "key": API_KEY,
            "instructionsType": "text",
            "routeType": "fastest",
            "traffic": "false",
            "language": "it-IT"

        }
        r = requests.get(url, params=params, timeout=10)
        if r.status_code != 200:
            print("Errore TomTom:", r.text)
            return None
        
        return r.json()

    except Exception as e:
        print("Errore get_route_from_tomtom:", e)
        return None


def extract_instructions(resp_json):
    """
    Estrae le istruzioni TomTom in modo sicuro.
    Restituisce lista: {text, lat, lon, dist, text_it}
    """
    results = []

    if not resp_json:
        return results

    try:
        route = resp_json.get("routes", [{}])[0]
        legs = route.get("legs", [])

        for leg in legs:
            guidance = leg.get("guidance", {}) or route.get("guidance", {})

            for instr in guidance.get("instructions", []):
                msg = instr.get("message", "")
                lat = instr.get("point", {}).get("latitude")
                lon = instr.get("point", {}).get("longitude")
                dist = instr.get("routeOffsetInMeters", 0)

                results.append({
                    "text": msg,
                    "text_it": translate_instruction_to_italian(msg),
                    "lat": lat,
                    "lon": lon,
                    "dist": dist,
                })

    except Exception as e:
        print("Errore extract_instructions:", e)

    return results


def manovra_to_freccia(text):
    """Converte testo TomTom in codice direzionale."""
    t = (text or "").lower()
    if "right" in t or "destra" in t:
        return 0
    if "left" in t or "sinistra" in t:
        return 1
    if "u-turn" in t or "inversione" in t:
        return 3
    return 2  # dritto default


###############################################################
# GPS UPDATE
###############################################################

@app.route("/update_position", methods=["POST"])
def update_position():
    try:
        data = request.get_json(force=True)
        if not data or "lat" not in data or "lon" not in data:
            return jsonify({"error": "Lat e Lon mancanti"}), 400

        user_id = DEMO_USER_ID
        lat = float(data["lat"])
        lon = float(data["lon"])

        current_positions[user_id] = {
            "lat": lat,
            "lon": lon,
            "time": time.time(),
        }

        # Controllo fuori rotta
        session = active_sessions.get(user_id)
        if session and "polyline" in session:
            if fuori_rotta(lat, lon, session["polyline"]):
                session["recalc_needed"] = True

        return jsonify({"status": "position_updated"})

    except Exception as e:
        print("Errore /update_position:", e)
        return jsonify({"error": str(e)}), 500


###############################################################
# COMPLETAMENTO VIAGGIO
###############################################################

@app.route("/complete_trip", methods=["POST"])
def complete_trip():
    try:
        user_id = DEMO_USER_ID

        session = active_sessions.get(user_id)
        if not session:
            return jsonify({"error": "Nessun viaggio attivo"}), 400

        del active_sessions[user_id]
        return jsonify({"status": "trip_saved"})

    except Exception as e:
        print("Errore /complete_trip:", e)
        return jsonify({"error": str(e)}), 500


###############################################################
# ROUTE INFO
###############################################################

@app.route("/route_info")
def route_info():
    try:
        start = request.args.get("start")
        end = request.args.get("end")

        if not start or not end:
            return jsonify({"error": "Start o end mancanti"}), 400

        start_coords = ensure_coordinates(start)
        end_coords = ensure_coordinates(end)

        if not start_coords or not end_coords:
            return jsonify({"error": "Geocoding fallito"}), 400

        route_data = get_route_from_tomtom(start_coords, end_coords)
        if not route_data:
            return jsonify({"error": "Nessuna rotta trovata"}), 400

        route = route_data["routes"][0]
        summary = route.get("summary", {})
        duration_sec = summary.get("travelTimeInSeconds", 0)
        distance_m = summary.get("lengthInMeters", 0)

        points = []
        for leg in route.get("legs", []):
            for point in leg.get("points", []):
                plat = point.get("latitude")
                plon = point.get("longitude")
                if plat and plon:
                    points.append({"lat": plat, "lon": plon})

        return jsonify({
            "duration": f"{round(duration_sec/60)} min",
            "distance": f"{round(distance_m/1000,1)} km",
            "coordinates": points
        })

    except Exception as e:
        print("Errore /route_info:", e)
        return jsonify({"error": str(e)}), 500


###############################################################
# STREAM ISTRUZIONI (SSE)
###############################################################

@app.route("/stream")
def stream():
    try:
        user_id = DEMO_USER_ID

        start = request.args.get("start")
        end = request.args.get("end")

        if not start or not end:
            return jsonify({"error": "Start o end mancanti"}), 400

        # 🟢 FIX: convertiamo TUTTO in coordinate prima di routing
        start_coords = ensure_coordinates(start)
        end_coords = ensure_coordinates(end)

        if not start_coords or not end_coords:
            return jsonify({"error": "Geocoding fallito"}), 400

        route_data = get_route_from_tomtom(start_coords, end_coords)
        if not route_data:
            return jsonify({"error": "Errore nel calcolo rotta"}), 400

        instructions = extract_instructions(route_data)

        # Polyline
        polyline = []
        for leg in route_data["routes"][0].get("legs", []):
            for p in leg.get("points", []):
                polyline.append({"lat": p["latitude"], "lon": p["longitude"]})

        active_sessions[user_id] = {
            "polyline": polyline,
            "recalc_needed": False
        }

        def generate():
            yield f"data: {json.dumps({'testo': 'Navigazione avviata 🚗', 'fase': 'preview'})}\n\n"

            idx = 0

            while True:
                pos = current_positions.get(user_id)
                session = active_sessions.get(user_id)

                if not pos:
                    yield ": waiting gps\n\n"
                    time.sleep(1)
                    continue

                # 🔄 RICALCOLO SE FUORI ROTTA
                if session.get("recalc_needed"):
                    new_start = f"{pos['lat']},{pos['lon']}"
                    route2 = get_route_from_tomtom(new_start, end_coords)
                    instructions[:] = extract_instructions(route2)

                    new_poly = []
                    for leg in route2["routes"][0]["legs"]:
                        for p in leg["points"]:
                            new_poly.append({"lat": p["latitude"], "lon": p["longitude"]})
                    session["polyline"] = new_poly
                    session["recalc_needed"] = False
                    idx = 0
                    continue

                if idx >= len(instructions):
                    yield f"data: {json.dumps({'testo': 'Percorso completato 🎉', 'fase': 'complete'})}\n\n"
                    time.sleep(2)
                    continue

                instr = instructions[idx]

                d = distanza_m(
                    pos["lat"], pos["lon"],
                    instr["lat"], instr["lon"]
                )

                # Definizione delle fasi
                if d > 120:
                    fase = "preview"
                elif d > 70:
                    fase = "prepare"
                elif d > 25:
                    fase = "near"
                else:
                    fase = "turn"

                # NEXT istruzione
                next_instr = (
                    instructions[idx + 1]
                    if idx + 1 < len(instructions)
                    else None
                )

                payload = {
                    "testo": instr["text_it"],
                    "metri": int(d),
                    "freccia": manovra_to_freccia(instr["text_it"]),
                    "fase": fase,
                    "next": {
                        "testo": next_instr["text_it"],
                        "freccia": manovra_to_freccia(next_instr["text_it"]),
                    } if next_instr else None
                }

                yield f"data: {json.dumps(payload)}\n\n"

                if d < 20:
                    idx += 1

                time.sleep(1)

        return Response(
            generate(),
            mimetype="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "Access-Control-Allow-Origin": "*"
            }
        )
    except Exception as e:
        print("Errore /stream:", e)
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


###############################################################
# FUNZIONI FUORI ROTTA
###############################################################

def distanza_punto_segmento(p, a, b):
    px, py = p
    ax, ay = a
    bx, by = b

    abx, aby = bx - ax, by - ay
    apx, apy = px - ax, py - ay

    ab_len2 = abx*abx + aby*aby
    if ab_len2 == 0:
        return math.dist(p, a)

    t = max(0, min(1, (apx*abx + apy*aby) / ab_len2))
    closest = (ax + t*abx, ay + t*aby)
    return math.dist(p, closest)


def fuori_rotta(user_lat, user_lon, polyline, soglia=30):
    p = (user_lat, user_lon)
    for i in range(len(polyline)-1):
        a = polyline[i]
        b = polyline[i+1]
        d = distanza_punto_segmento(
            p,
            (a["lat"], a["lon"]),
            (b["lat"], b["lon"])
        )
        if d <= soglia:
            return False
    return True


###############################################################
# AVVIO SERVER
###############################################################

if _name_ == "_main_":
    app.run(host="0.0.0.0", port=5000, debug=True)
